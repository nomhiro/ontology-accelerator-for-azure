"""マッピングの先の用語の生死(ADR-0030、`P2B-18`)。

ADR-0023 が受け入れたコスト「マッピングの先が廃止されても気づけない」を
埋めた。放置すると**エージェントが廃止された用語を根拠に回答を作る** —
ADR-0017 決定3 が `sparql_query` に対して塞いだのと同じ穴である。

ここで固定するのは 4 つである。

1. **4 状態を混ぜない**(決定2)。特に `unknown` は「問題なし」ではない
2. **読む権限が無ければ `unknown`**(決定1)。`active` とは言わない
3. **廃止する側にも見せる**(決定5)。ただし**ブロックしない**(決定6)
4. **宣言のときには検査しない**(決定3)
5. **健全性指標に出る**([ADR-0037](../../../docs/adr/0037-deprecated-target-metric.md)、
   `P2B-21`)。**廃止の件数と「調べられなかった」件数は 2 つで 1 組**であり、
   **呼び出し元の権限で数える**(指標のために権限ゲートを外すと存在の
   oracle が開く)

営業(`mt-sales`)と経理(`mt-finance`)の「優良顧客」を題材にする。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.mappings import MappingRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.routers.mappings import (
    MappingDeclare,
    MappingDirection,
    declare_mapping,
    list_mappings,
)
from ontology_api.services import mapping_targets
from ontology_api.services.projection import ProjectionService
from ontology_core.auth.entra import Principal
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.deprecation import ProblemKind, TargetStatus
from ontology_core.models import NamespaceRole, PlatformRole, TermMapping
from ontology_core.sparql.client import SparqlStore

_SALES = "mt-sales"
_FINANCE = "mt-finance"
_SALES_IRI = "https://e.example/sales#"
_FINANCE_IRI = "https://e.example/finance#"

_SALES_TERM = _SALES_IRI + "GoldCustomer"
_LIVE = _FINANCE_IRI + "PremiumCustomer"
_GONE = _FINANCE_IRI + "RetiredCustomer"
_MISSING = _FINANCE_IRI + "NeverExisted"
_SUCCESSOR = _FINANCE_IRI + "KeyAccount"
_EXTERNAL = "http://www.w3.org/2004/02/skos/core#Concept"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_SALES_OWNER = Principal(subject="s-owner", object_id="s-owner-oid")
#: 経理を読む権限を**持たない**営業の分析者。決定1 の検証に使う。
_SALES_ANALYST = Principal(subject="s-analyst", object_id="s-analyst-oid")
#: 経理も読める営業の責任者。**領域をまたぐ関係には相手の読み取り権限が要る。**
_BOTH = Principal(subject="both", object_id="both-oid")
_FINANCE_OWNER = Principal(subject="f-owner", object_id="f-owner-oid")

_HEAD = (
    f"@prefix f: <{_FINANCE_IRI}> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "@prefix dcterms: <http://purl.org/dc/terms/> .\n"
)
#: 経理の第 1 版。`RetiredCustomer` はまだ生きている。
_FINANCE_V1 = _HEAD + (
    'f:PremiumCustomer a owl:Class ; rdfs:label "優良顧客" .\n'
    'f:RetiredCustomer a owl:Class ; rdfs:label "旧・優良顧客" .\n'
    'f:KeyAccount a owl:Class ; rdfs:label "重要顧客" .\n'
)
#: 経理の第 2 版。`RetiredCustomer` を**削除ではなく廃止**して後継を書く。
_FINANCE_V2 = _HEAD + (
    'f:PremiumCustomer a owl:Class ; rdfs:label "優良顧客" .\n'
    'f:RetiredCustomer a owl:Class ; rdfs:label "旧・優良顧客" ;\n'
    "    owl:deprecated true ; dcterms:isReplacedBy f:KeyAccount .\n"
    'f:KeyAccount a owl:Class ; rdfs:label "重要顧客" .\n'
)


class _NullStore(SparqlStore):
    """射影を行わない代役。**生死の判定は正本の TTL を見る。**"""

    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
        return {"results": {"bindings": []}}

    async def construct(self, sparql: str, *, dataset: str) -> str:
        return ""

    async def update(self, sparql: str, *, dataset: str) -> None: ...
    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None: ...
    async def put_default_graph(self, turtle: str, *, dataset: str) -> None: ...
    async def delete_graph(self, graph_iri: str, *, dataset: str) -> None: ...
    async def list_graphs(self, dataset: str) -> list[str]:
        return []

    async def has_default_graph_content(self, dataset: str) -> bool:
        return True

    async def list_datasets(self) -> list[str]:
        return []

    async def create_dataset(self, dataset: str) -> None: ...
    async def delete_dataset(self, dataset: str) -> None: ...


async def _setup(session: AsyncSession) -> None:
    repo = NamespaceRepository(session)
    roles = RoleRepository(session)
    for name, base_iri, grants in (
        (
            _SALES,
            _SALES_IRI,
            (
                (_SALES_OWNER, NamespaceRole.OWNER),
                (_SALES_ANALYST, NamespaceRole.DATA_ANALYST),
                (_BOTH, NamespaceRole.OWNER),
            ),
        ),
        (
            _FINANCE,
            _FINANCE_IRI,
            (
                (_FINANCE_OWNER, NamespaceRole.OWNER),
                # **営業の分析者には付与しない。** 決定1 の検証に使う。
                (_BOTH, NamespaceRole.DATA_ANALYST),
            ),
        ),
    ):
        await repo.create(
            name=name,
            display_name=name,
            description="",
            base_iri=base_iri,
            created_by=_ADMIN.object_id,
            require_two_person_approval=False,
        )
        for principal, role in grants:
            await roles.grant(
                namespace=name,
                principal_id=principal.object_id,
                role=role,
                granted_by=_ADMIN.object_id,
            )
    await session.commit()


def _service(session: AsyncSession, blob: OntologyBlobStore) -> ProjectionService:
    return ProjectionService(
        session=session, blob=blob, store=_NullStore(), graph_iri_base="urn:ontology:graph"
    )


async def _approve_finance(
    session: AsyncSession, blob: OntologyBlobStore, *, turtle: str, version: str
) -> None:
    svc = _service(session, blob)
    await svc.publish(
        namespace=_FINANCE, turtle=turtle, actor=_FINANCE_OWNER.object_id, version=version
    )
    await svc.submit(namespace=_FINANCE, version=version, actor=_FINANCE_OWNER.object_id)
    await svc.approve(namespace=_FINANCE, version=version, actor=_FINANCE_OWNER.object_id)
    await session.commit()


async def _declare(session: AsyncSession, *, target: str, source: str = _SALES_TERM) -> None:
    await declare_mapping(
        namespace=_SALES,
        payload=MappingDeclare(
            source_term=source,
            target_term=target,
            predicate="closeMatch",
            reason="同じ顧客区分を指している",
        ),
        principal=_SALES_OWNER,
        session=session,
    )
    await session.commit()


async def _outgoing(
    session: AsyncSession, blob: OntologyBlobStore, *, principal: Principal = _BOTH
) -> dict[str, TermMapping]:
    found = await list_mappings(namespace=_SALES, principal=principal, session=session, blob=blob)
    return {mapping.target_term: mapping for mapping in found}


# ------------------------------------------------------------ 4 つの状態


@pytest.mark.integration
async def test_廃止された先は_deprecated_と後継を返す(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**これがこの機能の目的である。**

    経理が正しく縮めた(削除ではなく廃止 + 後継)結果を、営業が見られる。
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V1, version="1.0.0")
    await _declare(session, target=_GONE)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="2.0.0")

    found = await _outgoing(session, blob_store)
    assert found[_GONE].target_status == TargetStatus.DEPRECATED.value
    assert found[_GONE].target_successor == _SUCCESSOR
    assert found[_GONE].target_status_note == ""


@pytest.mark.integration
async def test_生きている先は_active(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="1.0.0")
    await _declare(session, target=_LIVE)

    found = await _outgoing(session, blob_store)
    assert found[_LIVE].target_status == TargetStatus.ACTIVE.value
    assert found[_LIVE].target_successor is None


@pytest.mark.integration
async def test_相手に存在しない先は_absent(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`active` にも `unknown` にも丸めない**(ADR-0030 決定2)。

    「何も指していないマッピング」は「廃止された先を指すマッピング」と
    同じくらい直すべきものである。そして**相手の現行版を読んだ結果として
    「無い」と分かったことは、調べていないことと違う。**
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="1.0.0")
    await _declare(session, target=_MISSING)

    found = await _outgoing(session, blob_store)
    assert found[_MISSING].target_status == TargetStatus.ABSENT.value


@pytest.mark.integration
async def test_外部語彙は_unknown_で理由が付く(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`absent` にしない**(決定2)。

    SKOS の用語はこのシステムのどの名前空間にも無いが、それは
    「存在しない」ではなく「**このシステムの管理外**」である
    (ADR-0023 決定6 が実在を検査しないと決めた主用途)。
    """
    await _setup(session)
    await _declare(session, target=_EXTERNAL)

    found = await _outgoing(session, blob_store)
    assert found[_EXTERNAL].target_status == TargetStatus.UNKNOWN.value
    assert "外部語彙" in found[_EXTERNAL].target_status_note


