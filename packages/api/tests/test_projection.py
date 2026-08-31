"""射影ループ。正本に書いてからストアへ射影することを検証する。"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_api.services.projection import AutoVersionError, ProjectionService
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.db import create_engine_and_factory
from ontology_core.models import OntologyVersion
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


async def test_race_recovery_uses_winners_version_and_graph_iri(
    prepared: Prepared, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I-2(a): 一意制約レースの回復パスは、勝った側の version と graph_iri を使う。

    修正前は `recorded = conflict` の後 `resolved` / `graph_iri` が負けた側の
    ローカル変数のままだったため、`mark_projected` が当たらず `NoResultFound`
    (SparqlStoreError ではないので握り潰されず 500)になり、`put_graph` も
    負けた側の graph_iri で呼ばれて孤児グラフが生まれていた。

    本物の同時実行(asyncio.gather)によるレースはタイミング依存で決定的に
    再現しづらいため、`find_by_hash` の事前チェックを1回だけ空振りさせる
    monkeypatch で「両方が record() まで到達した」状況を再現する
    (`test_namespaces_repo.py` の `test_create_is_rejected_when_precheck_race_slips_through`
    と同じ手法)。
    """
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )

    # X: 明示バージョンで先に commit まで進む(勝った側)。
    winner = await svc.publish(namespace="retail-core", turtle=TTL, actor="x", version="2.0.0")

    # Y: 事前の find_by_hash チェックだけ「X が見えない」状態を再現し、
    # record() まで進ませて (namespace, content_hash) の一意制約に当てる。
    # except ブランチ内の find_by_hash(conflict の取得)は本物の実装のまま。
    real_find_by_hash = VersionRepository.find_by_hash
    calls = {"n": 0}

    async def _miss_once(
        self: VersionRepository, namespace: str, content_hash: str
    ) -> OntologyVersion | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real_find_by_hash(self, namespace, content_hash)

    monkeypatch.setattr(VersionRepository, "find_by_hash", _miss_once)

    loser = await svc.publish(namespace="retail-core", turtle=TTL, actor="y")

    # 戻り値は勝った側そのもの。
    assert loser.version == winner.version == "2.0.0"
    assert loser.graph_iri == winner.graph_iri

    # put_graph は勝った側の graph_iri に対してだけ呼ばれ、
    # 負けた側が使おうとした版(auto版、"2.1.0")宛の孤児グラフは無い。
    assert store.graphs == {("retail-core", winner.graph_iri): TTL}

    # mark_projected が勝った側の行に当たり、500 にならず正常に完了している。
    rows = await VersionRepository(session).list_for("retail-core")
    assert len(rows) == 1
    assert rows[0].projected_at is not None


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


async def test_put_graph_failure_is_durably_committed_before_projection(
    prepared: Prepared, settings: Settings
) -> None:
    """O-1 の必須テスト: `put_graph` の前に正本(PostgreSQL)を commit するため、
    put_graph が失敗してもその時点で行は既に耐久化されている。

    `publish()` 自体は例外を投げず成功として返す(`projected_at` が NULL、
    reconcile が回収する既存の正しい挙動、これは変えない)。ここで確認するのは
    「その耐久化がいつ起きているか」であり、同一セッション内の `flush()` だけでは
    「読めるが未コミット」との違いを判定できないため、**独立した新規コネクション**
    (別セッション)から見えることを確認する。修正前(commit がリクエスト終了時
    のみ)は `publish()` を直接呼ぶこのテストでは commit が一度も走らないため、
    別コネクションからは 0 件に見えて失敗する。
    """
    session, blob = prepared
    failing = FakeStore(fail_put=True)
    svc = ProjectionService(
        session=session, blob=blob, store=failing, graph_iri_base="urn:ontology:graph"
    )

    version = await svc.publish(namespace="retail-core", turtle=TTL, actor="t")
    assert version.projected_at is None

    engine2, factory2 = create_engine_and_factory(settings)
    try:
        async with factory2() as other_session:
            rows = await VersionRepository(other_session).list_for("retail-core")
            assert len(rows) == 1
            assert rows[0].projected_at is None
    finally:
        await engine2.dispose()

    # reconcile がこの行を拾って射影を完了させる。
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

    ok = await svc.publish(namespace="retail-core", turtle=TTL, actor="t")
    broken = await svc.publish(
        namespace="retail-core", turtle=TTL + "\nex:B a ex:Class .\n", actor="t"
    )
    await session.commit()

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


async def test_reconcile_reports_orphan_blobs_without_deleting(prepared: Prepared) -> None:
    """I-2(b): PG に対応する行が無い Blob(孤児 TTL)は orphan_blobs に報告され、
    削除されない(orphan_datasets と同じ「報告のみ」方針。ADR-0006 の不変リビジョン)。

    一意制約レースの回復パス(I-2(a))で負けた側が書いた Blob や、名前空間削除の
    TOCTOU ウィンドウ(O-1)で残る Blob がこの経路で検出される想定。
    """
    session, blob = prepared
    svc = ProjectionService(
        session=session, blob=blob, store=FakeStore(), graph_iri_base="urn:ontology:graph"
    )
    published = await svc.publish(namespace="retail-core", turtle=TTL, actor="t")
    await session.commit()

    # PG の記録を経由せず Blob に直接置く(孤児 TTL を模す)。
    orphan_path = await blob.put_version("retail-core", "9.9.9", TTL + "\nex:Z a ex:Class .\n")

    report = await svc.reconcile()

    assert report.orphan_blobs == [orphan_path]
    # 報告しただけで消していないこと。
    assert orphan_path in await blob.list_versions("retail-core")
    # 正本に記録済みの版は孤児として報告されない。
    assert published.blob_path not in report.orphan_blobs


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
