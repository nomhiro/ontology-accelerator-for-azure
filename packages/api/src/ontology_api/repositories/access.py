"""アクセスログの読み書き(ADR-0018、`P2B-05`)。

## 2 つのテーブルを 1 つのトランザクションで書く

`access_events`(イベント)と `term_access`(集約)は用途が違うが、**同じ
クエリから作られる**。片方だけ書かれた状態を作らないため、同じセッションで
書いて呼び出し元の commit に載せる。

## 用語の更新は 1 文にまとめる

1 クエリで数千の用語を返しうるので、用語ごとに文を投げると往復が数千回に
なる。`INSERT ... ON CONFLICT DO UPDATE` に複数行を渡して**1 往復**にする。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.access import AccessRecord
from ontology_core.db import AccessEventRow, TermAccessRow
from ontology_core.models import AccessEvent, AccessPage, TermAccess

__all__ = ["AccessRepository"]


def _to_event(row: AccessEventRow) -> AccessEvent:
    return AccessEvent(
        namespace=row.namespace,
        actor=row.actor,
        occurred_at=row.occurred_at,
        query_text=row.query_text,
        query_hash=row.query_hash,
        query_truncated=row.query_truncated,
        default_graph_version=row.default_graph_version,
        used_graph_clause=row.used_graph_clause,
        returned_row_count=row.returned_row_count,
        returned_triple_count=row.returned_triple_count,
        returned_term_count=row.returned_term_count,
    )


def _to_term_access(row: TermAccessRow) -> TermAccess:
    return TermAccess(
        namespace=row.namespace,
        term_iri=row.term_iri,
        last_accessed_at=row.last_accessed_at,
        access_count=row.access_count,
    )


class AccessRepository:
    """`access_events` と `term_access` へのアクセス。"""

    #: 1 ページの既定件数と上限(`AuditRepository` と揃える)。
    DEFAULT_LIMIT = 50
    MAX_LIMIT = 500

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, record: AccessRecord) -> None:
        """1 回のクエリを記録する。

        **呼び出し元は失敗を握り潰すこと**(ADR-0018 決定3)。アクセスログは
        読み取りの副産物であって、読み取りの前提条件ではない。
        """
        self._session.add(
            AccessEventRow(
                namespace=record.namespace,
                actor=record.actor,
                query_text=record.query.text,
                query_hash=record.query.hash,
                query_truncated=record.query.truncated,
                default_graph_version=record.default_graph_version,
                used_graph_clause=record.used_graph_clause,
                returned_row_count=record.returned_row_count,
                returned_triple_count=record.returned_triple_count,
                returned_term_count=record.returned_term_count,
            )
        )
        if record.terms:
            # **1 文にまとめる。** 用語ごとに投げると往復が数千回になる。
            #
            # `last_accessed_at` は `now()`(トランザクション開始時刻)に任せず
            # 明示的に更新する — 既定値は挿入時にしか効かないため、更新の側で
            # 触らないと「最後の参照」が最初の参照のまま止まる。
            stmt = pg_insert(TermAccessRow).values(
                [
                    {
                        "namespace": record.namespace,
                        "term_iri": iri,
                        "access_count": 1,
                    }
                    for iri in record.terms
                ]
            )
            await self._session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_term_access_ns_term",
                    set_={
                        "access_count": TermAccessRow.access_count + 1,
                        "last_accessed_at": func.now(),
                    },
                )
            )
        await self._session.flush()

    async def query_events(
        self,
        *,
        namespace: str,
        actor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: int | None = None,
    ) -> AccessPage:
        """アクセスのイベントを新しい順に 1 ページ返す。

        **並び順とページングの鍵は `id` である**(`AuditRepository.query` と
        同じ理由。`occurred_at` はトランザクション開始時刻なので同時刻が並ぶ)。

        Raises:
            ValueError: `limit` が範囲外、日時にタイムゾーンが無い、
                または `until` が `since` より前のとき。
        """
        if not 1 <= limit <= self.MAX_LIMIT:
            raise ValueError(
                f"limit は 1〜{self.MAX_LIMIT} の範囲で指定してください(受領: {limit})"
            )
        for name, value in (("since", since), ("until", until)):
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} にはタイムゾーンを付けてください")
        if since is not None and until is not None and until < since:
            raise ValueError("until が since より前です")

        stmt = select(AccessEventRow).where(AccessEventRow.namespace == namespace)
        if actor is not None:
            stmt = stmt.where(AccessEventRow.actor == actor)
        if since is not None:
            stmt = stmt.where(AccessEventRow.occurred_at >= since)
        if until is not None:
            stmt = stmt.where(AccessEventRow.occurred_at < until)
        if cursor is not None:
            stmt = stmt.where(AccessEventRow.id < cursor)

        rows = list(
            (
                await self._session.execute(
                    stmt.order_by(AccessEventRow.id.desc()).limit(limit + 1)
                )
            ).scalars()
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        return AccessPage(
            events=tuple(_to_event(row) for row in page),
            next_cursor=page[-1].id if has_more and page else None,
        )

    async def list_term_access(self, namespace: str) -> list[TermAccess]:
        """用語ごとの参照の集約を、最後の参照が古い順に返す。

        **古い順**にするのは、健全性指標が知りたいのが「参照されていない用語」
        だからである。新しい順に並べると、見たいものが最後に来る。
        """
        stmt = (
            select(TermAccessRow)
            .where(TermAccessRow.namespace == namespace)
            .order_by(TermAccessRow.last_accessed_at, TermAccessRow.term_iri)
        )
        return [_to_term_access(r) for r in (await self._session.execute(stmt)).scalars()]

    async def purge_before(self, *, namespace: str, before: datetime) -> int:
        """この時刻より前のイベントを削除し、削除した件数を返す。

        **`term_access` は消さない**(ADR-0018 決定1)。集約はイベントより
        長生きしなければならない — 消した瞬間に「90 日参照されていない」が
        計算不能になる。

        **削除したことを `audit_events` に記録するのは呼び出し元の責任**
        である。監査証跡の側は追記専用なので(ADR-0011 決定2)、その記録は
        消えない。

        Raises:
            ValueError: `before` にタイムゾーンが無いとき。
        """
        if before.tzinfo is None:
            raise ValueError("before にはタイムゾーンを付けてください")
        stmt = select(AccessEventRow).where(
            AccessEventRow.namespace == namespace,
            AccessEventRow.occurred_at < before,
        )
        rows = list((await self._session.execute(stmt)).scalars())
        for row in rows:
            await self._session.delete(row)
        await self._session.flush()
        return len(rows)
