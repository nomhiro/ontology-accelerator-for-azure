"""射影ループ。正本に書いてからストアへ射影することを検証する。"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_api.services.projection import ProjectionService
from ontology_core.blob import OntologyBlobStore
from ontology_core.sparql.client import SparqlStore, SparqlStoreError

pytestmark = pytest.mark.integration

TTL = "@prefix ex: <https://e.example/#> .\nex:A a ex:Class .\n"

# session と blob_store をまとめて受け渡すためのフィクスチャの戻り値型。
Prepared = tuple[AsyncSession, OntologyBlobStore]


class FakeStore(SparqlStore):
    """射影先の代役。put_graph の呼び出しを記録する。"""

    def __init__(self, *, fail_put: bool = False) -> None:
        self.datasets: list[str] = []
        self.graphs: dict[tuple[str, str], str] = {}
        self._fail_put = fail_put

    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        return {"results": {"bindings": []}}

    async def update(self, sparql: str, *, dataset: str) -> None:
        return None

    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None:
        if self._fail_put:
            raise SparqlStoreError("射影に失敗しました(テスト)")
        self.graphs[(dataset, graph_iri)] = turtle

    async def list_datasets(self) -> list[str]:
        return list(self.datasets)

    async def create_dataset(self, dataset: str) -> None:
        self.datasets.append(dataset)

    async def delete_dataset(self, dataset: str) -> None:
        self.datasets.remove(dataset)


@pytest.fixture
async def prepared(session: AsyncSession, blob_store: OntologyBlobStore) -> Prepared:
    await NamespaceRepository(session).create(
        name="retail-core",
        display_name="小売",
        description="",
        base_iri="https://e.example/retail#",
        created_by="t",
    )
    await session.commit()
    return session, blob_store


async def test_publish_writes_source_of_truth_then_projects(prepared: Prepared) -> None:
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )

    version = await svc.publish(namespace="retail-core", turtle=TTL, actor="tester")

    # 正本
    assert version.content_hash == hashlib.sha256(TTL.encode()).hexdigest()
    assert version.blob_path == "approved/retail-core/1.0.0.ttl"
    assert await blob.get_version(version.blob_path) == TTL
    # 射影
    assert ("retail-core", version.graph_iri) in store.graphs
    assert store.graphs[("retail-core", version.graph_iri)] == TTL
    # 射影済みが記録される
    rows = await VersionRepository(session).list_for("retail-core")
    assert rows[0].projected_at is not None


async def test_publish_is_idempotent_for_same_content(prepared: Prepared) -> None:
    session, blob = prepared
    svc = ProjectionService(
        session=session, blob=blob, store=FakeStore(), graph_iri_base="urn:ontology:graph"
    )

    first = await svc.publish(namespace="retail-core", turtle=TTL, actor="t")
    second = await svc.publish(namespace="retail-core", turtle=TTL, actor="t")

    assert first.version == second.version
    assert len(await VersionRepository(session).list_for("retail-core")) == 1


async def test_version_is_incremented_for_new_content(prepared: Prepared) -> None:
    session, blob = prepared
    svc = ProjectionService(
        session=session, blob=blob, store=FakeStore(), graph_iri_base="urn:ontology:graph"
    )

    first = await svc.publish(namespace="retail-core", turtle=TTL, actor="t")
    second = await svc.publish(
        namespace="retail-core", turtle=TTL + "\nex:B a ex:Class .\n", actor="t"
    )

    assert first.version == "1.0.0"
    assert second.version == "1.1.0"


async def test_projection_failure_keeps_source_of_truth(prepared: Prepared) -> None:
    """射影が失敗しても正本は残り、未射影として記録される。"""
    session, blob = prepared
    svc = ProjectionService(
        session=session,
        blob=blob,
        store=FakeStore(fail_put=True),
        graph_iri_base="urn:ontology:graph",
    )

    version = await svc.publish(namespace="retail-core", turtle=TTL, actor="t")

    assert await blob.get_version(version.blob_path) == TTL
    rows = await VersionRepository(session).list_for("retail-core")
    assert rows[0].projected_at is None
    assert len(await VersionRepository(session).unprojected()) == 1


async def test_reconcile_projects_unprojected_versions(prepared: Prepared) -> None:
    """reconcile が未射影のバージョンとデータセットを埋める。"""
    session, blob = prepared
    failing = FakeStore(fail_put=True)
    await ProjectionService(
        session=session, blob=blob, store=failing, graph_iri_base="urn:ontology:graph"
    ).publish(namespace="retail-core", turtle=TTL, actor="t")
    await session.commit()

    healthy = FakeStore()
    report = await ProjectionService(
        session=session, blob=blob, store=healthy, graph_iri_base="urn:ontology:graph"
    ).reconcile()

    assert "retail-core" in report.datasets_created
    assert len(report.versions_projected) == 1
    assert not report.failures
    assert len(await VersionRepository(session).unprojected()) == 0


async def test_reconcile_reports_orphan_datasets_without_deleting(prepared: Prepared) -> None:
    """正本に無いデータセットは報告されるが削除されない。

    名前空間の作成はコミット前にデータセットを作るため、コミット失敗で宙に浮いた
    データセットが残りうる。削除は破壊的なので運用者の判断に委ねる。
    """
    session, blob = prepared
    store = FakeStore()
    await store.create_dataset("retail-core")
    await store.create_dataset("orphan-ns")

    report = await ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    ).reconcile()

    assert report.orphan_datasets == ["orphan-ns"]
    # 報告しただけで消していないこと。
    assert "orphan-ns" in await store.list_datasets()


async def test_audit_event_is_recorded(prepared: Prepared) -> None:
    from sqlalchemy import select

    from ontology_core.db import AuditEventRow

    session, blob = prepared
    svc = ProjectionService(
        session=session, blob=blob, store=FakeStore(), graph_iri_base="urn:ontology:graph"
    )
    await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")

    events = (await session.execute(select(AuditEventRow))).scalars().all()
    assert [e.action for e in events] == ["published"]
    assert events[0].actor == "alice"
