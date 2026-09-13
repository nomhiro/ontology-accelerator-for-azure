"""R2RML マッピングの読み書き(ADR-0046、`P3-01`)。

**改訂は不変である。** 書き換える口を持たない — `add` だけがある
(`questions.py` と同じ形。ADR-0022 決定2 と同じ理由ではないが、同じ形に
なる理由がある: **マッピングは実データへの入口の定義**なので、
「いつから何が見えていたか」を後から書き換えられてはいけない)。

**削除する口も持たない。** ソースを消せば `ON DELETE CASCADE` で消えるが、
マッピングだけを消す経路は無い。仮想グラフを止めたいならソースを消す。
"""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.db import VkgMappingRow
from ontology_core.models import VkgMapping, VkgMappingSummary

__all__ = ["VkgMappingRepository", "mapping_content_hash"]


def mapping_content_hash(text: str) -> str:
    """マッピング本文のハッシュ。改訂の同一性の根拠にする。

    オントロジーの版(`OntologyVersionRow`)と質問集合
    (`ontology_core.competency.content_hash`)と同じ形。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_tables(raw: str) -> tuple[str, ...]:
    """`tables` 列を分解する。

    **空文字列を `('',)` にしない。** 「関係が 1 つも無い」は登録時に
    弾いているので実際には起きないが、起きたときに「`''` という名前の
    関係を読む」と読めてしまうのは避ける。
    """
    return tuple(part for part in raw.split(",") if part)


def _to_model(row: VkgMappingRow, *, source: str) -> VkgMapping:
    return VkgMapping(
        namespace=row.namespace,
        source=source,
        revision=row.revision,
        content=row.content,
        content_hash=row.content_hash,
        triples_map_count=row.triples_map_count,
        tables=_split_tables(row.tables),
        validated_against_version=row.validated_against_version,
        created_at=row.created_at,
        created_by=row.created_by,
        reason=row.reason,
    )


class VkgMappingRepository:
    """R2RML マッピング(ソースごとの不変改訂)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active(self, *, source_id: int, source: str) -> VkgMapping | None:
        """有効な改訂(最大の `revision`)を返す。無ければ `None`。

        **`None` は「マッピングを登録していない」であって「仮想グラフが
        空である」ではない**(ADR-0046 決定11)。呼び出し側でこの区別を
        潰さないこと — 潰すと、登録を忘れたソースへの照会が
        **「該当する行が無い」**として返る。
        """
        stmt = (
            select(VkgMappingRow)
            .where(VkgMappingRow.source_id == source_id)
            .order_by(VkgMappingRow.revision.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _to_model(row, source=source)

    async def revisions(self, *, source_id: int, source: str) -> list[VkgMappingSummary]:
        """改訂の一覧を新しい順に返す(本文は含まない)。

        **実データへの入口がいつ・誰に・なぜ変えられたかの履歴である。**
        """
        stmt = (
            select(VkgMappingRow)
            .where(VkgMappingRow.source_id == source_id)
            .order_by(VkgMappingRow.revision.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_model(row, source=source).summary() for row in rows]

    async def add(
        self,
        *,
        namespace: str,
        source_id: int,
        source: str,
        content: str,
        triples_map_count: int,
        tables: tuple[str, ...],
        validated_against_version: str | None,
        actor: str,
        reason: str,
    ) -> VkgMapping:
        """新しい改訂を足す。

        **`revision` は「そのソースの最大 + 1」である。** 主キー(`id`)の
        順序には頼らない — `id` は全ソースで共有の連番なので、ソース内の
        「何番目の改訂か」を表さない。

        commit は呼び出し側が行う(監査記録と同一トランザクションにする)。
        """
        stmt = select(func.max(VkgMappingRow.revision)).where(VkgMappingRow.source_id == source_id)
        current = (await self._session.execute(stmt)).scalar_one_or_none()
        row = VkgMappingRow(
            namespace=namespace,
            source_id=source_id,
            revision=(current or 0) + 1,
            content=content,
            content_hash=mapping_content_hash(content),
            triples_map_count=triples_map_count,
            tables=",".join(tables),
            validated_against_version=validated_against_version,
            created_by=actor,
            reason=reason,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _to_model(row, source=source)
