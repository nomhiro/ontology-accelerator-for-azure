"""射影ループ。正本に書いてからストアへ射影することを検証する。"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_api.services.projection import AutoVersionError, ProjectionService
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
    """射影が失敗しても正本は残り、未射影として記録される。

    O-1: `put_graph` の前に正本(PostgreSQL)を commit する。そのため失敗時に
    呼び出し元へ返す例外(`SparqlStoreError`)は正本の書き込みを巻き戻さない
    (Blob は元々 commit 概念が無く書いた時点で耐久化、PostgreSQL は明示 commit
    済み)。この3点セットの検証は
    `test_put_graph_failure_after_commit_returns_error_and_is_recovered_by_reconcile`
    でロールバックを挟んで確認する。ここでは publish() 自体の基本挙動のみ見る。
    """
    session, blob = prepared
    svc = ProjectionService(
        session=session,
        blob=blob,
        store=FakeStore(fail_put=True),
        graph_iri_base="urn:ontology:graph",
    )

    with pytest.raises(SparqlStoreError):
        await svc.publish(namespace="retail-core", turtle=TTL, actor="t")

    rows = await VersionRepository(session).list_for("retail-core")
    assert len(rows) == 1
    assert await blob.get_version(rows[0].blob_path) == TTL
    assert rows[0].projected_at is None
    assert len(await VersionRepository(session).unprojected()) == 1


async def test_put_graph_failure_after_commit_returns_error_and_is_recovered_by_reconcile(
    prepared: Prepared,
) -> None:
    """O-1 の必須テスト: `put_graph` が失敗したとき、(a) 呼び出し元に例外
    (ルーターでは 500)が返り、(b) PG に行が残り `projected_at` が NULL であり、
    (c) `reconcile()` がその行を拾って射影を完了させることを通しで確認する。

    `put_graph` の前に commit しているため、呼び出し元への例外伝播で
    `session_scope` がロールバックを呼んでも(耐久化の観点で)行は消えない
    ことを、実際に rollback を挟んで確認する(commit 前に発生する他の例外
    ―― 例えば一意制約違反 ―― と違って、ここは巻き戻せないことが O-1 の要点)。
    """
    session, blob = prepared
    failing = FakeStore(fail_put=True)
    svc = ProjectionService(
        session=session, blob=blob, store=failing, graph_iri_base="urn:ontology:graph"
    )

    with pytest.raises(SparqlStoreError):
        await svc.publish(namespace="retail-core", turtle=TTL, actor="t")

    # session_scope の例外パス(`except Exception: await session.rollback(); raise`)
    # を模す。commit 済みの行は rollback では消えない。
    await session.rollback()

    rows = await VersionRepository(session).list_for("retail-core")
    assert len(rows) == 1
    assert rows[0].projected_at is None

    healthy = FakeStore()
    report = await ProjectionService(
        session=session, blob=blob, store=healthy, graph_iri_base="urn:ontology:graph"
    ).reconcile()

    assert len(report.versions_projected) == 1
    assert not report.failures
    rows = await VersionRepository(session).list_for("retail-core")
    assert rows[0].projected_at is not None


async def test_reconcile_projects_unprojected_versions(prepared: Prepared) -> None:
    """reconcile が未射影のバージョンとデータセットを埋める。"""
    session, blob = prepared
    failing = FakeStore(fail_put=True)
    with pytest.raises(SparqlStoreError):
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


async def test_reconcile_reports_blob_failures_without_aborting(prepared: Prepared) -> None:
    """Blob 側の失敗(実際の Azure SDK 例外)も reconcile を止めず、他の件は続行する。

    OntologyBlobStore が azure の例外を BlobStoreError で包んでいなければ、
    reconcile はここで未処理の例外のまま落ち、正常な方の版も failures に記録
    されないまま処理が中断する。モックで BlobStoreError を投げるのではなく、
    実在しない Blob を読ませて本物の azure.core.exceptions を発生させる。
    """
    from sqlalchemy import select

    from ontology_core.db import OntologyVersionRow

    session, blob = prepared
    failing = FakeStore(fail_put=True)
    svc = ProjectionService(
        session=session, blob=blob, store=failing, graph_iri_base="urn:ontology:graph"
    )

    # O-1: put_graph の前に commit するため、失敗しても正本(PG 行)は残る。
    # 呼び出し元には例外が伝播する(必ず catch すること)。
    with pytest.raises(SparqlStoreError):
        await svc.publish(namespace="retail-core", turtle=TTL, actor="t")
    with pytest.raises(SparqlStoreError):
        await svc.publish(namespace="retail-core", turtle=TTL + "\nex:B a ex:Class .\n", actor="t")
    await session.commit()

    rows = await VersionRepository(session).list_for("retail-core")
    assert len(rows) == 2
    ok, broken = rows[0], rows[1]

    # broken 版の DB 上の blob_path を、実在しない Blob を指すように壊す。
    row = (
        await session.execute(
            select(OntologyVersionRow).where(
                OntologyVersionRow.namespace == "retail-core",
                OntologyVersionRow.version == broken.version,
            )
        )
    ).scalar_one()
    row.blob_path = "approved/retail-core/does-not-exist.ttl"
    await session.flush()

    healthy = FakeStore()
    report = await ProjectionService(
        session=session, blob=blob, store=healthy, graph_iri_base="urn:ontology:graph"
    ).reconcile()

    assert f"retail-core@{ok.version}" in report.versions_projected
    assert f"retail-core@{broken.version}" not in report.versions_projected
    assert any(broken.version in failure for failure in report.failures)


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


async def test_auto_versioning_after_non_numeric_minor_is_rejected_not_500(
    prepared: Prepared,
) -> None:
    """M-1: 英字を含む明示バージョン(validate_version は許可する)の後、
    version 省略の publish は 500(未捕捉の ValueError)ではなく
    `AutoVersionError` になり、自動採番を諦めて明示バージョンを促すこと。
    """
    session, blob = prepared
    svc = ProjectionService(
        session=session, blob=blob, store=FakeStore(), graph_iri_base="urn:ontology:graph"
    )

    # 明示バージョンでの publish 自体は成功する(validate_version は英字を許可)。
    explicit = await svc.publish(namespace="retail-core", turtle=TTL, actor="t", version="1.beta.0")
    assert explicit.version == "1.beta.0"

    with pytest.raises(AutoVersionError, match=r"1\.beta\.0"):
        await svc.publish(namespace="retail-core", turtle=TTL + "\nex:B a ex:Class .\n", actor="t")

    # 例外は Blob 書き込みより前(_next_version は正本に触る前)なので、
    # 失敗した publish の分の版は増えていない。
    assert len(await VersionRepository(session).list_for("retail-core")) == 1


async def test_auto_versioning_rejects_date_like_result(prepared: Prepared) -> None:
    """ドットを含まない版(例: 日付形式)は split(".") で major 側に丸ごと入り、
    "2026-08-30.1.0" のような無意味な結果になる経路も併せて塞がれること。
    """
    session, blob = prepared
    svc = ProjectionService(
        session=session, blob=blob, store=FakeStore(), graph_iri_base="urn:ontology:graph"
    )

    await svc.publish(namespace="retail-core", turtle=TTL, actor="t", version="2026-08-30")

    with pytest.raises(AutoVersionError, match="2026-08-30"):
        await svc.publish(namespace="retail-core", turtle=TTL + "\nex:B a ex:Class .\n", actor="t")


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
