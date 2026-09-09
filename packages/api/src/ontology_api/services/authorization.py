"""名前空間ごとの権限判定(ADR-0014、P2A-06)。

設計原則「名前空間 x ロールは PostgreSQL で管理し API で強制する」の実装。
Entra のアプリロールは粗粒度(`platform-admin` か否か)に留め、細粒度は
このモジュールが判定する。

## 暗黙のフォールバックを作らない

付与が 1 件も無い名前空間は「誰も権限を持たない」として扱う(ADR-0014 決定6)。
「付与が無ければ全員に許可」のようなフォールバックは、**「強制していない」を
「強制している」と誤認させる**、この製品で最も避けたい形である。既存のデプロイ
のための移行はマイグレーション(`0002_namespace_rbac`)で明示的に行っている。

## `platform-admin` は名前空間の権限を飛び越える。四眼原則は飛び越えない

運用者がロックアウトから回復する経路が必要なので、`platform-admin` は
すべての名前空間で `owner` として扱う。**しかし四眼原則は飛び越えられない**
(ADR-0014 決定5)。管理者が自分の提案を自分で承認できてしまうと、四眼原則が
「管理者以外への制約」に成り下がり、規制対応の文脈で意味を失う。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.auth.entra import Principal
from ontology_core.db import NamespaceRoleRow
from ontology_core.models import NamespaceRole, PlatformRole

__all__ = [
    "PermissionDeniedError",
    "TwoPersonApprovalError",
    "effective_role",
    "principal_id_of",
    "require_namespace_role",
    "require_platform_admin",
]


class PermissionDeniedError(Exception):
    """この主体はこの操作を行う権限を持たない。呼び出し元は 403 に対応させる。"""


class TwoPersonApprovalError(Exception):
    """四眼原則により、この主体はこの版を承認できない(ADR-0014 決定4)。

    `PermissionDeniedError` と分けているのは、**運用者が取るべき対処が違う**
    ため。権限不足ならロールを付与すればよいが、四眼原則違反は「別の人に
    承認してもらう」しかない。同じ 403 に混ぜると、ロールを足して解決しようと
    して解決しない。
    """


def principal_id_of(principal: Principal) -> str:
    """付与の突き合わせに使う識別子を返す。

    **オブジェクト ID を使う**(ADR-0014 決定1)。UPN や表示名は変わりうるし、
    ゲストの `#EXT#` 形式は書き換えの罠がある(`P1-11` で実際に踏んだ)。
    `object_id` が無いトークン(稀)では `subject` に落とす — これは
    `created_by` / `approved_by` の記録に使っている値と同じ規則である。
    """
    return principal.object_id or principal.subject


def _is_platform_admin(principal: Principal) -> bool:
    return PlatformRole.PLATFORM_ADMIN.value in principal.platform_roles


async def effective_role(
    session: AsyncSession, *, namespace: str, principal: Principal
) -> NamespaceRole | None:
    """この主体がこの名前空間で持つ実効ロールを返す。無ければ `None`。

    `platform-admin` はすべての名前空間で `owner` として扱う。
    """
    if _is_platform_admin(principal):
        return NamespaceRole.OWNER
    stmt = select(NamespaceRoleRow.role).where(
        NamespaceRoleRow.namespace == namespace,
        NamespaceRoleRow.principal_id == principal_id_of(principal),
    )
    value = (await session.execute(stmt)).scalar_one_or_none()
    if value is None:
        return None
    try:
        return NamespaceRole(value)
    except ValueError:
        # DB に未知のロール文字列が入っている。**権限ありと解釈しない。**
        # 「読めない付与」を「上位ロール」と誤解すると権限が広がる。
        return None


async def require_namespace_role(
    session: AsyncSession, *, namespace: str, principal: Principal, required: NamespaceRole
) -> NamespaceRole:
    """必要なロールを満たしているか確認し、実効ロールを返す。

    Raises:
        PermissionDeniedError: 満たしていないとき。
    """
    role = await effective_role(session, namespace=namespace, principal=principal)
    if role is None or not role.covers(required):
        held = role.value if role is not None else "なし"
        raise PermissionDeniedError(
            f"名前空間 '{namespace}' に対する操作には '{required.value}' 以上が必要です"
            f"(現在のロール: {held})"
        )
    return role


def require_platform_admin(principal: Principal) -> None:
    """`platform-admin` を要求する。

    Raises:
        PermissionDeniedError: 持っていないとき。
    """
    if not _is_platform_admin(principal):
        raise PermissionDeniedError(
            f"この操作には '{PlatformRole.PLATFORM_ADMIN.value}' が必要です"
        )
