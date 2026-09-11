"""マッピングの先の用語の生死を解決する(ADR-0030、`P2B-18`)。

[ADR-0023](../../../../../docs/adr/0023-cross-domain-mappings.md) が受け入れた
コスト「マッピングの先が廃止されても気づけない」を埋める。放置すると、
エージェントが**廃止された用語を根拠に回答を作る** — ADR-0017 決定3 が
`sparql_query` に対して塞いだのと同じ穴が、マッピングの経路に開いていた。

## 読めない名前空間の状態を推測しない

**相手の名前空間に `data-analyst` が無ければ `unknown` を返す**
(ADR-0030 決定1)。`active` とは言わない。

不変条件11(権限の既定は拒否)の素直な適用であり、同時に
**「調べられなかった」を「問題なし」と書かない**という、このリポジトリが
繰り返している判断でもある。

**`active` と `absent` を区別するので、権限で切らないと存在の oracle になる。**
IRI を当て推量で `declare` して読み返せば相手の語彙を 1 件ずつ探れてしまう。
区別を捨てれば oracle は狭まるが、今度は決定2 が禁じた丸め
(`absent` を `active` に混ぜる)になる。**権限で切るのが、どちらも
犠牲にしない唯一の線である。**

## 名前空間ごとに 1 回だけ読む

コストを決めるのは**マッピングの件数ではなく対象の名前空間の数**である。
同じ名前空間を指すマッピングが 100 件あっても、TTL の読み込みと rdflib の
解析は 1 回で済む。

そのうえで名前空間の数に上限を置く(`MAX_TARGET_NAMESPACES`)。マッピングは
任意の IRI を指せるので**対象の数に上界が無い**。超えた分は `unknown` +
理由「上限」で返す — **黙って `active` にしない**(ADR-0016 決定5 と同じ形)。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_api.services.authorization import effective_role
from ontology_core.auth.entra import Principal
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.deprecation import (
    DeprecationCheckError,
    TargetStatus,
    TermLifecycle,
    term_lifecycle,
)
from ontology_core.models import NamespaceRole, OntologyVersionStatus, TermMapping

__all__ = ["MAX_TARGET_NAMESPACES", "resolve_target_lifecycles"]

#: 1 回の応答で TTL を読む名前空間の数の上限(ADR-0030 決定4)。
#:
#: **上界が無いものに上限を置く。** マッピングは外部語彙も含めて任意の IRI を
#: 指せるので、対象の名前空間の数に構造的な上界が無い。50 個の名前空間の TTL を
#: Blob から読んで解析すると、一覧そのものが使えなくなる。
MAX_TARGET_NAMESPACES = 10

_EXTERNAL = "このシステムの名前空間ではありません(外部語彙)。生死は追えません"
_NO_PERMISSION = (
    "その名前空間を読む権限(data-analyst)がないため調べていません。"
    "**生きているという意味ではありません**"
)
_NO_APPROVED = "その名前空間にまだ承認済みの版がありません"
_LIMIT = (
    f"1 回の応答で調べる名前空間の数の上限({MAX_TARGET_NAMESPACES})に達したため"
    "調べていません。`direction` や名前空間を絞って問い直してください"
)


def _unknown(note: str) -> TermLifecycle:
    return TermLifecycle(status=TargetStatus.UNKNOWN, note=note)


def _owning_namespace(iri: str, base_iris: Sequence[tuple[str, str]]) -> str | None:
    """IRI が属する名前空間を返す。属さなければ `None`。

    **最長一致で選ぶ。** `base_iri` が入れ子になっている名前空間
    (`https://e.example/#` と `https://e.example/sub#`)があったとき、
    短い方に吸われると別の名前空間の用語として扱ってしまう。
    """
    best: tuple[int, str] | None = None
    for name, base_iri in base_iris:
        if iri.startswith(base_iri) and (best is None or len(base_iri) > best[0]):
            best = (len(base_iri), name)
    return None if best is None else best[1]


async def resolve_target_lifecycles(
    session: AsyncSession,
    *,
    blob: OntologyBlobStore,
    principal: Principal,
    mappings: Sequence[TermMapping],
) -> dict[str, TermLifecycle]:
    """マッピングの先の IRI ごとに生死を返す(ADR-0030)。

    **返る辞書には対象の全 IRI が入る。** 調べられなかったものも
    `unknown` + 理由として入る — 欠落させると、呼び出し側が
    「載っていないものは問題なし」と読む。

    Args:
        mappings: 状態を知りたいマッピング。`target_term` だけを見る。

    Returns:
        `target_term` から `TermLifecycle` への辞書。
    """
    targets = sorted({mapping.target_term for mapping in mappings})
    if not targets:
        return {}

    namespaces = await NamespaceRepository(session).list_all()
    base_iris = [(ns.name, ns.base_iri) for ns in namespaces]

    # 対象を名前空間ごとにまとめる。**件数ではなく名前空間の数がコストを決める。**
    by_namespace: dict[str, list[str]] = {}
    result: dict[str, TermLifecycle] = {}
    for iri in targets:
        owner = _owning_namespace(iri, base_iris)
        if owner is None:
            # **`absent` にしない。** SKOS の用語は「存在しない」のではなく
            # 「このシステムの管理外」である(ADR-0023 決定6 の主用途)。
            result[iri] = _unknown(_EXTERNAL)
            continue
        by_namespace.setdefault(owner, []).append(iri)

    versions = VersionRepository(session)
    # 名前空間名で並べて上限を当てる。**同じ入力なら同じ結果になるようにする** —
    # 辞書の挿入順に依存すると、どれが `unknown` になるかが実行ごとに変わる。
    for index, name in enumerate(sorted(by_namespace)):
        iris = by_namespace[name]
        if index >= MAX_TARGET_NAMESPACES:
            for iri in iris:
                result[iri] = _unknown(_LIMIT)
            continue

        # **権限が無ければ調べない**(決定1)。`active` とは言わない。
        role = await effective_role(session, namespace=name, principal=principal)
        if role is None or not role.covers(NamespaceRole.DATA_ANALYST):
            for iri in iris:
                result[iri] = _unknown(_NO_PERMISSION)
            continue

        current = await _current_approved(versions, name)
        if current is None:
            for iri in iris:
                result[iri] = _unknown(_NO_APPROVED)
            continue

        try:
            turtle = await blob.get_version(current)
            found = term_lifecycle(turtle, iris)
        except (BlobStoreError, DeprecationCheckError) as exc:
            # **「読めなかった」を「生きている」にしない。**
            for iri in iris:
                result[iri] = _unknown(f"正本を読めませんでした: {exc}")
            continue
        result.update(found)

    return result


async def _current_approved(versions: VersionRepository, namespace: str) -> str | None:
    """その名前空間の現行の承認済み版の Blob パス。無ければ `None`。

    **`approved` だけを見る。** `in-review` の版で生死を判定すると、
    まだ承認されていない廃止を「廃止済み」として他の名前空間へ伝えてしまう。
    """
    for row in await versions.list_for(namespace):
        if row.status is OntologyVersionStatus.APPROVED:
            return row.blob_path
    return None
