"""名前空間の退役(ADR-0032、`P2B-19`)。

`DELETE /namespaces/{name}` の 409 が「公開済みオントロジーを含む名前空間の
削除は Phase 2(監査経路)で対応します」と約束していた中身である。
[ADR-0024](../../../docs/adr/0024-namespace-delete-locking.md) 決定4 が挙げた
3 つの選択肢のうち「**正本を残し、射影だけ止める**」を採った。

ここで固定するのは 5 つである。

1. **削除ではない。** 版と監査は読めるままである(**それを残すために退役する**)
2. **マニフェストが `skip:retired` になる**(決定2)。`schema` は上げない —
   `projection` は**古いローダが既に従う欄**である(`P2B-C1` の教訓)
3. **内容の増設と SPARQL が 409 になる**(決定5)。
   **SPARQL が 0 行を静かに返さない**のが要点
4. **`reconcile` が再射影しない**(決定4)。`retire` が `projected_at` を
   `NULL` に戻すので、これが無いと即座に射影が戻る
5. **戻せる**(決定1)
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.versions import AuditRepository, VersionRepository
from ontology_api.routers.mappings import MappingDeclare, declare_mapping, revoke_mapping
from ontology_api.routers.namespaces import (
    NamespaceRetire,
    delete_namespace,
    retire_namespace,
    unretire_namespace,
)
from ontology_api.routers.questions import QuestionSetRevise, revise_question_set
from ontology_api.routers.sparql import SparqlQueryRequest, run_query
from ontology_api.routers.versions import PublishRequest, publish_version
from ontology_api.services.projection import ProjectionService
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.models import NamespaceRole, OntologyVersionStatus, PlatformRole
from ontology_core.sparql.client import SparqlStore

_NS = "retire-ns"
_BASE = "https://e.example/retire#"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_OWNER = Principal(subject="owner", object_id="owner-oid")
_MAINTAINER = Principal(subject="maintainer", object_id="maintainer-oid")
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")

_TTL = f"@prefix e: <{_BASE}> .\ne:Thing a <http://www.w3.org/2002/07/owl#Class> .\n"
_TTL2 = (
    f"@prefix e: <{_BASE}> .\n"
    "e:Thing a <http://www.w3.org/2002/07/owl#Class> .\n"
    "e:Other a <http://www.w3.org/2002/07/owl#Class> .\n"
)
_QUESTIONS = """
questions:
  - id: cq-1
    question: 何かあるか
    expect: ask_true
    sparql: |
      ASK { ?s ?p ?o }
