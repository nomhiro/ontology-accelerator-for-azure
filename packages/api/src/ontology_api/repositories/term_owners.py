"""`term_owners` へのアクセスと、問い合わせ先の解決(ADR-0015、`P2B-04`)。"""

from __future__ import annotations

from collections.abc import Sequence

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

        **`resolve_many` に委譲する**
        ([ADR-0040](../../../../../docs/adr/0040-term-owner-not-an-approval-gate.md)
        決定5)。**規則を 2 か所に書かない** — フォールバックの順序が片方だけ
        変わると、1 件で引いたときと差分で見たときで問い合わせ先が違うという
        静かな不整合になる。
        """
        resolved = await self.resolve_many(namespace=namespace, term_iris=(term_iri,))
        return resolved[term_iri]

    async def resolve_many(
        self, *, namespace: str, term_iris: Sequence[str]
    ) -> dict[str, OwnerResolution]:
        """複数の用語の問い合わせ先をまとめて解決する(ADR-0040 決定5)。

        **クエリは 2 回で、用語数に依らない。** 1 回目で `term_owners` の該当行を、
        2 回目で名前空間の `owner` を引く。用語ごとに問い合わせると、差分に
        50 用語あれば最大 100 クエリになる。

        **返る辞書には渡した全 IRI が入る。** 解決できなかったものも
        `unresolved` として入る — 欠落させると、呼び出し側が「載っていない
        ものは問題なし」と読む(`resolve_target_lifecycles` と同じ契約)。

        Returns:
            用語 IRI から `OwnerResolution` への辞書。
        """
        wanted = list(dict.fromkeys(term_iris))
        if not wanted:
            return {}

        rows = (
            await self._session.execute(
                select(TermOwnerRow).where(
                    TermOwnerRow.namespace == namespace,
                    TermOwnerRow.term_iri.in_(wanted),
                )
            )
        ).scalars()
        by_term = {row.term_iri: row.principal_id for row in rows}

        # 名前空間の `owner` ロール保持者へ回す。**`platform-admin` は含めない** —
        # トークンのクレームで決まるものであり、DB からは列挙できない。
        # 運用者がロックアウトから回復する経路(ADR-0014 決定5)であって、
        # 日常の問い合わせ先ではない。
        #
        # **用語の責任者が全員そろっていても引く。** 引かないと、
        # 「責任者がいる用語だけを渡したとき」と「いない用語が混ざったとき」で
        # クエリ回数が変わる(測りにくくなる)。1 回は定数である。
        stmt = (
            select(NamespaceRoleRow.principal_id)
            .where(
                NamespaceRoleRow.namespace == namespace,
                NamespaceRoleRow.role == NamespaceRole.OWNER.value,
            )
            .order_by(NamespaceRoleRow.principal_id)
        )
        fallback = tuple((await self._session.execute(stmt)).scalars())

        result: dict[str, OwnerResolution] = {}
        for iri in wanted:
            if (principal_id := by_term.get(iri)) is not None:
                result[iri] = OwnerResolution(
                    namespace=namespace,
                    term_iri=iri,
                    source=OwnerResolutionSource.TERM_OWNER,
                    principal_ids=(principal_id,),
                )
            elif fallback:
                result[iri] = OwnerResolution(
                    namespace=namespace,
                    term_iri=iri,
                    source=OwnerResolutionSource.NAMESPACE_OWNERS,
                    principal_ids=fallback,
                )
            else:
                result[iri] = OwnerResolution(
                    namespace=namespace,
                    term_iri=iri,
                    source=OwnerResolutionSource.UNRESOLVED,
                )
        return result
