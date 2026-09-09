"""`term_owners` へのアクセスと、問い合わせ先の解決(ADR-0015、`P2B-04`)。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.db import NamespaceRoleRow, TermOwnerRow
from ontology_core.models import (
    NamespaceRole,
    OwnerResolution,
    OwnerResolutionSource,
    TermOwner,
)

__all__ = ["TermOwnerRepository"]


def _to_model(row: TermOwnerRow) -> TermOwner:
    return TermOwner(
        namespace=row.namespace,
        term_iri=row.term_iri,
        principal_id=row.principal_id,
        assigned_at=row.assigned_at,
        assigned_by=row.assigned_by,
    )


class TermOwnerRepository:
    """用語単位の責任者の読み書きと、問い合わせ先の解決。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def assign(
        self, *, namespace: str, term_iri: str, principal_id: str, assigned_by: str
    ) -> TermOwner:
        """責任者を割り当てる。既にあれば置き換える(冪等)。

        1 つの用語に責任者は 1 人なので(ADR-0015 決定1)、割り当てのやり直しは
        「付け替え」であり重複エラーにする理由がない。
        """
        stmt = select(TermOwnerRow).where(
            TermOwnerRow.namespace == namespace,
            TermOwnerRow.term_iri == term_iri,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = TermOwnerRow(
                namespace=namespace,
                term_iri=term_iri,
                principal_id=principal_id,
                assigned_by=assigned_by,
            )
            self._session.add(row)
        else:
            row.principal_id = principal_id
            row.assigned_by = assigned_by
        await self._session.flush()
        await self._session.refresh(row)
        return _to_model(row)

    async def unassign(self, *, namespace: str, term_iri: str) -> bool:
        """責任者を外す。外す対象があったかを返す。

        `RoleRepository.revoke` と同じく**存在確認してから削除する**
        (`rowcount` は `CursorResult` にしか無く、`execute()` の宣言型は
        それより広い)。
        """
        stmt = select(TermOwnerRow).where(
            TermOwnerRow.namespace == namespace,
            TermOwnerRow.term_iri == term_iri,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def get(self, *, namespace: str, term_iri: str) -> TermOwner | None:
        """1 件取得する。無ければ `None`。"""
        stmt = select(TermOwnerRow).where(
            TermOwnerRow.namespace == namespace,
            TermOwnerRow.term_iri == term_iri,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _to_model(row)

    async def list_for(self, namespace: str) -> list[TermOwner]:
        """この名前空間の責任者を一覧する。順序は用語 IRI で安定させる。"""
        stmt = (
            select(TermOwnerRow)
            .where(TermOwnerRow.namespace == namespace)
            .order_by(TermOwnerRow.term_iri)
        )
        return [_to_model(r) for r in (await self._session.execute(stmt)).scalars()]

    async def resolve(self, *, namespace: str, term_iri: str) -> OwnerResolution:
        """「この用語は誰に聞けばよいか」を解決する(ADR-0015 決定2)。

        用語の責任者 → 名前空間の `owner` → 解決不能 の順に落ちる。

        **フォールバックすることを隠さない。** `source` に何で解決したかを
        入れる。見えなければ `P2B-06` の健全性指標が「責任者が未設定の用語」を
        数えられない。

        **権限のフォールバックを禁じた ADR-0014 決定6 に反しない。** 安全側の
        向きが逆である — 権限は「無いなら拒否」が安全側だが、ルーティングは
        「無いなら上位に回す」が安全側である(誰にも届かない問い合わせは
        放置され、放置されたことも分からない)。
        """
        owner = await self.get(namespace=namespace, term_iri=term_iri)
        if owner is not None:
            return OwnerResolution(
                namespace=namespace,
                term_iri=term_iri,
                source=OwnerResolutionSource.TERM_OWNER,
                principal_ids=(owner.principal_id,),
            )

        # 名前空間の `owner` ロール保持者へ回す。**`platform-admin` は含めない** —
        # トークンのクレームで決まるものであり、DB からは列挙できない。
        # 運用者がロックアウトから回復する経路(ADR-0014 決定5)であって、
        # 日常の問い合わせ先ではない。
        stmt = (
            select(NamespaceRoleRow.principal_id)
            .where(
                NamespaceRoleRow.namespace == namespace,
                NamespaceRoleRow.role == NamespaceRole.OWNER.value,
            )
            .order_by(NamespaceRoleRow.principal_id)
        )
        owners = tuple((await self._session.execute(stmt)).scalars())
        if owners:
            return OwnerResolution(
                namespace=namespace,
                term_iri=term_iri,
                source=OwnerResolutionSource.NAMESPACE_OWNERS,
                principal_ids=owners,
            )
        return OwnerResolution(
            namespace=namespace,
            term_iri=term_iri,
            source=OwnerResolutionSource.UNRESOLVED,
        )
