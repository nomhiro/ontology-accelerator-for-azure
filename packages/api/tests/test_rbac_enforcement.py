"""ルータでの RBAC 強制と四眼原則のテスト(P2A-06、ADR-0014)。

**「機構があること」と「強制されていること」は別である。** Phase 1 は
「記録は正しいが強制が無い」状態を意図的に受け入れていた(ADR-0010)。
ここが落ちるのはその状態に戻ったことを意味する。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.routers.namespaces import (
    NamespaceCreate,
    RoleGrant,
    create_namespace,
    delete_namespace,
    grant_namespace_role,
    list_namespace_roles,
    list_namespaces,
    revoke_namespace_role,
)
from ontology_api.routers.versions import (
    PublishRequest,
    approve_version,
    list_versions,
    publish_version,
    submit_version,
)
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.models import NamespaceRole, PlatformRole
from ontology_core.sparql.client import SparqlStore

TTL = "@prefix ex: <https://e.example/#> .\nex:A a ex:Class .\n"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_STEWARD = Principal(subject="steward", object_id="steward-oid")
_MAINTAINER = Principal(subject="maintainer", object_id="maintainer-oid")
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")


class _NullStore(SparqlStore):
    """射影を行わない代役。"""

    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        return {"results": {"bindings": []}}

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


async def _make_namespace(session: AsyncSession, name: str, *, two_person: bool = True) -> None:
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri="https://e.example/#",
        created_by=_ADMIN.object_id,
        require_two_person_approval=two_person,
    )
    repo = RoleRepository(session)
    await repo.grant(
        namespace=name,
        principal_id=_STEWARD.object_id,
        role=NamespaceRole.DATA_STEWARD,
        granted_by=_ADMIN.object_id,
    )
    await repo.grant(
        namespace=name,
        principal_id=_MAINTAINER.object_id,
        role=NamespaceRole.MAINTAINER,
        granted_by=_ADMIN.object_id,
    )
    await repo.grant(
        namespace=name,
        principal_id=_ANALYST.object_id,
        role=NamespaceRole.DATA_ANALYST,
        granted_by=_ADMIN.object_id,
    )
    await session.commit()


# ---- 名前空間の作成 ----


async def test_creating_a_namespace_requires_platform_admin(
    session: AsyncSession, store: SparqlStore | None = None
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await create_namespace(
            payload=NamespaceCreate(
                name="rbac-denied", display_name="x", base_iri="https://e.example/#"
            ),
            principal=_STRANGER,
            session=session,
            store=_NullStore(),
        )
    assert exc_info.value.status_code == 403


async def test_the_creator_becomes_owner(session: AsyncSession) -> None:
    """**作った本人が何もできない名前空間を作らない**(ADR-0014 決定3)。"""
    created = await create_namespace(
        payload=NamespaceCreate(
            name="rbac-created", display_name="x", base_iri="https://e.example/#"
        ),
        principal=_ADMIN,
        session=session,
        store=_NullStore(),
    )
    await session.commit()
    assignments = await RoleRepository(session).list_for(created.name)
    assert [(a.principal_id, a.role) for a in assignments] == [
        (_ADMIN.object_id, NamespaceRole.OWNER)
    ]


async def test_two_person_approval_defaults_to_enabled(session: AsyncSession) -> None:
    """**既定は安全側**(ADR-0014 決定4)。緩めるのは明示的な選択にする。"""
    created = await create_namespace(
        payload=NamespaceCreate(
            name="rbac-default", display_name="x", base_iri="https://e.example/#"
        ),
        principal=_ADMIN,
        session=session,
        store=_NullStore(),
    )
    assert created.require_two_person_approval is True


# ---- 読み取り ----


async def test_list_namespaces_hides_namespaces_without_a_grant(
    session: AsyncSession,
) -> None:
    """権限の無い名前空間は名前も返さない。

    名前だけでも漏れると、どのドメインのオントロジーを持っているかが分かる。
    """
    await _make_namespace(session, "rbac-visible")
    assert [n.name for n in await list_namespaces(_ANALYST, session)] == ["rbac-visible"]
    # 付与が無い主体には空配列(403 ではない。一覧は「見えるものを返す」操作)。
    assert await list_namespaces(_STRANGER, session) == []
    # platform-admin は全件見える。
    assert [n.name for n in await list_namespaces(_ADMIN, session)] == ["rbac-visible"]


async def test_listing_versions_requires_data_analyst(session: AsyncSession) -> None:
    await _make_namespace(session, "rbac-versions")
    await list_versions("rbac-versions", _ANALYST, session)
    with pytest.raises(HTTPException) as exc_info:
        await list_versions("rbac-versions", _STRANGER, session)
    assert exc_info.value.status_code == 403


# ---- 書き込み ----


async def test_publish_requires_data_steward(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _make_namespace(session, "rbac-publish")
    # data-analyst では足りない。
    with pytest.raises(HTTPException) as exc_info:
        await publish_version(
            namespace="rbac-publish",
            payload=PublishRequest(turtle=TTL),
            principal=_ANALYST,
            session=session,
            blob=blob_store,
            store=_NullStore(),
            settings=settings,
            response=Response(),
        )
    assert exc_info.value.status_code == 403
    # data-steward なら通る。
    published = await publish_version(
        namespace="rbac-publish",
        payload=PublishRequest(turtle=TTL),
        principal=_STEWARD,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
        response=Response(),
    )
    assert published.created_by == _STEWARD.object_id


async def test_approve_requires_maintainer(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _make_namespace(session, "rbac-approve")
    store = _NullStore()
    published = await publish_version(
        namespace="rbac-approve",
        payload=PublishRequest(turtle=TTL),
        principal=_STEWARD,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=Response(),
    )
    await submit_version(
        namespace="rbac-approve",
        version=published.version,
        principal=_STEWARD,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    # data-steward は承認できない。
    with pytest.raises(HTTPException) as exc_info:
        await approve_version(
            namespace="rbac-approve",
            version=published.version,
            principal=_STEWARD,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 403
    # maintainer なら通る(かつ publish した主体とは別人なので四眼原則も満たす)。
    approved = await approve_version(
        namespace="rbac-approve",
        version=published.version,
        principal=_MAINTAINER,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    assert approved.approved_by == _MAINTAINER.object_id


# ---- 四眼原則 ----


async def test_the_publisher_cannot_approve_when_two_person_is_enabled(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**publish した主体は approve できない**(ADR-0014 決定4)。

    権限不足(403)ではなく **409** にする。ロールを足しても解決しないので、
    運用者が取るべき対処が違う。
    """
    await _make_namespace(session, "rbac-4eyes", two_person=True)
    await RoleRepository(session).grant(
        namespace="rbac-4eyes",
        principal_id=_MAINTAINER.object_id,
        role=NamespaceRole.OWNER,
        granted_by=_ADMIN.object_id,
    )
    await session.commit()
    store = _NullStore()

    published = await publish_version(
        namespace="rbac-4eyes",
        payload=PublishRequest(turtle=TTL),
        principal=_MAINTAINER,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=Response(),
    )
    await submit_version(
        namespace="rbac-4eyes",
        version=published.version,
        principal=_MAINTAINER,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    with pytest.raises(HTTPException) as exc_info:
        await approve_version(
            namespace="rbac-4eyes",
            version=published.version,
            principal=_MAINTAINER,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 409
    assert "四眼原則" in str(exc_info.value.detail)


async def test_platform_admin_cannot_bypass_two_person_approval(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**`platform-admin` も四眼原則は飛び越えられない**(ADR-0014 決定5)。

    管理者が自分の提案を自分で承認できてしまうと、四眼原則が
    「管理者以外への制約」に成り下がり、規制対応の文脈で意味を失う。
    """
    await _make_namespace(session, "rbac-admin-4eyes", two_person=True)
    store = _NullStore()
    published = await publish_version(
        namespace="rbac-admin-4eyes",
        payload=PublishRequest(turtle=TTL),
        principal=_ADMIN,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=Response(),
    )
    await submit_version(
        namespace="rbac-admin-4eyes",
        version=published.version,
        principal=_ADMIN,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    with pytest.raises(HTTPException) as exc_info:
        await approve_version(
            namespace="rbac-admin-4eyes",
            version=published.version,
            principal=_ADMIN,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 409


async def test_the_publisher_can_approve_when_two_person_is_disabled(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """無効にした名前空間では 1 人で承認できる(同梱サンプルの経路)。"""
    await _make_namespace(session, "rbac-no-4eyes", two_person=False)
    await RoleRepository(session).grant(
        namespace="rbac-no-4eyes",
        principal_id=_MAINTAINER.object_id,
        role=NamespaceRole.OWNER,
        granted_by=_ADMIN.object_id,
    )
    await session.commit()
    store = _NullStore()
    published = await publish_version(
        namespace="rbac-no-4eyes",
        payload=PublishRequest(turtle=TTL),
        principal=_MAINTAINER,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=Response(),
    )
    await submit_version(
        namespace="rbac-no-4eyes",
        version=published.version,
        principal=_MAINTAINER,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    approved = await approve_version(
        namespace="rbac-no-4eyes",
        version=published.version,
        principal=_MAINTAINER,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    assert approved.approved_by == _MAINTAINER.object_id


# ---- ロールの管理 ----


async def test_role_management_requires_owner(session: AsyncSession) -> None:
    await _make_namespace(session, "rbac-roles")
    with pytest.raises(HTTPException) as exc_info:
        await list_namespace_roles("rbac-roles", _MAINTAINER, session)
    assert exc_info.value.status_code == 403
    with pytest.raises(HTTPException):
        await grant_namespace_role(
            "rbac-roles",
            RoleGrant(principal_id="x", role=NamespaceRole.DATA_ANALYST),
            _MAINTAINER,
            session,
        )
    # owner(= platform-admin)なら通る。
    assignments = await list_namespace_roles("rbac-roles", _ADMIN, session)
    assert len(assignments) == 3


async def test_the_last_owner_cannot_be_revoked(session: AsyncSession) -> None:
    """取り消した結果、誰もその名前空間を管理できなくなる事故を防ぐ。"""
    created = await create_namespace(
        payload=NamespaceCreate(
            name="rbac-last-owner", display_name="x", base_iri="https://e.example/#"
        ),
        principal=_ADMIN,
        session=session,
        store=_NullStore(),
    )
    await session.commit()
    with pytest.raises(HTTPException) as exc_info:
        await revoke_namespace_role(created.name, _ADMIN.object_id, _ADMIN, session)
    assert exc_info.value.status_code == 409
    # 別の owner を足せば取り消せる。
    await grant_namespace_role(
        created.name,
        RoleGrant(principal_id="second-owner", role=NamespaceRole.OWNER),
        _ADMIN,
        session,
    )
    await session.commit()
    await revoke_namespace_role(created.name, _ADMIN.object_id, _ADMIN, session)
    await session.commit()
    remaining = await RoleRepository(session).list_for(created.name)
    assert [a.principal_id for a in remaining] == ["second-owner"]


async def test_deleting_a_namespace_requires_owner(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _make_namespace(session, "rbac-delete")
    with pytest.raises(HTTPException) as exc_info:
        await delete_namespace("rbac-delete", _MAINTAINER, session, _NullStore(), blob_store)
    assert exc_info.value.status_code == 403
