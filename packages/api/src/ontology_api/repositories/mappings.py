"""領域間マッピングの読み書き(ADR-0023、`P2B-10`)。

**逆向きのマッピングを作る口は持たない**(決定3)。`declare` は張った側の
名前空間にしか行を作らない。相手側の宣言は `_counterparts` で**突き合わせる
だけ**である。

`update` を足したくなったら決定3 を読み直すこと — 相手の名前空間の行を
書き換える操作を作ると、相手が宣言していない主張を作れてしまう。
"""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.db import TermMappingRow
from ontology_core.mapping import (
    MappingPredicate,
    compare_with_counterpart,
    predicate_iri,
)
from ontology_core.models import TermMapping

__all__ = ["MappingRepository"]


class MappingRepository:
    """領域間マッピング。始点の名前空間に属する。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def declare(
        self,
        *,
        namespace: str,
        source_term: str,
        target_term: str,
        predicate: MappingPredicate,
        reason: str,
        actor: str,
    ) -> None:
        """マッピングを宣言する。同じ用語ペアが既にあれば付け替える。

        **冪等である。** 1 つの用語ペアに述語は 1 つなので(テーブルの一意
        制約)、宣言のやり直しは付け替えであり重複エラーにする理由がない
        (`TermOwnerRepository.assign` と同じ形)。

        commit は呼び出し側が行う(監査記録と同一トランザクションにするため)。
        """
        stmt = select(TermMappingRow).where(
            TermMappingRow.namespace == namespace,
            TermMappingRow.source_term == source_term,
            TermMappingRow.target_term == target_term,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            self._session.add(
                TermMappingRow(
                    namespace=namespace,
                    source_term=source_term,
                    target_term=target_term,
                    predicate=predicate.value,
                    reason=reason,
                    declared_by=actor,
                )
            )
        else:
            row.predicate = predicate.value
            row.reason = reason
            row.declared_by = actor
        await self._session.flush()

    async def revoke(self, *, namespace: str, source_term: str, target_term: str) -> bool:
        """マッピングを取り消す。存在しなければ `False`。

        存在確認してから削除するのは `session.execute()` の戻り値に
        `rowcount` が無いためである(CLAUDE.md に記録済み。
        `RoleRepository.revoke` と同じ形)。
        """
        stmt = select(TermMappingRow).where(
            TermMappingRow.namespace == namespace,
            TermMappingRow.source_term == source_term,
            TermMappingRow.target_term == target_term,
        )
        if (await self._session.execute(stmt)).scalar_one_or_none() is None:
            return False
        await self._session.execute(
            delete(TermMappingRow).where(
                TermMappingRow.namespace == namespace,
                TermMappingRow.source_term == source_term,
                TermMappingRow.target_term == target_term,
            )
        )
        return True

    async def outgoing(self, namespace: str) -> list[TermMapping]:
        """その名前空間が**張った**マッピング。"""
        stmt = (
            select(TermMappingRow)
            .where(TermMappingRow.namespace == namespace)
            .order_by(TermMappingRow.source_term, TermMappingRow.target_term)
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        return await self._decorate(rows)

    async def incoming(self, namespace: str, *, base_iri: str) -> list[TermMapping]:
        """その名前空間の用語へ**他の名前空間から張られた**マッピング。

        **これが無いと相手の主張に気づけない**(ADR-0023 決定3)。逆向きを
        自動生成しない代わりに、両方向から見えるようにしている。

        判定は `target_term` が `base_iri` で始まるかで行う。**自分自身が
        張ったものは除く**(それは `outgoing` である)。
        """
        stmt = (
            select(TermMappingRow)
            .where(
                TermMappingRow.target_term.startswith(base_iri),
                TermMappingRow.namespace != namespace,
            )
            .order_by(TermMappingRow.target_term, TermMappingRow.source_term)
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        return await self._decorate(rows)

    async def disputed_count(self, namespace: str) -> int:
        """述語が食い違っているマッピングの数(健全性指標。ADR-0023 決定4)。

        その名前空間が**張った**マッピングのうち、相手側が逆向きに宣言して
        いて述語が食い違っているものを数える。
        """
        return sum(1 for mapping in await self.outgoing(namespace) if mapping.disputed)

    async def _decorate(self, rows: list[TermMappingRow]) -> list[TermMapping]:
        """相手側の宣言と突き合わせて `reciprocal` / `disputed` を付ける。"""
        counterparts = await self._counterparts(rows)
        result: list[TermMapping] = []
        for row in rows:
            predicate = MappingPredicate(row.predicate)
            counterpart = counterparts.get((row.target_term, row.source_term))
            compared = compare_with_counterpart(predicate, counterpart)
            result.append(
                TermMapping(
                    namespace=row.namespace,
                    source_term=row.source_term,
                    target_term=row.target_term,
                    predicate=predicate.value,
                    predicate_iri=predicate_iri(predicate),
                    reason=row.reason,
                    declared_by=row.declared_by,
                    declared_at=row.declared_at,
                    reciprocal=compared.reciprocal,
                    disputed=compared.disputed,
                    counterpart_predicate=(
                        None if compared.counterpart is None else compared.counterpart.value
                    ),
                )
            )
        return result

    async def _counterparts(
        self, rows: list[TermMappingRow]
    ) -> dict[tuple[str, str], MappingPredicate]:
        """逆向き(`target` → `source`)に宣言されている述語を 1 クエリで引く。

        **行ごとに引かない。** 一覧の件数だけクエリが飛ぶと、マッピングが
        増えたときに一覧が使えなくなる。
        """
        if not rows:
            return {}
        conditions = [
            (TermMappingRow.source_term == row.target_term)
            & (TermMappingRow.target_term == row.source_term)
            for row in rows
        ]
        stmt = select(
            TermMappingRow.source_term, TermMappingRow.target_term, TermMappingRow.predicate
        ).where(or_(*conditions))
        found: dict[tuple[str, str], MappingPredicate] = {}
        for source, target, predicate in (await self._session.execute(stmt)).all():
            found[(source, target)] = MappingPredicate(predicate)
        return found
