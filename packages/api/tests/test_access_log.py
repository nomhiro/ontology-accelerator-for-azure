"""アクセスログの API 経路のテスト(P2B-05、ADR-0018)。

**要点は 4 つ。**

1. **SPARQL の経路で記録される**(ADR-0006 決定4 の「コンテキスト」)
2. **記録の失敗はクエリを失敗させない**(決定3。不変条件3 と同じ向き)
3. **イベントは `owner`、集約は `data-analyst`**(決定5)
4. **削除は明示的で、削除したことが `audit_events` に残る**(決定2)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.access import AccessRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.versions import AuditRepository, VersionRepository
from ontology_api.routers.access import (
    AccessLogPurge,
    list_term_access,
    purge_access_log,
    query_access_log,
)
from ontology_api.routers.sparql import SparqlQueryRequest, run_query
from ontology_core.auth.entra import Principal
from ontology_core.config import Settings
from ontology_core.models import NamespaceRole, OntologyVersionStatus, PlatformRole
from ontology_core.sparql.client import SparqlStore, SparqlStoreError

_NS = "access-ns"
_BASE = "https://e.example/#"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_OWNER = Principal(subject="owner", object_id="owner-oid")
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")
_AGENT = Principal(subject="agent", object_id="agent-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")


class _Store(SparqlStore):
    """クエリの結果を差し込めるストアの代役。

    廃止済み用語の取得(`P2B-03`)と本来のクエリを区別して返す。
    """

    def __init__(self, *, results: list[str] | None = None) -> None:
        self._results = results if results is not None else []
        self.queries: list[str] = []

    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        self.queries.append(sparql)
        values = [] if "owl#deprecated" in sparql else self._results
        return {
            "head": {"vars": ["s"]},
            "results": {"bindings": [{"s": {"type": "uri", "value": v}} for v in values]},
        }

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
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri=_BASE,
        created_by=_ADMIN.object_id,
    )
    repo = RoleRepository(session)
    for principal, role in (
        (_OWNER, NamespaceRole.OWNER),
        (_ANALYST, NamespaceRole.DATA_ANALYST),
        (_AGENT, NamespaceRole.DATA_ANALYST),
    ):
        await repo.grant(
            namespace=_NS,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await session.commit()


async def _record_version(session: AsyncSession, version: str) -> None:
    """承認済みの版を 1 つ作る(既定グラフの版として記録されるもの)。"""
    repo = VersionRepository(session)
    await repo.record(
        namespace=_NS,
        version=version,
        content_hash="h" * 64,
        graph_iri=f"urn:ontology:graph/{_NS}/{version}",
        blob_path=f"versions/{_NS}/{version}.ttl",
        created_by=_ADMIN.object_id,
        status=OntologyVersionStatus.APPROVED,
    )
    await session.commit()


async def _run(
    session: AsyncSession,
    settings: Settings,
    *,
    store: SparqlStore,
    principal: Principal = _AGENT,
    query: str = "SELECT ?s WHERE { ?s ?p ?o }",
) -> dict[str, Any]:
    result = await run_query(
        namespace=_NS,
        payload=SparqlQueryRequest(query=query),
        principal=principal,
        session=session,
        settings=settings,
        store=store,
        response=Response(),
    )
    await session.commit()
    return result


# ---------------------------------------------------------------- 記録


@pytest.mark.integration
async def test_SPARQL_の経路で記録される(session: AsyncSession, settings: Settings) -> None:
    """ADR-0006 決定4 の「エージェントへ提供したコンテキスト」はこの経路である。"""
    await _setup(session)
    await _record_version(session, "2.0.0")
    await _run(session, settings, store=_Store(results=[_BASE + "Product"]))

    page = await query_access_log(namespace=_NS, principal=_OWNER, session=session)
    assert len(page.events) == 1
    event = page.events[0]
    assert event.actor == _AGENT.object_id
    assert event.query_text == "SELECT ?s WHERE { ?s ?p ?o }"
    assert event.default_graph_version == "2.0.0"
    assert event.used_graph_clause is False
    assert event.returned_row_count == 1
    assert event.returned_term_count == 1


@pytest.mark.integration
async def test_用語の集約が更新される(session: AsyncSession, settings: Settings) -> None:
    await _setup(session)
    store = _Store(results=[_BASE + "Product", _BASE + "Customer"])
    await _run(session, settings, store=store)
    await _run(session, settings, store=store)

    terms = await list_term_access(namespace=_NS, principal=_ANALYST, session=session)
    assert {t.term_iri for t in terms} == {_BASE + "Product", _BASE + "Customer"}
    assert all(t.access_count == 2 for t in terms), "2 回参照したので 2 になる"


@pytest.mark.integration
async def test_最後の参照が更新される(session: AsyncSession, settings: Settings) -> None:
    """**`server_default` は挿入時にしか効かない。** 更新の側で
    `last_accessed_at` を触らないと、最初の参照のまま止まる。
    """
    await _setup(session)
    store = _Store(results=[_BASE + "Product"])
    await _run(session, settings, store=store)
    first = (await list_term_access(namespace=_NS, principal=_ANALYST, session=session))[0]

    await _run(session, settings, store=store)
    second = (await list_term_access(namespace=_NS, principal=_ANALYST, session=session))[0]

    # **`>=` にしてはいけない。** 等号を許すと「更新していない」実装でも通る
    # (実際に変異テストで見逃した)。別のトランザクションなので `now()` は
    # 必ず進む。
    assert second.last_accessed_at > first.last_accessed_at
    assert second.access_count == 2


@pytest.mark.integration
async def test_外部語彙の_IRI_は集約しない(session: AsyncSession, settings: Settings) -> None:
    """**使われていない外部 IRI を「縮める」ことはできない**(決定6)。"""
    await _setup(session)
    await _run(
        session,
        settings,
        store=_Store(results=[_BASE + "Product", "https://schema.org/Thing"]),
    )
    terms = await list_term_access(namespace=_NS, principal=_ANALYST, session=session)
    assert [t.term_iri for t in terms] == [_BASE + "Product"]


@pytest.mark.integration
async def test_GRAPH_句を使ったクエリは印が付く(session: AsyncSession, settings: Settings) -> None:
    """**版の記録が不完全であることを読み手に伝える**(決定7)。"""
    await _setup(session)
    await _record_version(session, "2.0.0")
    await _run(
        session,
        settings,
        store=_Store(),
        query="SELECT ?s WHERE { GRAPH <urn:ontology:graph/access-ns/1.0.0> { ?s ?p ?o } }",
    )
    page = await query_access_log(namespace=_NS, principal=_OWNER, session=session)
    assert page.events[0].used_graph_clause is True


@pytest.mark.integration
async def test_承認済みの版が無ければ版は_None(session: AsyncSession, settings: Settings) -> None:
    await _setup(session)
    await _run(session, settings, store=_Store())
    page = await query_access_log(namespace=_NS, principal=_OWNER, session=session)
    assert page.events[0].default_graph_version is None


@pytest.mark.integration
async def test_記録に失敗してもクエリは成功する(session: AsyncSession, settings: Settings) -> None:
    """**不変条件3 と同じ向きの判断**(ADR-0018 決定3)。

    アクセスログは読み取りの副産物であって、読み取りの前提条件ではない。
    """
    await _setup(session)

    from ontology_api.repositories import access as access_module

    original = access_module.AccessRepository.record

    async def boom(self: AccessRepository, record: object) -> None:
        raise RuntimeError("記録できません")

    access_module.AccessRepository.record = boom  # type: ignore[method-assign]
    try:
        results = await _run(session, settings, store=_Store(results=[_BASE + "A"]))
    finally:
        access_module.AccessRepository.record = original  # type: ignore[method-assign]

    assert results["results"]["bindings"] == [{"s": {"type": "uri", "value": _BASE + "A"}}], (
        "記録の失敗でクエリの結果が変わってはいけない"
    )


@pytest.mark.integration
async def test_廃止済み用語の取得が失敗しても記録は残る(
    session: AsyncSession, settings: Settings
) -> None:
    """`P2B-03` の警告と `P2B-05` の記録は独立していること。"""
    await _setup(session)

    class _Failing(_Store):
        async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
            if "owl#deprecated" in sparql:
                raise SparqlStoreError("到達できません")
            return await super().query(sparql, dataset=dataset)

    await _run(session, settings, store=_Failing(results=[_BASE + "A"]))
    page = await query_access_log(namespace=_NS, principal=_OWNER, session=session)
    assert len(page.events) == 1


# ---------------------------------------------------------------- 権限


@pytest.mark.integration
async def test_イベントの照会には_owner_が必要(session: AsyncSession, settings: Settings) -> None:
    """**同僚の行動の記録である**(決定5)。他のどの口からも導出できないので、
    `P2B-11` の「足し合わせれば見える」論法は当てはまらない。
    """
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await query_access_log(namespace=_NS, principal=_ANALYST, session=session)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_集約は_data_analyst_で読める(session: AsyncSession, settings: Settings) -> None:
    """**個人を特定しない。** 指標を見るために `owner` を要求すると、
    指標が使われなくなる。
    """
    await _setup(session)
    assert await list_term_access(namespace=_NS, principal=_ANALYST, session=session) == []


@pytest.mark.integration
async def test_ロールを持たない主体は集約も読めない(
    session: AsyncSession, settings: Settings
) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await list_term_access(namespace=_NS, principal=_STRANGER, session=session)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_存在しない名前空間は_404(session: AsyncSession, settings: Settings) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await query_access_log(namespace="no-such-ns", principal=_ADMIN, session=session)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------- 照会


@pytest.mark.integration
async def test_主体で絞り込める(session: AsyncSession, settings: Settings) -> None:
    await _setup(session)
    store = _Store()
    await _run(session, settings, store=store, principal=_AGENT)
    await _run(session, settings, store=store, principal=_ANALYST)

    page = await query_access_log(
        namespace=_NS, principal=_OWNER, session=session, actor=_AGENT.object_id
    )
    assert [e.actor for e in page.events] == [_AGENT.object_id]


@pytest.mark.integration
async def test_新しい順に返りページングできる(session: AsyncSession, settings: Settings) -> None:
    await _setup(session)
    store = _Store()
    for i in range(5):
        await _run(session, settings, store=store, query=f"SELECT ?s WHERE {{ ?s ?p ?o }} #{i}")

    seen: list[str] = []
    cursor: int | None = None
    for _ in range(10):
        page = await query_access_log(
            namespace=_NS, principal=_OWNER, session=session, limit=2, cursor=cursor
        )
        seen.extend(e.query_text for e in page.events)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert [t[-2:] for t in seen] == ["#4", "#3", "#2", "#1", "#0"]


@pytest.mark.integration
async def test_上限を超える_limit_は_422(session: AsyncSession, settings: Settings) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await query_access_log(
            namespace=_NS,
            principal=_OWNER,
            session=session,
            limit=AccessRepository.MAX_LIMIT + 1,
        )
    assert exc.value.status_code == 422


@pytest.mark.integration
async def test_タイムゾーンの無い日時は_422(session: AsyncSession, settings: Settings) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await query_access_log(
            namespace=_NS,
            principal=_OWNER,
            session=session,
            # タイムゾーン無しの日時を意図的に渡す。
            since=datetime(2026, 1, 1),
        )
    assert exc.value.status_code == 422


# ---------------------------------------------------------------- 削除


@pytest.mark.integration
async def test_保持期間を過ぎたイベントを削除できる(
    session: AsyncSession, settings: Settings
) -> None:
    await _setup(session)
    await _run(session, settings, store=_Store())
    boundary = datetime.now(UTC) + timedelta(seconds=1)

    result = await purge_access_log(
        namespace=_NS,
        payload=AccessLogPurge(before=boundary, reason="保持期間 30 日の運用ポリシー"),
        principal=_OWNER,
        session=session,
    )
    await session.commit()

    assert result["deleted"] == 1
    page = await query_access_log(namespace=_NS, principal=_OWNER, session=session)
    assert page.events == ()


@pytest.mark.integration
async def test_削除したことが監査に残る(session: AsyncSession, settings: Settings) -> None:
    """**これが「保持期間があること」と「監査可能であること」の両立である**
    (ADR-0018 決定2)。監査証跡の側は追記専用なので、この記録は消えない。
    """
    await _setup(session)
    await _run(session, settings, store=_Store())
    await purge_access_log(
        namespace=_NS,
        payload=AccessLogPurge(
            before=datetime.now(UTC) + timedelta(seconds=1), reason="保持期間の運用"
        ),
        principal=_OWNER,
        session=session,
    )
    await session.commit()

    events = await AuditRepository(session).list_for_subject(_NS, f"{_NS}/access-log")
    assert [e.action for e in events] == ["access-log-purged"]
    assert "1 件を削除" in events[0].reason
    assert "保持期間の運用" in events[0].reason
    assert events[0].actor == _OWNER.object_id


@pytest.mark.integration
async def test_削除しても集約は残る(session: AsyncSession, settings: Settings) -> None:
    """**集約はイベントより長生きしなければならない**(決定1)。

    消した瞬間に「90 日参照されていない」が計算不能になる。
    """
    await _setup(session)
    await _run(session, settings, store=_Store(results=[_BASE + "Product"]))
    await purge_access_log(
        namespace=_NS,
        payload=AccessLogPurge(
            before=datetime.now(UTC) + timedelta(seconds=1), reason="保持期間の運用"
        ),
        principal=_OWNER,
        session=session,
    )
    await session.commit()

    terms = await list_term_access(namespace=_NS, principal=_ANALYST, session=session)
    assert [t.term_iri for t in terms] == [_BASE + "Product"]
    assert terms[0].access_count == 1


@pytest.mark.integration
async def test_境界より後のイベントは消さない(session: AsyncSession, settings: Settings) -> None:
    await _setup(session)
    await _run(session, settings, store=_Store())
    boundary = datetime.now(UTC) - timedelta(hours=1)

    result = await purge_access_log(
        namespace=_NS,
        payload=AccessLogPurge(before=boundary, reason="1 時間前より古いものだけ"),
        principal=_OWNER,
        session=session,
    )
    await session.commit()
    assert result["deleted"] == 0
    page = await query_access_log(namespace=_NS, principal=_OWNER, session=session)
    assert len(page.events) == 1


@pytest.mark.integration
async def test_削除には_owner_が必要(session: AsyncSession, settings: Settings) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await purge_access_log(
            namespace=_NS,
            payload=AccessLogPurge(before=datetime.now(UTC), reason="x"),
            principal=_ANALYST,
            session=session,
        )
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_理由が空なら受け付けない() -> None:
    """**削除の理由は必須である。** 理由の無い削除は監査にならない
    (`reject` の理由を必須にしたのと同じ判断)。
    """
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        AccessLogPurge(before=datetime.now(UTC), reason="")


@pytest.mark.integration
async def test_他の名前空間のイベントは消さない(session: AsyncSession, settings: Settings) -> None:
    """名前空間は隔離の境界である(不変条件5)。"""
    await _setup(session)
    await _run(session, settings, store=_Store())

    await NamespaceRepository(session).create(
        name="other-ns",
        display_name="other",
        description="",
        base_iri="https://e.example/other#",
        created_by=_ADMIN.object_id,
    )
    await session.commit()

    result = await purge_access_log(
        namespace="other-ns",
        payload=AccessLogPurge(
            before=datetime.now(UTC) + timedelta(seconds=1), reason="別の名前空間"
        ),
        principal=_ADMIN,
        session=session,
    )
    await session.commit()
    assert result["deleted"] == 0
    page = await query_access_log(namespace=_NS, principal=_OWNER, session=session)
    assert len(page.events) == 1
