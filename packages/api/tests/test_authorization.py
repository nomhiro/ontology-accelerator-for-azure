"""名前空間 RBAC の判定のテスト(P2A-06、ADR-0014)。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.services.authorization import (
    PermissionDeniedError,
    effective_role,
    principal_id_of,
    require_namespace_role,
    require_platform_admin,
)
from ontology_core.auth.entra import Principal
from ontology_core.models import NamespaceRole, PlatformRole

_NS = "rbac-ns"


def _user(oid: str, *, platform_roles: tuple[str, ...] = ()) -> Principal:
    return Principal(subject=f"sub-{oid}", object_id=oid, platform_roles=platform_roles)


@pytest.fixture
async def namespace(session: AsyncSession) -> str:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri="https://e.example/#",
        created_by="creator-oid",
    )
    await session.commit()
    return _NS


# ---- ロールの順序 ----


def test_higher_roles_cover_lower_ones() -> None:
    """上位は下位のすべてを含む(ADR-0014 決定2)。"""
    assert NamespaceRole.OWNER.covers(NamespaceRole.DATA_ANALYST)
    assert NamespaceRole.MAINTAINER.covers(NamespaceRole.DATA_STEWARD)
    assert NamespaceRole.DATA_STEWARD.covers(NamespaceRole.DATA_ANALYST)
    assert not NamespaceRole.DATA_STEWARD.covers(NamespaceRole.MAINTAINER)
    assert not NamespaceRole.DATA_ANALYST.covers(NamespaceRole.DATA_STEWARD)
    # 自分自身は満たす。
    for role in NamespaceRole:
        assert role.covers(role)


# ---- 実効ロール ----


async def test_no_grant_means_no_role(session: AsyncSession, namespace: str) -> None:
    """**付与が無ければ権限は無い。** 暗黙のフォールバックを作らない。

    「付与が無ければ全員に許可」は、「強制していない」を「強制している」と
    誤認させる(ADR-0014 決定6)。
    """
    assert await effective_role(session, namespace=namespace, principal=_user("nobody")) is None


async def test_granted_role_is_returned(session: AsyncSession, namespace: str) -> None:
    await RoleRepository(session).grant(
        namespace=namespace,
        principal_id="alice",
        role=NamespaceRole.MAINTAINER,
        granted_by="creator-oid",
    )
    await session.commit()
    role = await effective_role(session, namespace=namespace, principal=_user("alice"))
    assert role is NamespaceRole.MAINTAINER


async def test_platform_admin_is_owner_everywhere(session: AsyncSession, namespace: str) -> None:
    """`platform-admin` はロックアウトからの回復経路(ADR-0014 決定5)。"""
    admin = _user("admin", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,))
    role = await effective_role(session, namespace=namespace, principal=admin)
    assert role is NamespaceRole.OWNER


async def test_an_unknown_role_string_is_not_treated_as_permission(
    session: AsyncSession, namespace: str
) -> None:
    """DB に未知のロール文字列があっても**権限ありと解釈しない**。

    「読めない付与」を「上位ロール」と誤解すると権限が広がる。
    """
    repo = RoleRepository(session)
    await repo.grant(
        namespace=namespace,
        principal_id="weird",
        role=NamespaceRole.OWNER,
        granted_by="creator-oid",
    )
    await session.commit()
    # 直接書き換えて未知の値にする。
    from sqlalchemy import update

    from ontology_core.db import NamespaceRoleRow

    await session.execute(
        update(NamespaceRoleRow)
        .where(NamespaceRoleRow.principal_id == "weird")
        .values(role="super-duper-admin")
    )
    await session.commit()
    assert await effective_role(session, namespace=namespace, principal=_user("weird")) is None


# ---- 要求の判定 ----


async def test_require_namespace_role_allows_equal_or_higher(
    session: AsyncSession, namespace: str
) -> None:
    await RoleRepository(session).grant(
        namespace=namespace,
        principal_id="bob",
        role=NamespaceRole.MAINTAINER,
        granted_by="creator-oid",
    )
    await session.commit()
    bob = _user("bob")
    # 同等・下位は通る。
    assert (
        await require_namespace_role(
            session, namespace=namespace, principal=bob, required=NamespaceRole.MAINTAINER
        )
        is NamespaceRole.MAINTAINER
    )
    await require_namespace_role(
        session, namespace=namespace, principal=bob, required=NamespaceRole.DATA_ANALYST
    )
    # 上位は通らない。
    with pytest.raises(PermissionDeniedError) as exc_info:
        await require_namespace_role(
            session, namespace=namespace, principal=bob, required=NamespaceRole.OWNER
        )
    # 何が足りないかが分かるメッセージであること。
    assert "owner" in str(exc_info.value)
    assert "maintainer" in str(exc_info.value)


async def test_require_namespace_role_without_a_grant_is_denied(
    session: AsyncSession, namespace: str
) -> None:
    with pytest.raises(PermissionDeniedError) as exc_info:
        await require_namespace_role(
            session,
            namespace=namespace,
            principal=_user("stranger"),
            required=NamespaceRole.DATA_ANALYST,
        )
    assert "なし" in str(exc_info.value)


def test_require_platform_admin() -> None:
    require_platform_admin(_user("a", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)))
    with pytest.raises(PermissionDeniedError):
        require_platform_admin(_user("a"))
    # 別のプラットフォームロールでは通らない。
    with pytest.raises(PermissionDeniedError):
        require_platform_admin(_user("a", platform_roles=(PlatformRole.PLATFORM_VIEWER.value,)))


def test_principal_id_prefers_the_object_id() -> None:
    """付与の突き合わせはオブジェクト ID で行う(ADR-0014 決定1)。"""
    assert principal_id_of(Principal(subject="s", object_id="oid")) == "oid"
    # object_id が無いトークンでは subject に落ちる(created_by と同じ規則)。
    assert principal_id_of(Principal(subject="s")) == "s"


# ---- 付与の読み書き ----


async def test_grant_is_idempotent_and_replaces_the_role(
    session: AsyncSession, namespace: str
) -> None:
    """付与のやり直しは昇格・降格であり、重複エラーにしない。"""
    repo = RoleRepository(session)
    await repo.grant(
        namespace=namespace,
        principal_id="carol",
        role=NamespaceRole.DATA_ANALYST,
        granted_by="creator-oid",
    )
    assignment = await repo.grant(
        namespace=namespace,
        principal_id="carol",
        role=NamespaceRole.OWNER,
        granted_by="creator-oid",
    )
    await session.commit()
    assert assignment.role is NamespaceRole.OWNER
    assert len(await repo.list_for(namespace)) == 1


async def test_revoke_reports_whether_there_was_something_to_revoke(
    session: AsyncSession, namespace: str
) -> None:
    repo = RoleRepository(session)
    await repo.grant(
        namespace=namespace,
        principal_id="dave",
        role=NamespaceRole.MAINTAINER,
        granted_by="creator-oid",
    )
    await session.commit()
    assert await repo.revoke(namespace=namespace, principal_id="dave") is True
    await session.commit()
    assert await repo.revoke(namespace=namespace, principal_id="dave") is False


async def test_count_with_at_least_counts_higher_roles_too(
    session: AsyncSession, namespace: str
) -> None:
    """`owner` を最後の 1 人まで取り消す事故を防ぐための数え方。"""
    repo = RoleRepository(session)
    await repo.grant(
        namespace=namespace, principal_id="o1", role=NamespaceRole.OWNER, granted_by="x"
    )
    await repo.grant(
        namespace=namespace, principal_id="m1", role=NamespaceRole.MAINTAINER, granted_by="x"
    )
    await session.commit()
    assert await repo.count_with_at_least(namespace=namespace, role=NamespaceRole.OWNER) == 1
    assert await repo.count_with_at_least(namespace=namespace, role=NamespaceRole.MAINTAINER) == 2