@pytest.mark.integration
async def test_承認済みの版が無ければ_unknown(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`absent` にしない。** 相手にまだ承認済み版が無いだけである。"""
    await _setup(session)
    await _declare(session, target=_LIVE)

    found = await _outgoing(session, blob_store)
    assert found[_LIVE].target_status == TargetStatus.UNKNOWN.value
    assert "承認済みの版" in found[_LIVE].target_status_note


# ------------------------------------------------------------ 権限


@pytest.mark.integration
async def test_相手を読めない主体には_unknown_を返す(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`active` とは言わない**(ADR-0030 決定1)。

    不変条件11(権限の既定は拒否)の素直な適用であり、同時に
    「調べられなかった」を「問題なし」と書かないという判断でもある。

    **`active` と `absent` を区別するので、権限で切らないと存在の oracle に
    なる** — IRI を当て推量で `declare` して読み返せば相手の語彙を 1 件ずつ
    探れてしまう。
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="1.0.0")
    await _declare(session, target=_LIVE)

    # 経理を読める主体には状態が見える。
    visible = await _outgoing(session, blob_store, principal=_BOTH)
    assert visible[_LIVE].target_status == TargetStatus.ACTIVE.value

    # 読めない主体には見えない。**そして「生きている」とも言わない。**
    hidden = await _outgoing(session, blob_store, principal=_SALES_ANALYST)
    assert hidden[_LIVE].target_status == TargetStatus.UNKNOWN.value
    assert "権限" in hidden[_LIVE].target_status_note
    assert "生きているという意味ではありません" in hidden[_LIVE].target_status_note


@pytest.mark.integration
async def test_incoming_では自分の用語なので権限の問題が起きない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """指されている側は**自分のデータ**を見るので、常に状態が分かる。"""
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V1, version="1.0.0")
    await _declare(session, target=_GONE)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="2.0.0")

    incoming = await list_mappings(
        namespace=_FINANCE,
        principal=_FINANCE_OWNER,
        session=session,
        blob=blob_store,
        direction=MappingDirection.INCOMING,
    )
    assert [m.target_status for m in incoming] == [TargetStatus.DEPRECATED.value]


# ------------------------------------------------------------ 上限


@pytest.mark.integration
async def test_名前空間の数の上限を超えたら_unknown_で理由を返す(
    session: AsyncSession, blob_store: OntologyBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**黙って `active` にしない**(ADR-0030 決定4)。

    マッピングは任意の IRI を指せるので**対象の名前空間の数に上界が無い**。
    上限に達した分は理由付きの `unknown` で返す。
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="1.0.0")
    await _declare(session, target=_LIVE)
    monkeypatch.setattr(mapping_targets, "MAX_TARGET_NAMESPACES", 0)

    found = await _outgoing(session, blob_store)
    assert found[_LIVE].target_status == TargetStatus.UNKNOWN.value
    assert "上限" in found[_LIVE].target_status_note


# ------------------------------------------------- 宣言のときは検査しない


@pytest.mark.integration
async def test_廃止済みの先へも宣言できる(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**宣言は検査しない**(ADR-0030 決定3、ADR-0023 決定6)。

    検査を入れると宣言が Blob と他の名前空間の可用性に依存する
    (不変条件2・3)。そして**肝心の事象(宣言の後に廃止される)には効かない**。

    さらに ADR-0017 決定2 のとおり、**旧用語を指すマッピングは正当にありうる**
    (旧→新の移行のため)。
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V1, version="1.0.0")
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="2.0.0")
    # 既に廃止済みの用語へ宣言する。**例外にならない。**
    await _declare(session, target=_GONE)

    found = await _outgoing(session, blob_store)
    assert found[_GONE].target_status == TargetStatus.DEPRECATED.value


# ----------------------------------------------- 廃止する側への報告


@pytest.mark.integration
async def test_廃止する側に依存が報告される(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**両側から見える**(ADR-0030 決定5)。

    指されている側には「他の名前空間がこの用語を指している」が見える。
    `term_mappings` は PostgreSQL にあるので Blob も推論器も要らず、
    **自分のデータに対する `incoming` なので権限の論点も生じない。**
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V1, version="1.0.0")
    await _declare(session, target=_GONE)

    svc = _service(session, blob_store)
    await svc.publish(
        namespace=_FINANCE, turtle=_FINANCE_V2, actor=_FINANCE_OWNER.object_id, version="2.0.0"
    )
    await session.commit()
    base = await svc.current_approved(namespace=_FINANCE, excluding="2.0.0")
    problems = await svc.check_deprecation_lifecycle(namespace=_FINANCE, version="2.0.0", base=base)

    mapped = [p for p in problems if p.kind is ProblemKind.MAPPED_BY_OTHERS]
    assert len(mapped) == 1
    assert mapped[0].term == _GONE
    assert _SALES in mapped[0].message
    # **ブロックしない**(決定6)。
    assert mapped[0].blocking is False


@pytest.mark.integration
async def test_依存があっても承認は止まらない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`N` が `M` を人質に取れてはいけない**(ADR-0030 決定6)。

    マッピングを張るだけで相手の廃止を封じられるなら、張られた側には
    従う手段が無い(逆向きの書き込み口が無いので相手の行を消せない)。
    ADR-0017 決定2 の「ブロックが妥当なのは従う正当な手段が常にあるときだけ」
    にそのまま反する。
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V1, version="1.0.0")
    await _declare(session, target=_GONE)
    # 廃止を含む版が**承認できる**。
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="2.0.0")

    from ontology_api.repositories.versions import VersionRepository
    from ontology_core.models import OntologyVersionStatus

    row = await VersionRepository(session).get(_FINANCE, "2.0.0")
    assert row is not None
    assert row.status is OntologyVersionStatus.APPROVED


@pytest.mark.integration
async def test_誰も指していない廃止は報告に出ない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """報告が雑音にならないようにする。"""
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V1, version="1.0.0")

    svc = _service(session, blob_store)
    await svc.publish(
        namespace=_FINANCE, turtle=_FINANCE_V2, actor=_FINANCE_OWNER.object_id, version="2.0.0"
    )
    await session.commit()
    base = await svc.current_approved(namespace=_FINANCE, excluding="2.0.0")
    problems = await svc.check_deprecation_lifecycle(namespace=_FINANCE, version="2.0.0", base=base)
    assert [p for p in problems if p.kind is ProblemKind.MAPPED_BY_OTHERS] == []


# ------------------------------------------------------------ 読み出し


@pytest.mark.integration
async def test_指定した用語へのマッピングを_1_クエリで引く(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**用語ごとに引かない。** 廃止した用語の数だけクエリが飛ぶ形にしない。"""
    await _setup(session)
    await _declare(session, target=_GONE)
    await _declare(session, target=_LIVE, source=_SALES_IRI + "Other")

    found = await MappingRepository(session).incoming_targets({_GONE, _LIVE, _MISSING})
    assert found[_GONE] == [(_SALES, _SALES_TERM)]
    assert found[_LIVE] == [(_SALES, _SALES_IRI + "Other")]
    assert _MISSING not in found


@pytest.mark.integration
async def test_空の集合では問い合わせない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    assert await MappingRepository(session).incoming_targets(set()) == {}


@pytest.mark.integration
async def test_マッピングが無ければ空の辞書(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    assert (
        await mapping_targets.resolve_target_lifecycles(
            session, blob=blob_store, principal=_BOTH, mappings=[]
        )
        == {}
    )


@pytest.mark.integration
async def test_未承認の版で生死を判定しない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`approved` だけを見る**(`_current_approved`)。

    `draft` や `in-review` の版で判定すると、**まだ承認されていない廃止を
    「廃止済み」として他の名前空間へ伝えてしまう。** 承認前の作業中の状態が
    領域をまたいで漏れる形でもある。
    """
    await _setup(session)
    await _declare(session, target=_LIVE)
    # publish だけして submit も approve もしない。
    await _service(session, blob_store).publish(
        namespace=_FINANCE, turtle=_FINANCE_V2, actor=_FINANCE_OWNER.object_id, version="1.0.0"
    )
    await session.commit()

    found = await _outgoing(session, blob_store)
    assert found[_LIVE].target_status == TargetStatus.UNKNOWN.value
    assert "承認済みの版" in found[_LIVE].target_status_note


@pytest.mark.integration
async def test_入れ子の_base_iri_は長い方に割り当てる(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**最長一致で選ぶ**(`_owning_namespace`)。

    `base_iri` が入れ子になっている名前空間があったとき、短い方に吸われると
    **別の名前空間の用語として扱ってしまう** — 権限の判定も TTL の読み先も
    間違える。
    """
    inner_iri = _FINANCE_IRI + "sub/"
    inner_term = inner_iri + "Widget"
    await _setup(session)
    await NamespaceRepository(session).create(
        name="mt-inner",
        display_name="mt-inner",
        description="",
        base_iri=inner_iri,
        created_by=_ADMIN.object_id,
        require_two_person_approval=False,
    )
    await session.commit()
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="1.0.0")
    await _declare(session, target=inner_term)

    # 内側の名前空間には `_BOTH` の付与が無いので**権限が無い**と出る。
    # 外側(経理)に吸われていたら、経理を読める `_BOTH` には `absent` が出る。
    found = await _outgoing(session, blob_store)
    assert found[inner_term].target_status == TargetStatus.UNKNOWN.value
    assert "権限" in found[inner_term].target_status_note, (
        "外側の名前空間に吸われている(最長一致になっていない)"
    )


@pytest.mark.integration
async def test_生きている用語への依存は報告しない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**報告するのは廃止する用語への依存だけである。**

    全部の用語について報告すると、依存があるだけで毎回警告が並び、
    肝心の廃止の報告が埋もれる。
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V1, version="1.0.0")
    await _declare(session, target=_LIVE)

    svc = _service(session, blob_store)
    await svc.publish(
        namespace=_FINANCE, turtle=_FINANCE_V2, actor=_FINANCE_OWNER.object_id, version="2.0.0"
    )
    await session.commit()
    base = await svc.current_approved(namespace=_FINANCE, excluding="2.0.0")
    problems = await svc.check_deprecation_lifecycle(namespace=_FINANCE, version="2.0.0", base=base)
    mapped = [p for p in problems if p.kind is ProblemKind.MAPPED_BY_OTHERS]
    assert mapped == [], "廃止していない用語への依存を報告している"


