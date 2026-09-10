"""想定質問の集合の読み書き(ADR-0022、`P2B-14`)。

**改訂は不変である。** 書き換える口を持たない — `add` だけがある。
`update` を足したくなったら ADR-0022 決定2 を読み直すこと(受け入れ基準を
後から書き換えられるなら、テストが通ったことは保証にならない)。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.competency import content_hash
from ontology_core.db import CompetencyQuestionSetRow
from ontology_core.models import QuestionSet, QuestionSetSummary

__all__ = ["QuestionSetRepository"]


def _to_model(row: CompetencyQuestionSetRow) -> QuestionSet:
    return QuestionSet(
        namespace=row.namespace,
        revision=row.revision,
        content=row.content,
        content_hash=row.content_hash,
        question_count=row.question_count,
        created_at=row.created_at,
        created_by=row.created_by,
        reason=row.reason,
    )


class QuestionSetRepository:
    """想定質問の集合(名前空間ごとの不変改訂)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active(self, namespace: str) -> QuestionSet | None:
        """有効な改訂(最大の `revision`)を返す。無ければ `None`。

        **`None` は「基準を定めていない」であって「基準を満たしていない」
        ではない**(ADR-0022 決定7)。呼び出し側でこの区別を潰さないこと。
        """
        stmt = (
            select(CompetencyQuestionSetRow)
            .where(CompetencyQuestionSetRow.namespace == namespace)
            .order_by(CompetencyQuestionSetRow.revision.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _to_model(row)

    async def revisions(self, namespace: str) -> list[QuestionSetSummary]:
        """改訂の一覧を新しい順に返す(本文は含まない)。

        **基準がいつ・誰に・なぜ変えられたかの履歴そのものである。**
        ADR-0022 決定6 の「防止ではなく可視化」がここで効く。
        """
        stmt = (
            select(CompetencyQuestionSetRow)
            .where(CompetencyQuestionSetRow.namespace == namespace)
            .order_by(CompetencyQuestionSetRow.revision.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_model(row).summary() for row in rows]

    async def add(
        self, *, namespace: str, content: str, question_count: int, actor: str, reason: str
    ) -> QuestionSet:
        """新しい改訂を足す。

        **`revision` は「その名前空間の最大 + 1」である。** 主キー(`id`)の
        順序には頼らない — `id` は全名前空間で共有の連番なので、名前空間内の
        「何番目の改訂か」を表さない。

        **同一トランザクションで監査記録を書けることが、このテーブルを
        PostgreSQL に置いた理由である**(ADR-0022 決定1)。commit は
        呼び出し側が行う。
        """
        stmt = select(func.max(CompetencyQuestionSetRow.revision)).where(
            CompetencyQuestionSetRow.namespace == namespace
        )
        current = (await self._session.execute(stmt)).scalar_one_or_none()
        row = CompetencyQuestionSetRow(
            namespace=namespace,
            revision=(current or 0) + 1,
            content=content,
            content_hash=content_hash(content),
            question_count=question_count,
            created_by=actor,
            reason=reason,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _to_model(row)

    async def count_questions(self, namespace: str) -> int:
        """有効な改訂の質問の件数。質問集合が無ければ 0。

        **健全性指標のために本文を解析しない**(`question_count` を列に持って
        いる理由)。指標が本文の解析に依存すると、壊れた質問集合が入っている
        名前空間で指標そのものが落ちる。
        """
        stmt = (
            select(CompetencyQuestionSetRow.question_count)
            .where(CompetencyQuestionSetRow.namespace == namespace)
            .order_by(CompetencyQuestionSetRow.revision.desc())
            .limit(1)
        )
        value = (await self._session.execute(stmt)).scalar_one_or_none()
        return int(value or 0)
