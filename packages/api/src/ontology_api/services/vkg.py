"""R2RML マッピングの登録時の検査(ADR-0046、`P3-01`)。

## 何を検査し、何を検査しないか

**主語の IRI**: この名前空間のデータ接頭辞の下に限る。不変条件5 —
他の名前空間の IRI を主語にできると、**相手が宣言していない事実を
相手の名前で作れる**。

**クラス・述語**: 出所で扱いを分ける。

- **自分の `base_iri` の下**: 承認済み版に実在しなければ拒否。
  実在しない用語に実データを流すと、**その用語で問い合わせる
  エージェントからは何も見えない**(照会は 0 件を返す)
- **他の名前空間の `base_iri` の下**: 拒否(不変条件5)。領域をまたぐ
  対応づけは用語マッピング(SKOS)で宣言する
- **外部語彙**: 検査しない。`rdfs:label` や schema.org への写像が
  正当な主用途である(ADR-0023 決定6 / ADR-0015 決定4 と同じ判断)

**読む関係**: 直近の成功したスキャンで観測していなければ拒否。
観測していない表は名前を打ち間違えても分からず、**Ontop は不正な
マッピングでは起動しない**(実測)ので、登録を通すと**仮想グラフが
丸ごと落ちる**。

**SQL の安全性**: 判定しない(`rr:sqlQuery` を受け付けない)。パーサが
無い。境界は DB ユーザの権限である(`ontology_core.r2rml` の冒頭)。

## 「検査できなかった」を「検査した」と書かない

スキャンの成功した run が無いソースでは、**関係の実在を確かめられない**。
そのとき通してしまうと、「観測済みの表に限る」という表の 1 行が
**嘘になる**。だから拒否して、先にスキャンしてくださいと言う
(不変条件11 と同じ向き — 分からないなら許可しない)。

承認済みの版が無い名前空間では、**自分の語彙の実在を確かめられない**。
ここは拒否ではなく通して、`validated_against_version` に `NULL` を記録する
(ADR-0046 決定12)。**Model より先に Scan が来る**製品なので、版がまだ
無い段階でマッピングを書くのは異常ではない。**`NULL` は「照合先が
無かった」であって「検査に通った」ではない。**
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.scan import ScanRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.db import ScanSourceRow
from ontology_core.deprecation import (
    DeprecationCheckError,
    TargetStatus,
    term_lifecycle,
)
from ontology_core.models import OntologyVersionStatus
from ontology_core.r2rml import R2rmlMapping, data_iri_prefix, parse_mapping

__all__ = ["MappingRejectedError", "ValidatedMapping", "validate_mapping"]


class MappingRejectedError(Exception):
    """マッピングを登録できないことを表す。

    **`R2rmlError`(構文・構成)と分けてある。** あちらはマッピング単体で
    決まる問題、こちらは名前空間・スキャン・承認済み版と突き合わせて
    初めて分かる問題である。どちらも呼び出し元では 422 になるが、
    **原因の層が違うことをログと例外の型で残す。**
    """


@dataclass(frozen=True)
class ValidatedMapping:
    """検査を通ったマッピングと、そのとき照合した版。"""

    mapping: R2rmlMapping
    #: 照合した承認済み版。**`None` は「承認済み版が無かった」**
    #: (ADR-0046 決定12)。「検査していない」ではない。
    validated_against_version: str | None


def _owning_namespace(iri: str, base_iris: list[tuple[str, str]]) -> str | None:
    """IRI が属する名前空間を返す。属さなければ `None`(外部語彙)。

    **最長一致で選ぶ。** `mapping_targets._owning_namespace` と同じ理由で、
    入れ子の `base_iri` があったときに短い方へ吸われてはいけない。
    """
    best: tuple[int, str] | None = None
    for name, base_iri in base_iris:
        if iri.startswith(base_iri) and (best is None or len(base_iri) > best[0]):
            best = (len(base_iri), name)
    return None if best is None else best[1]


async def _current_approved(versions: VersionRepository, namespace: str) -> tuple[str, str] | None:
    """現行の承認済み版の `(version, blob_path)`。無ければ `None`。

    **`approved` だけを見る。** `in-review` の版に実在する用語を根拠に
    マッピングを通すと、その版が却下されたときにマッピングだけが残る。
    """
    for row in await versions.list_for(namespace):
        if row.status is OntologyVersionStatus.APPROVED:
            return row.version, row.blob_path
    return None


async def _observed_tables(session: AsyncSession, *, source_id: int) -> set[str] | None:
    """直近の成功したスキャンで観測した関係名。成功した run が無ければ `None`。

    **`None` と空集合を分ける。** `None` は「観測していない」、空集合は
    「観測したが 1 つも無かった」である。前者で通すと検査したことにならず、
    後者は本当に読める表が無い(どちらも拒否だが理由が違う)。
    """
    scans = ScanRepository(session)
    run = await scans.latest_succeeded_run(source_id=source_id)
    if run is None:
        return None
    catalog = await scans.list_catalog(run_id=run.id)
    return {f"{table.schema_name}.{table.table_name}" for table in catalog}


def _check_subjects(mapping: R2rmlMapping, *, data_prefix: str) -> None:
    """主語の IRI がこの名前空間のデータ接頭辞の下にあることを確かめる。

    Raises:
        MappingRejectedError: 接頭辞の外に出る主語があるとき。
    """
    escaping = sorted(p for p in mapping.subject_prefixes if not p.startswith(data_prefix))
    if escaping:
        raise MappingRejectedError(
            f"主語の IRI は '{data_prefix}' で始まらなければなりません。"
            f"外に出ている接頭辞: {', '.join(escaping)}。"
            "名前空間の外の IRI を主語にすると、宣言していない主張を"
            "他の名前空間の名前で作ることになります"
        )


def _check_foreign_vocabulary(
    mapping: R2rmlMapping, *, namespace: str, base_iris: list[tuple[str, str]]
) -> list[str]:
    """他の名前空間の語彙を使っていないか確かめ、自分の語彙の IRI を返す。

    Returns:
        自分の `base_iri` の下にあるクラス・述語の IRI(実在を確かめる対象)。

    Raises:
        MappingRejectedError: 他の名前空間の語彙を使っているとき。
    """
    own: list[str] = []
    foreign: list[str] = []
    for iri in mapping.asserted_iris:
        owner = _owning_namespace(iri, base_iris)
        if owner is None:
            # **外部語彙は検査しない。** `rdfs:label` などへの写像が主用途
            # である(ADR-0023 決定6 と同じ判断)。
            continue
        if owner == namespace:
            own.append(iri)
        else:
            foreign.append(iri)
    if foreign:
        raise MappingRejectedError(
            "他の名前空間の用語をクラス・述語に使うマッピングは登録できません: "
            f"{', '.join(sorted(set(foreign)))}。"
            "領域をまたぐ対応づけは用語マッピング(SKOS)で宣言してください"
        )
    return sorted(set(own))


def _check_tables(mapping: R2rmlMapping, observed: set[str] | None) -> None:
    """読む関係が観測済みかを確かめる。

    Raises:
        MappingRejectedError: 観測していない関係を読む、またはスキャンが
            一度も成功していないとき。
    """
    if observed is None:
        raise MappingRejectedError(
            "このソースにはまだ成功したスキャンがありません。"
            "先にスキャンしてください — 観測していない表の名前は確かめられず、"
            "打ち間違えたマッピングは Ontop の起動そのものを失敗させます"
        )
    unknown = sorted(table for table in mapping.tables if table not in observed)
    if unknown:
        raise MappingRejectedError(
            f"直近のスキャンで観測していない関係を読もうとしています: {', '.join(unknown)}。"
            "スキーマ名とテーブル名(大文字小文字を含む)を確かめるか、"
            "スキャンをやり直してください"
        )


async def _check_own_vocabulary(
    own_iris: list[str],
    *,
    versions: VersionRepository,
    blob: OntologyBlobStore,
    namespace: str,
) -> str | None:
    """自分の語彙の IRI が承認済み版に実在するかを確かめる。

    Returns:
        照合した版。承認済み版が無ければ `None`。

    Raises:
        MappingRejectedError: 実在しない、廃止済み、または正本を読めないとき。
    """
    current = await _current_approved(versions, namespace)
    if current is None:
        # **拒否しない**(ADR-0046 決定12)。Scan が Model より先に来る製品で、
        # 版が無い段階のマッピングは異常ではない。**照合していないことは
        # `None` として記録に残る。**
        return None
    version, blob_path = current
    if not own_iris:
        # 自分の語彙を 1 つも使っていない(すべて外部語彙)。**照合する対象が
        # 無いだけで、版は読めている。** 版を記録して通す。
        return version

    try:
        turtle = await blob.get_version(blob_path)
        lifecycles = term_lifecycle(turtle, own_iris)
    except (BlobStoreError, DeprecationCheckError) as exc:
        # **「読めなかった」を「実在する」にしない。**
        raise MappingRejectedError(
            f"承認済み版 '{version}' の正本を読めなかったため、用語の実在を"
            f"確かめられませんでした: {exc}"
        ) from exc

    absent = sorted(iri for iri, life in lifecycles.items() if life.status is TargetStatus.ABSENT)
    if absent:
        raise MappingRejectedError(
            f"承認済み版 '{version}' に存在しない用語を使っています: {', '.join(absent)}。"
            "実データを流しても、その用語で問い合わせるエージェントからは"
            "何も見えません"
        )

    deprecated = sorted(
        (iri, life.successor)
        for iri, life in lifecycles.items()
        if life.status is TargetStatus.DEPRECATED
    )
    if deprecated:
        detail = ", ".join(
            iri if successor is None else f"{iri}(後継: {successor})"
            for iri, successor in deprecated
        )
        raise MappingRejectedError(
            f"承認済み版 '{version}' で廃止された用語を使っています: {detail}。"
            "廃止した用語に新しく実データを流すと、廃止の宣言が意味を失います"
        )
    return version


async def validate_mapping(
    session: AsyncSession,
    *,
    blob: OntologyBlobStore,
    namespace: str,
    source: ScanSourceRow,
    content: str,
) -> ValidatedMapping:
    """R2RML マッピングを登録できるか検査する(ADR-0046 決定5・6・12)。

    **保存する前に呼ぶ。** 検査を通らないマッピングを保存すると、
    仮想グラフが起動しない改訂が有効な改訂として残る(実測: Ontop は
    参照する関係が無いと**起動に失敗する**)。

    Raises:
        R2rmlError: マッピング単体として成立していないとき。
        MappingRejectedError: 名前空間・スキャン・承認済み版と
            突き合わせて成立しないとき。
    """
    mapping = parse_mapping(content)

    namespaces = await NamespaceRepository(session).list_all()
    base_iris = [(ns.name, ns.base_iri) for ns in namespaces]
    own = next((ns for ns in namespaces if ns.name == namespace), None)
    if own is None:
        # 呼び出し元が存在を確かめているのでここには来ない。
        raise MappingRejectedError(f"名前空間 '{namespace}' が見つかりません")

    _check_subjects(mapping, data_prefix=data_iri_prefix(own.base_iri))
    own_iris = _check_foreign_vocabulary(mapping, namespace=namespace, base_iris=base_iris)
    _check_tables(mapping, await _observed_tables(session, source_id=source.id))
    version = await _check_own_vocabulary(
        own_iris, versions=VersionRepository(session), blob=blob, namespace=namespace
    )
    return ValidatedMapping(mapping=mapping, validated_against_version=version)