# --------------------------------------------- Turtle での書き出し(P2B-17)


@pytest.mark.integration
async def test_Turtle_で書き出せる(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    """**射影しないが、標準の RDF としては取り出せる**(ADR-0031 決定2)。

    素の SKOS トリプルと、終点の生死を載せた記述ノードの両方が出る。
    """
    from rdflib import Graph, Literal, URIRef
    from rdflib.namespace import SKOS

    from ontology_api.routers.mappings import export_mappings
    from ontology_core.mapping import MAPPING_ONT_NAMESPACE, mapping_node_iri

    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V1, version="1.0.0")
    await _declare(session, target=_GONE)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="2.0.0")

    response = await export_mappings(
        namespace=_SALES, principal=_BOTH, session=session, blob=blob_store
    )
    assert response.media_type is not None
    assert response.media_type.startswith("text/turtle")
    graph = Graph()
    graph.parse(data=bytes(response.body).decode("utf-8"), format="turtle")

    # 素の SKOS トリプル。
    assert (URIRef(_SALES_TERM), SKOS.closeMatch, URIRef(_GONE)) in graph
    # 終点の生死は記述ノードに載る。
    node = URIRef(mapping_node_iri(_SALES, _SALES_TERM, _GONE))
    assert (node, URIRef(MAPPING_ONT_NAMESPACE + "targetStatus"), Literal("deprecated")) in graph
    assert (
        node,
        URIRef(MAPPING_ONT_NAMESPACE + "targetSuccessor"),
        URIRef(_SUCCESSOR),
    ) in graph


