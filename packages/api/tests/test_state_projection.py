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
    superseded になる)で、GRAPH 句なしのクエリが 1 件だけ返る。

    修正前は publish が承認状態に関わらずそのまま射影し、既定グラフが
    unionDefaultGraph で全版の和集合になっていたため 2 件返っていた
    (backlog.md P1-C1 の実測: 2026-09-01)。
    """
    svc = ProjectionService(
        session=session, blob=blob_store, store=store, graph_iri_base="urn:ontology:graph"
    )

    v1 = await svc.publish(namespace=ns, turtle=TTL_V1, actor="alice")
    v1 = await svc.submit(namespace=ns, version=v1.version, actor="alice")
    v1 = await svc.approve(namespace=ns, version=v1.version, actor="bob")

    v2 = await svc.publish(namespace=ns, turtle=TTL_V2, actor="alice")
    v2 = await svc.submit(namespace=ns, version=v2.version, actor="alice")
    v2 = await svc.approve(namespace=ns, version=v2.version, actor="bob")

    v1_after = await VersionRepository(session).get(ns, v1.version)
    assert v1_after is not None
    assert v1_after.status is OntologyVersionStatus.SUPERSEDED

    subjects = await _default_graph_subjects(store, ns)
    assert len(subjects) == 1
    assert subjects == ["https://e.example/#B"]


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