"""


class _RecordingStore(SparqlStore):
    """射影の呼び出しを記録する代役。"""

    def __init__(self) -> None:
        self.graphs: dict[str, str] = {}
        self.datasets: set[str] = {"ds"}
        self.deleted_datasets: list[str] = []
        self.fail_delete_dataset = False

    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
        return {"head": {"vars": []}, "results": {"bindings": []}}

    async def construct(self, sparql: str, *, dataset: str) -> str:
        return ""

    async def update(self, sparql: str, *, dataset: str) -> None: ...

    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None:
        self.graphs[graph_iri] = turtle

    async def put_default_graph(self, turtle: str, *, dataset: str) -> None: ...

    async def delete_graph(self, graph_iri: str, *, dataset: str) -> None:
        self.graphs.pop(graph_iri, None)

    async def list_graphs(self, dataset: str) -> list[str]:
        return sorted(self.graphs)

    async def has_default_graph_content(self, dataset: str) -> bool:
        return True

    async def list_datasets(self) -> list[str]:
        return sorted(self.datasets)

    async def create_dataset(self, dataset: str) -> None:
        self.datasets.add(dataset)

    async def delete_dataset(self, dataset: str) -> None:
        if self.fail_delete_dataset:
            from ontology_core.sparql.client import SparqlStoreError

            raise SparqlStoreError("削除に失敗しました(テスト)")
        self.datasets.discard(dataset)
        self.deleted_datasets.append(dataset)
        self.graphs.clear()


async def _setup(session: AsyncSession) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri=_BASE,
        created_by=_ADMIN.object_id,
        require_two_person_approval=False,
    )
    repo = RoleRepository(session)
    for principal, role in (
        (_OWNER, NamespaceRole.OWNER),
        (_MAINTAINER, NamespaceRole.MAINTAINER),
        (_ANALYST, NamespaceRole.DATA_ANALYST),
    ):
        await repo.grant(
            namespace=_NS,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await session.commit()


def _service(
    session: AsyncSession, blob: OntologyBlobStore, store: SparqlStore
) -> ProjectionService:
    return ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base="urn:ontology:graph"
    )


async def _approve(
    session: AsyncSession,
    blob: OntologyBlobStore,
    store: SparqlStore,
    *,
    turtle: str = _TTL,
    version: str = "1.0.0",
) -> None:
    svc = _service(session, blob, store)
    await svc.publish(namespace=_NS, turtle=turtle, actor=_OWNER.object_id, version=version)
    await svc.submit(namespace=_NS, version=version, actor=_OWNER.object_id)
    await svc.approve(namespace=_NS, version=version, actor=_MAINTAINER.object_id)
    await session.commit()


async def _retire(
    session: AsyncSession,
    blob: OntologyBlobStore,
    store: SparqlStore,
    settings: Settings,
    *,
    principal: Principal = _OWNER,
    reason: str = "この領域は統合されたため使わなくなった",
) -> Any:
    return await retire_namespace(
        namespace=_NS,
        payload=NamespaceRetire(reason=reason),
        principal=principal,
        session=session,
        blob=blob,
        store=store,
        settings=settings,
    )


async def _manifest(blob: OntologyBlobStore) -> dict[str, Any]:
    raw = await blob.get_version(f"versions/{_NS}/_state.json")
    parsed: dict[str, Any] = json.loads(raw)
    return parsed


# --------------------------------------------------------- 退役そのもの


@pytest.mark.integration
async def test_退役しても正本は残る(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**これを残すために退役している**(ADR-0032 のコンテキスト)。

    ADR-0006 の中核価値「誰が承認した定義に基づく答えかを説明できること」は、
    **その名前空間が使われなくなった後こそ効く**。
    """
    store = _RecordingStore()
    await _setup(session)
    await _approve(session, blob_store, store)
    await _retire(session, blob_store, store, settings)

    rows = await VersionRepository(session).list_for(_NS)
    assert [r.version for r in rows] == ["1.0.0"]
    assert rows[0].status is OntologyVersionStatus.APPROVED
    # 正本の TTL も読める。
    assert await blob_store.get_version(rows[0].blob_path) == _TTL
    # 監査も残る(退役そのものも記録される)。
    page = await AuditRepository(session).query(namespace=_NS)
    assert "retired" in {e.action for e in page.events}


