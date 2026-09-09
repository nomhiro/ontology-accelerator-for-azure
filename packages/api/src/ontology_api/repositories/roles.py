"""`namespace_roles` へのアクセス(ADR-0014、P2A-06)。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.db import NamespaceRoleRow
from ontology_core.models import NamespaceRole, NamespaceRoleAssignment

__all__ = ["RoleRepository"]


def _to_model(row: NamespaceRoleRow) -> NamespaceRoleAssignment:
    return NamespaceRoleAssignment(
        namespace=row.namespace,
        principal_id=row.principal_id,
        role=NamespaceRole(row.role),
        granted_at=row.granted_at,
        granted_by=row.granted_by,
    )


class RoleRepository:
    """名前空間ごとのロール付与の読み書き。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def grant(
        self, *, namespace: str, principal_id: str, role: NamespaceRole, granted_by: str
    ) -> NamespaceRoleAssignment:
        """付与する。既にあれば置き換える(冪等)。

        1 人が 1 つの名前空間に持つロールは 1 つなので(ADR-0014 決定1)、
        付与のやり直しは「昇格・降格」であり、重複エラーにする理由がない。
        """
        stmt = select(NamespaceRoleRow).where(
            NamespaceRoleRow.namespace == namespace,
            NamespaceRoleRow.principal_id == principal_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = NamespaceRoleRow(
                namespace=namespace,
                principal_id=principal_id,
                role=role.value,
                granted_by=granted_by,
            )
            self._session.add(row)
        else:
            row.role = role.value
            row.granted_by = granted_by
        await self._session.flush()
        await self._session.refresh(row)
        return _to_model(row)

    async def revoke(self, *, namespace: str, principal_id: str) -> bool:
        """取り消す。取り消す対象があったかを返す。

        `rowcount` は `CursorResult` にしか無く、`execute()` の戻り値の型は
        それより広い。**存在確認してから削除する**(1 クエリ増えるが、型を
        無視する `cast` を書かずに済み、意図も読みやすい)。
        """
        stmt = select(NamespaceRoleRow).where(
            NamespaceRoleRow.namespace == namespace,
            NamespaceRoleRow.principal_id == principal_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def list_for(self, namespace: str) -> list[NamespaceRoleAssignment]:
        """この名前空間の付与を一覧する。順序は principal_id で安定させる。"""
        stmt = (
            select(NamespaceRoleRow)
            .where(NamespaceRoleRow.namespace == namespace)
            .order_by(NamespaceRoleRow.principal_id)
        )
        return [_to_model(r) for r in (await self._session.execute(stmt)).scalars()]

    async def count_with_at_least(self, *, namespace: str, role: NamespaceRole) -> int:
        """指定ロール以上を持つ主体の数を返す。

        `owner` を最後の 1 人まで取り消してしまう事故を防ぐために使う
        (取り消した結果、誰もその名前空間を管理できなくなる)。
        """
        rows = await self.list_for(namespace)
        return sum(1 for r in rows if r.role.covers(role))