@pytest.mark.integration
async def test_書き出しでも権限の無い相手は_unknown(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**JSON と Turtle で見える情報を変えない**(ADR-0031 決定2 の共有経路)。

    片方だけ生死が付く状態を作ると、表現によって見える範囲が変わる
    (ADR-0026 決定1 が避けた形である)。
    """
    from rdflib import Graph, Literal, URIRef
    from rdflib.namespace import RDF

    from ontology_api.routers.mappings import export_mappings
    from ontology_core.mapping import MAPPING_ONT_NAMESPACE

    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="1.0.0")
    await _declare(session, target=_LIVE)

    response = await export_mappings(
        namespace=_SALES, principal=_SALES_ANALYST, session=session, blob=blob_store
    )
    graph = Graph()
    graph.parse(data=bytes(response.body).decode("utf-8"), format="turtle")
    nodes = list(graph.subjects(RDF.type, URIRef(MAPPING_ONT_NAMESPACE + "Mapping")))
    assert len(nodes) == 1
    assert (
        nodes[0],
        URIRef(MAPPING_ONT_NAMESPACE + "targetStatus"),
        Literal("unknown"),
    ) in graph


@pytest.mark.integration
async def test_書き出しにも権限が必要(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    from fastapi import HTTPException

    from ontology_api.routers.mappings import export_mappings

    await _setup(session)
    stranger = Principal(subject="x", object_id="x-oid")
    with pytest.raises(HTTPException) as exc:
        await export_mappings(
            namespace=_SALES, principal=stranger, session=session, blob=blob_store
        )
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_incoming_も書き出せる(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    from rdflib import Graph, URIRef
    from rdflib.namespace import SKOS

    from ontology_api.routers.mappings import export_mappings

    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="1.0.0")
    await _declare(session, target=_LIVE)

    response = await export_mappings(
        namespace=_FINANCE,
        principal=_FINANCE_OWNER,
        session=session,
        blob=blob_store,
        direction=MappingDirection.INCOMING,
    )
    graph = Graph()
    graph.parse(data=bytes(response.body).decode("utf-8"), format="turtle")
    assert (URIRef(_SALES_TERM), SKOS.closeMatch, URIRef(_LIVE)) in graph


@pytest.mark.integration
async def test_Accept_で_JSON_LD_でも書き出せる(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**RDF を返す口はすべて同じ規則で交渉する**
    ([ADR-0036](../../../docs/adr/0036-jsonld-serialization.md) 決定8、`P2A-17`)。

    片方だけ交渉可能にすると「なぜマッピングは JSON-LD で取れないのか」に
    技術的な答えが無い(どちらも同じ `Graph` である)。

    **2 つの表現が同型であることをここで固定する**(決定6)。`exportedAt` は
    呼ぶたびに変わるので比べる前に外す。
    """
    import json

    from rdflib import Graph, URIRef
    from rdflib.compare import isomorphic

    from ontology_api.routers.mappings import export_mappings
    from ontology_core.jsonld import JSONLD_MEDIA_TYPE, MAPPING_CONTEXT
    from ontology_core.mapping import MAPPING_ONT_NAMESPACE

    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="1.0.0")
    await _declare(session, target=_LIVE)

    turtle_response = await export_mappings(
        namespace=_SALES, principal=_BOTH, session=session, blob=blob_store
    )
    jsonld_response = await export_mappings(
        namespace=_SALES,
        principal=_BOTH,
        session=session,
        blob=blob_store,
        accept=JSONLD_MEDIA_TYPE,
    )
    assert turtle_response.media_type is not None
    assert turtle_response.media_type.startswith("text/turtle")
    assert jsonld_response.media_type == JSONLD_MEDIA_TYPE

    body = bytes(jsonld_response.body).decode("utf-8")
    # **コンテキストを取り違えても同型性は壊れない**(圧縮しか変わらない)ので、
    # ルータが**マッピングの**コンテキストを渡していることを別に固定する。
    # **変異テストで見つけた穴である** — PROV-O のコンテキストを渡す変異が
    # 同型性のテストだけでは生き残った。
    assert json.loads(body)["@context"] == dict(MAPPING_CONTEXT)

    from_turtle = Graph()
    from_turtle.parse(data=bytes(turtle_response.body).decode("utf-8"), format="turtle")
    from_jsonld = Graph()
    from_jsonld.parse(data=body, format="json-ld")
    exported_at = URIRef(MAPPING_ONT_NAMESPACE + "exportedAt")
    for graph in (from_turtle, from_jsonld):
        graph.remove((None, exported_at, None))
    assert isomorphic(from_turtle, from_jsonld), "表現によって内容が違う"