@pytest.mark.integration
async def test_マニフェストが全版を_skip_retired_にする(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**`schema` を上げずに退役を表現する**(ADR-0032 決定2)。

    `projection` は schema 2 のローダが**そのまま従う**欄なので、
    **ローダを 1 行も変えずに退役が効く**。`P2B-C1` の教訓
    (安全に関わる指示を新しい schema にだけ載せない)の適用である。
    """
    store = _RecordingStore()
    await _setup(session)
    await _approve(session, blob_store, store)
    await _retire(session, blob_store, store, settings)

    manifest = await _manifest(blob_store)
    assert manifest["schema"] == 2, "schema を上げてはいけない(古いローダが拒否する)"
    assert manifest["retired"] is True
    assert manifest["current"] is None
    assert [v["projection"] for v in manifest["versions"]] == ["skip:retired"]


@pytest.mark.integration
async def test_退役でデータセットを消し_射影の記録も戻す(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**次の再構築を待たない**(ADR-0032 決定3)。

    `projected_at` を `NULL` に戻すのは、`unretire` したときに `reconcile` が
    拾えるようにするためである(不変条件10 — `projected_at` は書き込み経路の
    知識であり、こちらから射影を取り消したなら戻すのが正しい)。
    """
    store = _RecordingStore()
    await _setup(session)
    await _approve(session, blob_store, store)
    assert store.graphs  # 射影されている
    await _retire(session, blob_store, store, settings)

    assert store.deleted_datasets == [_NS]
    rows = await VersionRepository(session).list_for(_NS)
    assert all(r.projected_at is None for r in rows)


@pytest.mark.integration
async def test_データセット削除の失敗で退役を失敗させない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**射影の失敗は正本への書き込みを失敗させない**(不変条件3)。

    マニフェストは既に `skip:retired` なので、**次の再構築で必ず止まる**。
    残骸は `reconcile` が消す(決定4)。
    """
    store = _RecordingStore()
    store.fail_delete_dataset = True
    await _setup(session)
    await _approve(session, blob_store, store)
    await _retire(session, blob_store, store, settings)

    found = await NamespaceRepository(session).get(_NS)
    assert found is not None
    assert found.retired is True
    assert (await _manifest(blob_store))["retired"] is True


@pytest.mark.integration
async def test_理由と主体が記録される(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    store = _RecordingStore()
    await _setup(session)
    result = await _retire(session, blob_store, store, settings, reason="統合したため")

    assert result.namespace.retired_reason == "統合したため"
    assert result.namespace.retired_by == _OWNER.object_id
    assert result.namespace.retired_at is not None


@pytest.mark.integration
async def test_二重の退役は_409(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**冪等にしない。** 理由と時刻を黙って上書きすると最初の記録が消える。"""
    store = _RecordingStore()
    await _setup(session)
    await _retire(session, blob_store, store, settings)
    with pytest.raises(HTTPException) as exc:
        await _retire(session, blob_store, store, settings)
    assert exc.value.status_code == 409


@pytest.mark.integration
async def test_退役には_owner_が必要(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """名前空間の退役は射影を止める操作なので削除と同じ最上位に置く。"""
    store = _RecordingStore()
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _retire(session, blob_store, store, settings, principal=_MAINTAINER)
    assert exc.value.status_code == 403


# ------------------------------------------------------- 退役中の経路


@pytest.mark.integration
async def test_退役中は_publish_が_409(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    store = _RecordingStore()
    await _setup(session)
    await _retire(session, blob_store, store, settings)

    with pytest.raises(HTTPException) as exc:
        await publish_version(
            namespace=_NS,
            payload=PublishRequest(version="1.0.0", turtle=_TTL),
            principal=_OWNER,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
            response=Response(),
        )
    assert exc.value.status_code == 409
    assert "退役" in exc.value.detail


@pytest.mark.integration
async def test_退役中の_SPARQL_は_0_行ではなく_409(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**ここが決定5 の要点である。**

    `retire` はデータセットを消すので、クエリを通すと空の結果が返る。
    **エージェントはそれを「該当なし」と読んで回答を作る。** 退役した
    名前空間への問い合わせと、本当に該当が無いのは別の事実である。
    """
    store = _RecordingStore()
    await _setup(session)
    await _approve(session, blob_store, store)
    await _retire(session, blob_store, store, settings)

    with pytest.raises(HTTPException) as exc:
        await run_query(
            namespace=_NS,
            payload=SparqlQueryRequest(query="SELECT ?s WHERE { ?s ?p ?o }"),
            principal=_ANALYST,
            session=session,
            settings=settings,
            store=store,
            response=Response(),
        )
    assert exc.value.status_code == 409
    assert "退役" in exc.value.detail


@pytest.mark.integration
async def test_退役中は想定質問の改訂が_409(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    store = _RecordingStore()
    await _setup(session)
    await _retire(session, blob_store, store, settings)
    with pytest.raises(HTTPException) as exc:
        await revise_question_set(
            namespace=_NS,
            payload=QuestionSetRevise(content=_QUESTIONS, reason="r"),
            principal=_OWNER,
            session=session,
        )
    assert exc.value.status_code == 409


@pytest.mark.integration
async def test_退役中はマッピングの宣言が_409_だが取り消しは通る(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**片付けはできるべきである**(ADR-0032 決定5)。

    新しい主張は止めるが、既にある主張の取り消しは通す。
    """
    store = _RecordingStore()
    await _setup(session)
    await declare_mapping(
        namespace=_NS,
        payload=MappingDeclare(
            source_term=_BASE + "Thing",
            target_term="http://www.w3.org/2004/02/skos/core#Concept",
            predicate="closeMatch",
            reason="上位概念",
        ),
        principal=_OWNER,
        session=session,
    )
    await session.commit()
    await _retire(session, blob_store, store, settings)

    with pytest.raises(HTTPException) as exc:
        await declare_mapping(
            namespace=_NS,
            payload=MappingDeclare(
                source_term=_BASE + "Other",
                target_term="http://www.w3.org/2004/02/skos/core#Concept",
                predicate="closeMatch",
                reason="上位概念",
            ),
            principal=_OWNER,
            session=session,
        )
    assert exc.value.status_code == 409

    # 取り消しは通る。
    await revoke_mapping(
        namespace=_NS,
        source_term=_BASE + "Thing",
        target_term="http://www.w3.org/2004/02/skos/core#Concept",
        principal=_OWNER,
        session=session,
    )


@pytest.mark.integration
async def test_退役中でも版と監査は読める(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**それを残すために退役している**(ADR-0032 決定5)。"""
    from ontology_api.routers.audit import export_provenance, query_audit
    from ontology_api.routers.versions import list_versions

    store = _RecordingStore()
    await _setup(session)
    await _approve(session, blob_store, store)
    await _retire(session, blob_store, store, settings)

    versions = await list_versions(namespace=_NS, principal=_ANALYST, session=session)
    assert [v.version for v in versions] == ["1.0.0"]
    page = await query_audit(namespace=_NS, principal=_ANALYST, session=session)
    assert page.events
    response = await export_provenance(namespace=_NS, principal=_ANALYST, session=session)
    assert bytes(response.body)


# ----------------------------------------------------------- reconcile


@pytest.mark.integration
async def test_reconcile_は退役した名前空間を再射影しない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**これが無いと `reconcile` が即座に射影を戻す**(ADR-0032 決定4)。

    `retire` が `projected_at` を `NULL` に戻すので(決定3)、
    `unprojected()` が全版を返す。
    """
    store = _RecordingStore()
    await _setup(session)
    await _approve(session, blob_store, store)
    await _retire(session, blob_store, store, settings)

    report = await _service(session, blob_store, store).reconcile()
    assert report.retired_namespaces == [_NS]
    assert report.versions_projected == []
    assert store.graphs == {}
    # データセットも作り直さない。
    assert _NS not in store.datasets


@pytest.mark.integration
async def test_reconcile_は退役した名前空間の残骸を消す(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """`retire` の `delete_dataset` の失敗を回収する最後の経路(決定4)。

    正本が「1 版も載せない」と言っているのにストアに載っているなら乖離で
    ある — ADR-0013 の「観測された乖離を直す」そのものである。
    """
    store = _RecordingStore()
    store.fail_delete_dataset = True
    await _setup(session)
    await _approve(session, blob_store, store)
    await _retire(session, blob_store, store, settings)
    assert store.graphs, "削除に失敗したので残っているはず"

    report = await _service(session, blob_store, store).reconcile()
    assert report.retired_graphs_removed
    assert store.graphs == {}


# ------------------------------------------------------------- 解除


@pytest.mark.integration
async def test_退役は戻せる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**正本が無傷なのに回復手段が無いのは不合理である**(ADR-0032 決定1)。"""
    store = _RecordingStore()
    await _setup(session)
    await _approve(session, blob_store, store)
    await _retire(session, blob_store, store, settings)

    result = await unretire_namespace(
        namespace=_NS,
        payload=NamespaceRetire(reason="やはり使う"),
        principal=_OWNER,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    assert result.namespace.retired is False
    assert result.namespace.retired_reason is None
    assert "reconcile" in result.note, "解除の直後はストアが空であることを伝える"

    manifest = await _manifest(blob_store)
    assert manifest["retired"] is False
    assert manifest["current"] == "1.0.0"

    page = await AuditRepository(session).query(namespace=_NS)
    assert "unretired" in {e.action for e in page.events}


@pytest.mark.integration
async def test_解除の後に_reconcile_が射影を戻す(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    store = _RecordingStore()
    await _setup(session)
    await _approve(session, blob_store, store)
    await _retire(session, blob_store, store, settings)
    await unretire_namespace(
        namespace=_NS,
        payload=NamespaceRetire(reason="やはり使う"),
        principal=_OWNER,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )

    report = await _service(session, blob_store, store).reconcile()
    assert report.versions_projected == [f"{_NS}@1.0.0"]
    assert store.graphs


@pytest.mark.integration
async def test_退役していない名前空間の解除は_409(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**何も起きないことを成功として返さない。**"""
    store = _RecordingStore()
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await unretire_namespace(
            namespace=_NS,
            payload=NamespaceRetire(reason="r"),
            principal=_OWNER,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc.value.status_code == 409


@pytest.mark.integration
async def test_無い名前空間の退役は_404(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    store = _RecordingStore()
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await retire_namespace(
            namespace="missing-ns",
            payload=NamespaceRetire(reason="r"),
            principal=_ADMIN,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc.value.status_code == 404


# ----------------------------------------------------------- DELETE の案内


@pytest.mark.integration
async def test_公開済みを含む削除の_409_が退役を案内する(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**約束を果たした形で閉じる**(ADR-0032 決定6)。

    以前は「Phase 2(監査経路)で対応します」と書いてあった。
    """
    store = _RecordingStore()
    await _setup(session)
    await _approve(session, blob_store, store)

    with pytest.raises(HTTPException) as exc:
        await delete_namespace(
            namespace=_NS,
            principal=_OWNER,
            session=session,
            store=store,
            blob=blob_store,
        )
    assert exc.value.status_code == 409
    assert "retire" in exc.value.detail
    assert "Phase 2" not in exc.value.detail


@pytest.mark.integration
async def test_公開済みが無ければ削除は通る(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**作ってすぐ消す経路を塞がない**(ADR-0032 決定6)。"""
    store = _RecordingStore()
    await _setup(session)
    await delete_namespace(
        namespace=_NS, principal=_OWNER, session=session, store=store, blob=blob_store
    )
    assert await NamespaceRepository(session).get(_NS) is None


@pytest.mark.integration
async def test_退役中は状態遷移が_409(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**publish の後に退役しても、その draft を進められない**(ADR-0032 決定5)。

    退役した名前空間で版の状態を動かすと、マニフェストは `skip:retired` の
    ままなのに PostgreSQL の状態だけが変わり、**正本とマニフェストが食い違う**。
    """
    store = _RecordingStore()
    await _setup(session)
    svc = _service(session, blob_store, store)
    await svc.publish(namespace=_NS, turtle=_TTL, actor=_OWNER.object_id, version="1.0.0")
    await session.commit()
    await _retire(session, blob_store, store, settings)

    from ontology_api.services.authorization import NamespaceRetiredError

    with pytest.raises(NamespaceRetiredError):
        await svc.submit(namespace=_NS, version="1.0.0", actor=_OWNER.object_id)
    with pytest.raises(NamespaceRetiredError):
        await svc.approve(namespace=_NS, version="1.0.0", actor=_MAINTAINER.object_id)
    with pytest.raises(NamespaceRetiredError):
        await svc.reject(namespace=_NS, version="1.0.0", actor=_MAINTAINER.object_id, reason="r")


@pytest.mark.integration
async def test_解除は主体と理由も消す(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**`at=None` は「退役の解除」であり、他の引数は無視する**という契約。

    `set_retired` の呼び出し側(`unretire`)は `actor=None` / `reason=None` を
    渡すので、**この契約はサービス経由では観測できない**(変異テストで実際に
    生き残った)。リポジトリの契約としてここで固定する — 残ったままにすると
    「退役していないのに退役した主体と理由が付いている行」ができる。
    """
    await _setup(session)
    repo = NamespaceRepository(session)
    from datetime import UTC, datetime

    await repo.set_retired(_NS, actor="a-oid", reason="やめた", at=datetime.now(UTC))
    # **解除のときに主体と理由を渡しても、消える。**
    cleared = await repo.set_retired(_NS, actor="a-oid", reason="やめた", at=None)
    assert cleared is not None
    assert cleared.retired is False
    assert cleared.retired_by is None
    assert cleared.retired_reason is None


@pytest.mark.integration
async def test_無い名前空間への退役の書き込みは_None(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    from datetime import UTC, datetime

    await _setup(session)
    repo = NamespaceRepository(session)
    assert await repo.set_retired("missing-ns", actor="a", reason="r", at=datetime.now(UTC)) is None
