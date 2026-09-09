"""状態に応じた射影(ADR-0010 決定5・6)を実物の Fuseki に対して検証する。

`test_approval.py` は `FakeStore` で状態遷移そのものの論理を検証しているが、
**P1-C1 の Critical は SPARQL の実際の挙動(既定グラフ / GRAPH 句)の話**であり、
フェイクでは再現できない。ここでは `test_isolation.py` と同じ方針で実物の
Fuseki に対して検証する。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_api.services.projection import ProjectionService
from ontology_core.blob import OntologyBlobStore
from ontology_core.models import OntologyVersionStatus
from ontology_core.sparql.client import FusekiStore

pytestmark = pytest.mark.integration

_PORT = os.environ.get("FUSEKI_PORT", "3030")
_BASE = f"http://localhost:{_PORT}"

TTL_V1 = "@prefix ex: <https://e.example/#> .\nex:A a ex:Class .\n"
TTL_V2 = "@prefix ex: <https://e.example/#> .\nex:B a ex:Class .\n"

# `P1-C1` の実証に使う 2 版。**V2 は V1 の IRI を削除しない。**
#
# 以前は V1 が `ex:A`、V2 が `ex:B` だけを持つ形だったが、`P2B-03`
# (ADR-0017 決定2)で **IRI の削除が承認をブロックするようになった**ため、
# この形では approve が通らない。
#
# 削除ではなく**廃止**して残す形に変え、判別は「同じ用語に新旧の定義が
# 同居するか」で行う。これは `P1-C1` で実際に報告された不具合
# (「GRAPH 句なしのクエリで矛盾する定義が同時に返る」)そのものなので、
# 以前の「主語の数」で見る形より判別力が強い。
_LIFECYCLE_HEAD = (
    "@prefix ex: <https://e.example/#> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "@prefix dcterms: <http://purl.org/dc/terms/> .\n"
)
LIFECYCLE_V1 = _LIFECYCLE_HEAD + 'ex:A a ex:Class ; rdfs:label "旧" .\n'
LIFECYCLE_V2 = (
    _LIFECYCLE_HEAD
    + 'ex:A a ex:Class ; rdfs:label "新" ; owl:deprecated true ;\n'
    + "    dcterms:isReplacedBy ex:B .\n"
    + "ex:B a ex:Class .\n"
)


@pytest.fixture
async def store() -> AsyncIterator[FusekiStore]:
    s = FusekiStore(
        query_endpoint=_BASE + "/{dataset}/sparql",
        update_endpoint=_BASE + "/{dataset}/update",
        gsp_endpoint=_BASE + "/{dataset}/data",
        admin_endpoint=_BASE + "/$/",
        admin_auth=("admin", os.environ.get("FUSEKI_ADMIN_PASSWORD", "localdev")),
    )
    yield s
    await s.aclose()


async def _default_graph_subjects(store: FusekiStore, dataset: str) -> list[str]:
    result = await store.query("SELECT ?s WHERE { ?s ?p ?o }", dataset=dataset)
    return [b["s"]["value"] for b in result["results"]["bindings"]]


async def _default_graph_labels(store: FusekiStore, dataset: str, subject: str) -> list[str]:
    """既定グラフでこの主語に付いているラベルを返す。

    **`P1-C1` の判別に使う。** 既定グラフが全版の和集合になっていると、
    同じ用語に旧版と新版のラベルが**両方**現れる。
    """
    result = await store.query(
        "SELECT ?l WHERE { <" + subject + "> <http://www.w3.org/2000/01/rdf-schema#label> ?l }",
        dataset=dataset,
    )
    return sorted(b["l"]["value"] for b in result["results"]["bindings"])


async def _named_graph_subjects(store: FusekiStore, dataset: str, graph_iri: str) -> list[str]:
    result = await store.query(
        f"SELECT ?s WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }}", dataset=dataset
    )
    return [b["s"]["value"] for b in result["results"]["bindings"]]


@pytest.fixture
async def ns(session: AsyncSession, store: FusekiStore) -> str:
    name = "state-proj"
    # 各テストで確実にクリーンな状態から始める。データセットは PostgreSQL の
    # `session` フィクスチャ(テーブルを毎回 drop_all + create_all する)とは
    # 独立して実 Fuseki 上に残るため、前のテストで書いた既定グラフの内容が
    # 次のテストに漏れる(実際に発生した: 修正前はここで `list_datasets()` に
    # 無ければ作るだけだったため、前のテストが既定グラフに書いた内容を
    # 次のテストの「まだ何も無いはず」のアサーションが誤検出した)。
    if name in await store.list_datasets():
        await store.delete_dataset(name)
    await store.create_dataset(name)
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()
    return name


async def test_p1_c1_two_approved_versions_default_graph_returns_exactly_one(
    session: AsyncSession, blob_store: OntologyBlobStore, store: FusekiStore, ns: str
) -> None:
    """必須テスト1(P1-C1 の実証): 2 版を approved にした状態(1 つは
    superseded になる)で、**同じ用語の新旧の定義が同時に返らない**。

    修正前は publish が承認状態に関わらずそのまま射影し、既定グラフが
    unionDefaultGraph で全版の和集合になっていたため、`GRAPH` 句なしの
    クエリで矛盾する定義が同時に返っていた
    (backlog.md P1-C1 の実測: 2026-09-01)。

    **判別は同じ主語のラベルの数で行う。** `P2B-03` で IRI の削除が承認を
    ブロックするようになったため(ADR-0017 決定2)、「V2 が V1 の用語を
    持たない」形はもう作れない。和集合になっていればラベルが 2 つ現れる。
    """
    svc = ProjectionService(
        session=session, blob=blob_store, store=store, graph_iri_base="urn:ontology:graph"
    )

    v1 = await svc.publish(namespace=ns, turtle=LIFECYCLE_V1, actor="alice")
    v1 = await svc.submit(namespace=ns, version=v1.version, actor="alice")
    v1 = await svc.approve(namespace=ns, version=v1.version, actor="bob")

    v2 = await svc.publish(namespace=ns, turtle=LIFECYCLE_V2, actor="alice")
    v2 = await svc.submit(namespace=ns, version=v2.version, actor="alice")
    v2 = await svc.approve(namespace=ns, version=v2.version, actor="bob")

    v1_after = await VersionRepository(session).get(ns, v1.version)
    assert v1_after is not None
    assert v1_after.status is OntologyVersionStatus.SUPERSEDED

    labels = await _default_graph_labels(store, ns, "https://e.example/#A")
    assert labels == ["新"], f"既定グラフに新旧の定義が同居している: {labels}"
    assert sorted(set(await _default_graph_subjects(store, ns))) == [
        "https://e.example/#A",
        "https://e.example/#B",
    ]


async def test_p1_15_in_review_visible_only_via_graph_clause(
    session: AsyncSession, blob_store: OntologyBlobStore, store: FusekiStore, ns: str
) -> None:
    """必須テスト2(P1-15 の実証): in-review の版は GRAPH 句付きで引ける。
    GRAPH 句無しでは引けない(既定グラフに載っていない)。
    """
    svc = ProjectionService(
        session=session, blob=blob_store, store=store, graph_iri_base="urn:ontology:graph"
    )

    v1 = await svc.publish(namespace=ns, turtle=TTL_V1, actor="alice")
    v1 = await svc.submit(namespace=ns, version=v1.version, actor="alice")

    assert await _default_graph_subjects(store, ns) == []
    assert await _named_graph_subjects(store, ns, v1.graph_iri) == ["https://e.example/#A"]


async def test_draft_is_not_projected_at_all(
    session: AsyncSession, blob_store: OntologyBlobStore, store: FusekiStore, ns: str
) -> None:
    """必須テスト3: publish 直後(draft)は GRAPH 句付きでも引けない
    (Blob と PostgreSQL にのみ存在する。ADR-0010 決定5)。
    """
    svc = ProjectionService(
        session=session, blob=blob_store, store=store, graph_iri_base="urn:ontology:graph"
    )

    v1 = await svc.publish(namespace=ns, turtle=TTL_V1, actor="alice")

    assert await _default_graph_subjects(store, ns) == []
    assert await _named_graph_subjects(store, ns, v1.graph_iri) == []


async def test_list_graphs_returns_named_graphs_from_real_fuseki(
    store: FusekiStore, ns: str
) -> None:
    """`list_graphs` の SPARQL クエリが実物の Fuseki で意図どおり動くこと(ADR-0010 補記1)。

    このクエリ(`SELECT DISTINCT ?g WHERE { GRAPH ?g { ?s ?p ?o } }`)が
    フェイクでは検証できない部分そのものである。既定グラフの内容が
    名前付きグラフとして混ざらないことも同時に確認する(混ざると
    `reconcile` が既定グラフを「残留」と誤判定しかねない)。
    """
    await store.put_default_graph(TTL_V1, dataset=ns)
    assert await store.list_graphs(ns) == []

    await store.put_graph("urn:ontology:graph/state-proj/1.0.0", TTL_V1, dataset=ns)
    await store.put_graph("urn:ontology:graph/state-proj/2.0.0", TTL_V2, dataset=ns)

    assert await store.list_graphs(ns) == [
        "urn:ontology:graph/state-proj/1.0.0",
        "urn:ontology:graph/state-proj/2.0.0",
    ]

    await store.delete_graph("urn:ontology:graph/state-proj/1.0.0", dataset=ns)
    assert await store.list_graphs(ns) == ["urn:ontology:graph/state-proj/2.0.0"]


async def test_reconcile_removes_residual_named_graph_on_real_fuseki(
    session: AsyncSession, blob_store: OntologyBlobStore, store: FusekiStore, ns: str
) -> None:
    """P1-17 の回収が実物の Fuseki でも成立すること。

    `reject` の `delete_graph` が失敗した状況を、状態だけ `draft` に戻して
    グラフを残すことで作る(実際の Fuseki 一時障害と同じ最終状態)。
    """
    svc = ProjectionService(
        session=session, blob=blob_store, store=store, graph_iri_base="urn:ontology:graph"
    )
    draft = await svc.publish(namespace=ns, turtle=TTL_V1, actor="alice")
    submitted = await svc.submit(namespace=ns, version=draft.version, actor="alice")
    assert await _named_graph_subjects(store, ns, submitted.graph_iri) != []

    # 削除だけが失敗した状態を作る(PG は draft、Fuseki にはグラフが残る)。
    await VersionRepository(session).set_status(
        ns, draft.version, status=OntologyVersionStatus.DRAFT, reset_projected=True
    )
    await session.commit()
    assert submitted.graph_iri in await store.list_graphs(ns)

    report = await svc.reconcile()

    assert report.graphs_removed == [submitted.graph_iri]
    assert submitted.graph_iri not in await store.list_graphs(ns)
    assert await _named_graph_subjects(store, ns, submitted.graph_iri) == []


async def test_reconcile_repairs_a_wiped_store_on_real_fuseki(
    session: AsyncSession, blob_store: OntologyBlobStore, store: FusekiStore, ns: str
) -> None:
    """P1-25 / ADR-0013: ストアの内容が失われても reconcile が復旧すること。

    ローダが名前空間をスキップした後(データセットはあるが空)を実物で作り、
    **`GRAPH` 句を書かないクエリが再び答えを返す**ところまで確認する。
    フェイクでは既定グラフの実際の挙動を検証できないため、ここが本題。
    """
    svc = ProjectionService(
        session=session, blob=blob_store, store=store, graph_iri_base="urn:ontology:graph"
    )
    draft = await svc.publish(namespace=ns, turtle=TTL_V1, actor="alice")
    await svc.submit(namespace=ns, version=draft.version, actor="alice")
    approved = await svc.approve(namespace=ns, version=draft.version, actor="bob")

    assert await _default_graph_subjects(store, ns) != []
    assert await store.has_default_graph_content(ns) is True

    # データセットを作り直して中身を失わせる(ローダのスキップと同じ最終状態)。
    await store.delete_dataset(ns)
    await store.create_dataset(ns)
    assert await store.list_graphs(ns) == []
    assert await store.has_default_graph_content(ns) is False
    assert await _default_graph_subjects(store, ns) == []

    report = await svc.reconcile()

    # 名前付きグラフと既定グラフの両方が戻っている。
    assert approved.graph_iri in await store.list_graphs(ns)
    assert await store.has_default_graph_content(ns) is True
    assert await _default_graph_subjects(store, ns) != []
    # 報告は消えない(ADR-0013 決定5)。
    assert len(report.missing_graphs) == 2
    assert len(report.graphs_repaired) == 2
    assert report.failures == []