@pytest.mark.integration
async def test_マッピングの書き出しも既定は_Turtle(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**内容交渉は足すだけ**(ADR-0036 決定5)。"""
    from ontology_api.routers.mappings import export_mappings

    await _setup(session)
    for accept in (None, "*/*", "application/json"):
        response = await export_mappings(
            namespace=_SALES,
            principal=_BOTH,
            session=session,
            blob=blob_store,
            accept=accept,
        )
        assert response.media_type is not None
        assert response.media_type.startswith("text/turtle"), accept


# ------------------ 健全性指標に出る(ADR-0037、`P2B-21`)


async def _health(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    *,
    principal: Principal,
    namespace: str = _SALES,
) -> dict[str, Any]:
    from ontology_api.routers.health import measure_health

    return await measure_health(
        namespace=namespace, principal=principal, session=session, blob=blob_store
    )


@pytest.mark.integration
async def test_廃止された先を指すマッピングが指標に出る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """ADR-0030 が受け入れたコスト「気づくのは一覧を見たときだけ」を埋める
    (ADR-0037)。

    **相手の名前空間を読める主体で数える。** `_BOTH` は経理も読めるので、
    廃止を「調べられた」うえで廃止だと分かる。
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V1, version="1.0.0")
    await _declare(session, target=_GONE)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="2.0.0")

    report = await _health(session, blob_store, principal=_BOTH)
    assert report["deprecated_target_mapping_count"] == 1
    assert report["unknown_target_mapping_count"] == 0
    # **`unavailable` に積まない**(ADR-0037 決定2)。項目は測れている。
    assert report["unavailable"] == []


