"""承認の状態遷移(submit/approve/reject)。ADR-0010 / P1-16 の実証。

`FakeStore` は test_projection.py にも同種のものがあるが、テスト間の
cross-import は mypy がモジュール解決できない(`tests/` に `__init__.py` が無く
`mypy_path` にも含めていないため)。`test_versions_router.py` の `_NullStore` と
同様、ファイルごとに最小のフェイクを持つ既存の方針に揃えて複製する。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_api.services.projection import (
    InvalidTransitionError,
    ProjectionService,
    UnknownVersionError,
)
from ontology_core.blob import OntologyBlobStore
from ontology_core.db import AuditEventRow
from ontology_core.models import OntologyVersionStatus
from ontology_core.sparql.client import SparqlStore, SparqlStoreError

pytestmark = pytest.mark.integration

TTL = "@prefix ex: <https://e.example/#> .\nex:A a ex:Class .\n"

Prepared = tuple[AsyncSession, OntologyBlobStore]


class FakeStore(SparqlStore):
    """射影先の代役。test_projection.py の同名クラスと同じ挙動。"""

    def __init__(
        self,
        *,
        fail_put: bool = False,
        fail_default: bool = False,
        fail_delete: bool = False,
    ) -> None:
        self.datasets: list[str] = []
        self.graphs: dict[tuple[str, str], str] = {}
        self.default_graphs: dict[str, str] = {}
        self._fail_put = fail_put
        self._fail_default = fail_default
        self._fail_delete = fail_delete

    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        return {"results": {"bindings": []}}

    async def update(self, sparql: str, *, dataset: str) -> None:
        return None

    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None:
        if self._fail_put:
            raise SparqlStoreError("射影に失敗しました(テスト)")
        self.graphs[(dataset, graph_iri)] = turtle

    async def put_default_graph(self, turtle: str, *, dataset: str) -> None:
        if self._fail_default:
            raise SparqlStoreError("既定グラフへの射影に失敗しました(テスト)")
        self.default_graphs[dataset] = turtle

    async def delete_graph(self, graph_iri: str, *, dataset: str) -> None:
        if self._fail_delete:
            raise SparqlStoreError("グラフの削除に失敗しました(テスト)")
        self.graphs.pop((dataset, graph_iri), None)

    async def list_graphs(self, dataset: str) -> list[str]:
        return sorted(iri for (ds, iri) in self.graphs if ds == dataset)

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


async def _manifest(blob: OntologyBlobStore, namespace: str) -> dict[str, Any]:
    body = await blob.get_version(f"versions/{namespace}/_state.json")
    result: dict[str, Any] = json.loads(body)
    return result


async def test_submit_moves_draft_to_in_review_and_projects_named_graph(
    prepared: Prepared,
) -> None:
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )
    draft = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")

    submitted = await svc.submit(namespace="retail-core", version=draft.version, actor="alice")

    assert submitted.status is OntologyVersionStatus.IN_REVIEW
    assert submitted.projected_at is not None
    assert store.graphs[("retail-core", submitted.graph_iri)] == TTL
    # 既定グラフには載らない(承認済みではないため)。
    assert store.default_graphs == {}

    manifest = await _manifest(blob, "retail-core")
    assert manifest["current"] is None
    assert manifest["versions"] == [{"version": draft.version, "status": "in-review"}]


async def test_submit_rejects_non_draft_with_409(prepared: Prepared) -> None:
    session, blob = prepared
    svc = ProjectionService(
        session=session, blob=blob, store=FakeStore(), graph_iri_base="urn:ontology:graph"
    )
    draft = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")
    await svc.submit(namespace="retail-core", version=draft.version, actor="alice")

    with pytest.raises(InvalidTransitionError):
        await svc.submit(namespace="retail-core", version=draft.version, actor="alice")


async def test_submit_unknown_version_raises_404_equivalent(prepared: Prepared) -> None:
    session, blob = prepared
    svc = ProjectionService(
        session=session, blob=blob, store=FakeStore(), graph_iri_base="urn:ontology:graph"
    )
    with pytest.raises(UnknownVersionError):
        await svc.submit(namespace="retail-core", version="9.9.9", actor="alice")


async def test_approve_rejects_draft_with_409(prepared: Prepared) -> None:
    """draft を approve しようとすると 409 相当(InvalidTransitionError)。"""
    session, blob = prepared
    svc = ProjectionService(
        session=session, blob=blob, store=FakeStore(), graph_iri_base="urn:ontology:graph"
    )
    draft = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")

    with pytest.raises(InvalidTransitionError):
        await svc.approve(namespace="retail-core", version=draft.version, actor="bob")


async def test_approve_projects_named_and_default_graph_and_writes_approved_by(
    prepared: Prepared,
) -> None:
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )
    draft = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")
    await svc.submit(namespace="retail-core", version=draft.version, actor="alice")

    approved = await svc.approve(namespace="retail-core", version=draft.version, actor="bob")

    assert approved.status is OntologyVersionStatus.APPROVED
    assert approved.approved_by == "bob"
    assert approved.approved_at is not None
    assert approved.projected_at is not None
    assert store.graphs[("retail-core", approved.graph_iri)] == TTL
    assert store.default_graphs["retail-core"] == TTL

    manifest = await _manifest(blob, "retail-core")
    assert manifest["current"] == draft.version
    assert manifest["versions"] == [{"version": draft.version, "status": "approved"}]


async def test_approve_rejects_already_approved_with_409(prepared: Prepared) -> None:
    """`approved` を再度 submit/approve しようとすると 409 相当。"""
    session, blob = prepared
    svc = ProjectionService(
        session=session, blob=blob, store=FakeStore(), graph_iri_base="urn:ontology:graph"
    )
    draft = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")
    await svc.submit(namespace="retail-core", version=draft.version, actor="alice")
    await svc.approve(namespace="retail-core", version=draft.version, actor="bob")

    with pytest.raises(InvalidTransitionError):
        await svc.submit(namespace="retail-core", version=draft.version, actor="alice")
    with pytest.raises(InvalidTransitionError):
        await svc.approve(namespace="retail-core", version=draft.version, actor="bob")


async def test_approve_supersedes_previous_approved_automatically(prepared: Prepared) -> None:
    """P1-C1 / ADR-0010 決定3: 新しい版を approve すると、前の approved は
    自動で superseded になる。既定グラフは新しい版の内容だけになる
    (PUT が既定グラフを丸ごと置き換えるため)。
    """
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )

    v1 = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")
    await svc.submit(namespace="retail-core", version=v1.version, actor="alice")
    v1 = await svc.approve(namespace="retail-core", version=v1.version, actor="bob")

    ttl2 = TTL + "\nex:B a ex:Class .\n"
    v2 = await svc.publish(namespace="retail-core", turtle=ttl2, actor="alice")
    await svc.submit(namespace="retail-core", version=v2.version, actor="alice")
    v2 = await svc.approve(namespace="retail-core", version=v2.version, actor="bob")

    rows = await VersionRepository(session).list_for("retail-core")
    by_version = {r.version: r for r in rows}
    assert by_version[v1.version].status is OntologyVersionStatus.SUPERSEDED
    assert by_version[v2.version].status is OntologyVersionStatus.APPROVED

    # 既定グラフは v2 の内容だけになっている(v1 は自動的に置き換わる)。
    assert store.default_graphs["retail-core"] == ttl2
    # v1 の名前付きグラフ自体は残る(保持ポリシーの範囲、監査目的)。
    assert store.graphs[("retail-core", v1.graph_iri)] == TTL

    manifest = await _manifest(blob, "retail-core")
    assert manifest["current"] == v2.version
    assert {"version": v1.version, "status": "superseded"} in manifest["versions"]
    assert {"version": v2.version, "status": "approved"} in manifest["versions"]


async def test_reject_moves_in_review_to_draft_and_removes_named_graph(prepared: Prepared) -> None:
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )
    draft = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")
    submitted = await svc.submit(namespace="retail-core", version=draft.version, actor="alice")
    assert ("retail-core", submitted.graph_iri) in store.graphs

    rejected = await svc.reject(
        namespace="retail-core", version=draft.version, actor="bob", reason="用語が不足"
    )

    assert rejected.status is OntologyVersionStatus.DRAFT
    assert ("retail-core", submitted.graph_iri) not in store.graphs

    manifest = await _manifest(blob, "retail-core")
    assert manifest["versions"] == []
    assert manifest["current"] is None


async def test_reject_rejects_draft_with_409(prepared: Prepared) -> None:
    session, blob = prepared
    svc = ProjectionService(
        session=session, blob=blob, store=FakeStore(), graph_iri_base="urn:ontology:graph"
    )
    draft = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")

    with pytest.raises(InvalidTransitionError):
        await svc.reject(namespace="retail-core", version=draft.version, actor="bob", reason="x")


async def test_reject_delete_graph_404_is_treated_as_idempotent_success(
    prepared: Prepared,
) -> None:
    """submit の射影がまだ終わっていない(named graph が無い)状態で reject しても
    削除操作(delete_graph)は冪等に成功として扱われ、reject 自体は失敗しない。
    """
    session, blob = prepared
    submit_store = FakeStore(fail_put=True)
    svc_submit = ProjectionService(
        session=session, blob=blob, store=submit_store, graph_iri_base="urn:ontology:graph"
    )
    draft = await svc_submit.publish(namespace="retail-core", turtle=TTL, actor="alice")
    await svc_submit.submit(namespace="retail-core", version=draft.version, actor="alice")

    reject_store = FakeStore()  # delete_graph は呼ばれても対象が無いだけ(FakeStore は例外にしない)
    svc_reject = ProjectionService(
        session=session, blob=blob, store=reject_store, graph_iri_base="urn:ontology:graph"
    )
    rejected = await svc_reject.reject(
        namespace="retail-core", version=draft.version, actor="bob", reason="不十分"
    )
    assert rejected.status is OntologyVersionStatus.DRAFT


async def test_all_transitions_are_recorded_in_audit_events_with_reason_on_reject(
    prepared: Prepared,
) -> None:
    """必須テスト7: 各遷移が audit_events に記録され、reject の reason が入る。
    approve による自動 supersede も記録される。
    """
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )

    v1 = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")
    await svc.submit(namespace="retail-core", version=v1.version, actor="alice")
    await svc.approve(namespace="retail-core", version=v1.version, actor="bob")

    v2 = await svc.publish(
        namespace="retail-core", turtle=TTL + "\nex:B a ex:Class .\n", actor="alice"
    )
    await svc.submit(namespace="retail-core", version=v2.version, actor="alice")
    await svc.reject(namespace="retail-core", version=v2.version, actor="bob", reason="用語が不足")
    await svc.submit(namespace="retail-core", version=v2.version, actor="alice")
    await svc.approve(namespace="retail-core", version=v2.version, actor="bob")

    events = (
        (await session.execute(select(AuditEventRow).order_by(AuditEventRow.id))).scalars().all()
    )
    actions = [(e.action, e.subject, e.reason) for e in events]

    assert ("published", f"retail-core@{v1.version}", "") in actions
    assert ("submitted", f"retail-core@{v1.version}", "") in actions
    assert ("approved", f"retail-core@{v1.version}", "") in actions
    assert ("rejected", f"retail-core@{v2.version}", "用語が不足") in actions
    # v2 の approve が v1 を superseded にした自動遷移。
    assert any(
        action == "superseded" and subject == f"retail-core@{v1.version}" and reason
        for action, subject, reason in actions
    )


async def test_reconcile_regenerates_manifest_from_postgres(prepared: Prepared) -> None:
    """必須テスト5: reconcile がマニフェストを PostgreSQL から再生成できる。"""
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )
    v1 = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")
    await svc.submit(namespace="retail-core", version=v1.version, actor="alice")
    await svc.approve(namespace="retail-core", version=v1.version, actor="bob")

    # マニフェストを壊す(正本にはまだ手を付けていない)。
    await blob.put_manifest(
        "retail-core", {"schema": 1, "namespace": "retail-core", "broken": True}
    )

    await svc.reconcile()

    manifest = await _manifest(blob, "retail-core")
    assert manifest["current"] == v1.version
    assert manifest["versions"] == [{"version": v1.version, "status": "approved"}]


# ---- P1-17: reject の名前付きグラフ削除が失敗したときの回収 ----
#
# reject は `delete_graph` の失敗を握り潰して成功を返す(不変条件3と同じ扱い)。
# その結果、却下された版の名前付きグラフが残留し、`GRAPH` 句を明示した
# レビュア・監査経路から見え続ける。`draft` は `unprojected()` の対象外
# (ADR-0010 決定5)なので、以前の reconcile はこれを回収できなかった。
# ADR-0010 の補記1で「グラフ単位の残留検出」を追加した。


async def test_reconcile_removes_named_graph_left_by_failed_reject(prepared: Prepared) -> None:
    """reject 時の delete_graph が失敗して残った名前付きグラフを reconcile が外す。"""
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )
    draft = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")
    submitted = await svc.submit(namespace="retail-core", version=draft.version, actor="alice")
    assert ("retail-core", submitted.graph_iri) in store.graphs

    # 削除が必ず失敗するストアで却下する(Fuseki の一時障害を模す)。
    failing = FakeStore(fail_delete=True)
    failing.datasets = ["retail-core"]
    failing.graphs = dict(store.graphs)
    svc_failing = ProjectionService(
        session=session, blob=blob, store=failing, graph_iri_base="urn:ontology:graph"
    )
    rejected = await svc_failing.reject(
        namespace="retail-core", version=draft.version, actor="bob", reason="用語が不足"
    )
    assert rejected.status is OntologyVersionStatus.DRAFT
    # 削除に失敗したので残留している(これが P1-17 の状態)。
    assert ("retail-core", submitted.graph_iri) in failing.graphs

    # reconcile が回収する。
    recovered = FakeStore()
    recovered.datasets = ["retail-core"]
    recovered.graphs = dict(failing.graphs)
    svc_recover = ProjectionService(
        session=session, blob=blob, store=recovered, graph_iri_base="urn:ontology:graph"
    )
    report = await svc_recover.reconcile()

    assert ("retail-core", submitted.graph_iri) not in recovered.graphs
    assert report.graphs_removed == [submitted.graph_iri]
    assert report.failures == []


async def test_reconcile_keeps_named_graphs_of_in_review_and_approved(prepared: Prepared) -> None:
    """draft でない版の名前付きグラフは reconcile が消さない。"""
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )
    v1 = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")
    await svc.submit(namespace="retail-core", version=v1.version, actor="alice")
    approved = await svc.approve(namespace="retail-core", version=v1.version, actor="bob")

    v2 = await svc.publish(
        namespace="retail-core", turtle=TTL + "\n<urn:x> <urn:y> <urn:z> .\n", actor="alice"
    )
    in_review = await svc.submit(namespace="retail-core", version=v2.version, actor="alice")

    store.datasets = ["retail-core"]
    before = set(store.graphs)
    report = await svc.reconcile()

    assert set(store.graphs) == before
    assert ("retail-core", approved.graph_iri) in store.graphs
    assert ("retail-core", in_review.graph_iri) in store.graphs
    assert report.graphs_removed == []


async def test_reconcile_reports_graphs_outside_our_iri_prefix_without_deleting(
    prepared: Prepared,
) -> None:
    """自分たちの IRI 接頭辞に一致しないグラフは報告するだけで消さない。

    orphan_datasets / orphan_blobs と同じ保守的な方針。`GRAPH_IRI_BASE` を
    変更した後や、利用者が持ち込んだストアに他の用途のグラフがある場合に、
    それを勝手に消してはいけない。
    """
    session, blob = prepared
    store = FakeStore()
    store.datasets = ["retail-core"]
    store.graphs[("retail-core", "https://other.example/graph/1")] = "# 他の用途"
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )

    report = await svc.reconcile()

    assert ("retail-core", "https://other.example/graph/1") in store.graphs
    assert report.foreign_graphs == ["retail-core: https://other.example/graph/1"]
    assert report.graphs_removed == []


async def test_reconcile_reports_graph_removal_failure_without_aborting(
    prepared: Prepared,
) -> None:
    """残留グラフの削除に失敗しても reconcile 全体は止まらず、失敗として報告する。"""
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )
    draft = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")
    submitted = await svc.submit(namespace="retail-core", version=draft.version, actor="alice")
    await svc.reject(namespace="retail-core", version=draft.version, actor="bob", reason="x")

    failing = FakeStore(fail_delete=True)
    failing.datasets = ["retail-core"]
    failing.graphs[("retail-core", submitted.graph_iri)] = TTL
    svc_failing = ProjectionService(
        session=session, blob=blob, store=failing, graph_iri_base="urn:ontology:graph"
    )
    report = await svc_failing.reconcile()

    assert report.graphs_removed == []
    assert any(submitted.graph_iri in f for f in report.failures)
    # マニフェストの再生成(reconcile の後段)まで到達していること。
    manifest = await _manifest(blob, "retail-core")
    assert manifest["versions"] == []


# ---- P1-19: ローダにスキップされた名前空間を運用者が API から検出できる ----
#
# ローダはマニフェストが取得できない・不正な名前空間を丸ごとスキップする
# (1 件の設定不備が他の名前空間を巻き込んで全滅させないため)。しかし
# スキップされた名前空間のデータセットは存在するが空になるため、
# エージェントから見ると「データが無い」と区別がつかない。気づく手段が
# ローダのログしかなかった。
#
# reconcile は正本(PostgreSQL)がどの版の射影を求めているかを知っているので、
# **ストアに実際にあるグラフと突き合わせれば、ローダと一切協調せずに検出できる**。


async def test_reconcile_reports_graphs_the_loader_should_have_loaded(
    prepared: Prepared,
) -> None:
    """承認済みの版のグラフがストアに無いことを reconcile が報告する。

    ローダが名前空間をスキップした後の状態(データセットはあるが空)を模す。
    `projected_at` は過去に射影したときのまま残るため `unprojected()` では
    拾えず、この突き合わせが唯一の検出手段になる。
    """
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )
    draft = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")
    await svc.submit(namespace="retail-core", version=draft.version, actor="alice")
    approved = await svc.approve(namespace="retail-core", version=draft.version, actor="bob")

    # 再構築でスキップされた状態を作る(データセットはあるがグラフが無い)。
    store.graphs.clear()
    store.default_graphs.clear()
    store.datasets = ["retail-core"]

    report = await svc.reconcile()

    assert report.missing_graphs == [f"retail-core: {approved.graph_iri} (approved)"]
    # `projected_at` が残っているため、バージョン単位の回収は動かない
    # (= 報告が唯一の検出手段であることの確認)。
    assert report.versions_projected == []
    assert report.failures == []


async def test_reconcile_does_not_report_missing_superseded_graphs(
    prepared: Prepared,
) -> None:
    """`superseded` のグラフが無いのは正常(SUPERSEDED_RETAIN=0 の既定)。

    ローダは既定で superseded を読み込まないため、これを報告すると
    正常な構成で毎回ノイズが出て、本当の異常が埋もれる。
    """
    session, blob = prepared
    store = FakeStore()
    svc = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )
    v1 = await svc.publish(namespace="retail-core", turtle=TTL, actor="alice")
    await svc.submit(namespace="retail-core", version=v1.version, actor="alice")
    await svc.approve(namespace="retail-core", version=v1.version, actor="bob")

    v2 = await svc.publish(
        namespace="retail-core", turtle=TTL + "<urn:x> <urn:y> <urn:z> .\n", actor="alice"
    )
    await svc.submit(namespace="retail-core", version=v2.version, actor="alice")
    approved = await svc.approve(namespace="retail-core", version=v2.version, actor="bob")

    # v1 は superseded になっている。両方のグラフを消す。
    versions = {
        v.version: v.status for v in await VersionRepository(session).list_for("retail-core")
    }
    assert versions[v1.version] is OntologyVersionStatus.SUPERSEDED
    store.graphs.clear()
    store.datasets = ["retail-core"]

    report = await svc.reconcile()

    # 承認済み現行版だけが報告される。superseded は報告しない。
    assert report.missing_graphs == [f"retail-core: {approved.graph_iri} (approved)"]