@pytest.mark.integration
async def test_権限が無ければ調べられなかった件数に入る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**指標は呼び出し元に依存する**(ADR-0037 決定4)。

    `_SALES_ANALYST` は経理を読めないので、同じマッピングが `unknown` に
    数えられる。**`active` にも `deprecated` にもしない** — 権限ゲートを
    指標のために外すと存在の oracle が開く(ADR-0030 決定1)。

    **これがこのファイルで最も重要なテストである** — 権限で切っていることを
    指標の経路でも固定する。
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V1, version="1.0.0")
    await _declare(session, target=_GONE)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="2.0.0")

    report = await _health(session, blob_store, principal=_SALES_ANALYST)
    assert report["deprecated_target_mapping_count"] == 0, (
        "読めない名前空間の廃止を指標に出してはならない(存在の oracle になる)"
    )
    assert report["unknown_target_mapping_count"] == 1, (
        "調べられなかったことが消えると、0 件が「健全」と読める"
    )


@pytest.mark.integration
async def test_外部語彙は調べられなかった件数に入る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """SKOS などこのシステムの管理外の用語(ADR-0030 の `unknown` の 5 種)。

    **`active` にしない** — 生きているかどうかを追う手段が無い。
    """
    await _setup(session)
    await _declare(session, target=_EXTERNAL)

    report = await _health(session, blob_store, principal=_BOTH)
    assert report["deprecated_target_mapping_count"] == 0
    assert report["unknown_target_mapping_count"] == 1


@pytest.mark.integration
async def test_生きている先は_どちらにも数えない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="1.0.0")
    await _declare(session, target=_LIVE)

    report = await _health(session, blob_store, principal=_BOTH)
    assert report["deprecated_target_mapping_count"] == 0
    assert report["unknown_target_mapping_count"] == 0


@pytest.mark.integration
async def test_マッピングが無ければ両方_0(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`null` にしない**(ADR-0037 決定2)。「マッピングが無い」は事実である。"""
    await _setup(session)
    report = await _health(session, blob_store, principal=_BOTH)
    assert report["deprecated_target_mapping_count"] == 0
    assert report["unknown_target_mapping_count"] == 0


@pytest.mark.integration
async def test_相手の正本が読めなくても項目は_null_にならない(
    session: AsyncSession, blob_store: OntologyBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**これが `P2B-21` を保留させていた理由そのものである。**

    ADR-0030 は「指標が他の名前空間の Blob の可用性に依存する」ことを理由に
    却下していた。ADR-0037 決定2 の答えは「**項目を `null` にせず、その分を
    `unknown` として数える**」である。

    自分の名前空間の TTL は読めるので `term_count` は測れたままになる —
    **「全部か無か」にしない**(ADR-0020 決定3)。
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="1.0.0")
    await _declare(session, target=_GONE)

    original = blob_store.get_version

    async def _fail_finance(path: str) -> str:
        if _FINANCE in path:
            raise BlobStoreError("経理の Blob を読めません(テスト)")
        return await original(path)

    monkeypatch.setattr(blob_store, "get_version", _fail_finance)
    report = await _health(session, blob_store, principal=_BOTH)

    assert report["deprecated_target_mapping_count"] == 0
    assert report["unknown_target_mapping_count"] == 1, (
        "相手の Blob が読めないことを「廃止ではない」に丸めてはならない"
    )
    assert report["deprecated_target_mapping_count"] is not None
    # 自分の TTL は読めているので、用語に関する項目は測れたまま。
    assert report["term_count"] is not None


@pytest.mark.integration
async def test_指されている側の指標には出ない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`outgoing` だけを数える**(ADR-0037 決定3)。

    営業が経理の廃止済み用語を指している。**経理の指標には出ない** —
    経理は 1 件もマッピングを張っていない。

    **自分では直せないものを自分の健全性の数字にしない。** 相手のマッピングを
    取り消すのは ADR-0023 決定3 の裏返しであり、廃止する側への報告は
    ADR-0030 決定7(`MAPPED_BY_OTHERS`)が別に持っている。
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V1, version="1.0.0")
    await _declare(session, target=_GONE)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="2.0.0")

    # 張った側には出る。
    sales = await _health(session, blob_store, principal=_BOTH)
    assert sales["deprecated_target_mapping_count"] == 1

    # 指されている側には出ない。
    finance = await _health(session, blob_store, principal=_FINANCE_OWNER, namespace=_FINANCE)
    assert finance["deprecated_target_mapping_count"] == 0, (
        "incoming を数えると、自分では直せないものが自分の数字を悪くする"
    )
    assert finance["unknown_target_mapping_count"] == 0


@pytest.mark.integration
async def test_消えている先は廃止として数えない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """`absent` は**廃止ではない**(ADR-0030 決定2 の 4 状態)。

    相手の現行版から消えているのは別の問題であり、一覧では `absent` として
    区別して出る。**指標では廃止だけを数える** — 混ぜると「廃止された先を
    指している」という項目の意味が変わる。
    """
    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="1.0.0")
    await _declare(session, target=_MISSING)

    report = await _health(session, blob_store, principal=_BOTH)
    assert report["deprecated_target_mapping_count"] == 0, (
        "absent を廃止に混ぜると項目の意味が変わる"
    )
    # **調べられてはいる**ので `unknown` でもない。
    assert report["unknown_target_mapping_count"] == 0


@pytest.mark.integration
async def test_生死の辞書に欠落があれば落ちる(
    session: AsyncSession, blob_store: OntologyBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**契約違反は黙って数え落とさない**(ADR-0037 の実装上の判断)。

    `resolve_target_lifecycles` は「返る辞書には対象の全 IRI が入る」という
    契約を持つ(ADR-0030)。指標側が `.get()` で受けると、契約が破れたときに
    **黙って件数が減る** — しかもその分岐は正常時に到達しないので、
    振る舞いを検証できない(変異テストで生き残る)。

    **添字で引いて `KeyError` で落ちる**ようにしてある。それをここで固定する。
    """
    from ontology_api.services import health as health_service

    await _setup(session)
    await _approve_finance(session, blob_store, turtle=_FINANCE_V2, version="1.0.0")
    await _declare(session, target=_GONE)

    async def _incomplete(*args: object, **kwargs: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(health_service, "resolve_target_lifecycles", _incomplete)
    with pytest.raises(KeyError):
        await _health(session, blob_store, principal=_BOTH)
