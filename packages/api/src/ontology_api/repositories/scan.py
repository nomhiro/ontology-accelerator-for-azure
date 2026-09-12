"""スキャンのソースとカタログの永続化(ADR-0041、`P2A-01`)。

**資格情報を保存しない。** `scan_sources` は接続の行き先と、秘密の在り処
(Key Vault の秘密名)だけを持つ(決定4)。

**観測を積む。上書きしない**(決定6)。スキーマは変わるので、上書きすると
「列が消えたこと」が分からなくなる。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.db import ScanColumnRow, ScanRunRow, ScanSourceRow, ScanTableRow
from ontology_core.models import ScanColumn, ScanRun, ScanSource, ScanTable
from ontology_core.scan import ScanRunStatus, TableObservation

__all__ = ["ScanRepository"]


def _to_source(row: ScanSourceRow) -> ScanSource:
    return ScanSource(
        id=row.id,
        namespace=row.namespace,
        name=row.name,
        driver=row.driver,
        host=row.host,
        port=row.port,
        database=row.database,
        username=row.username,
        auth_mode=row.auth_mode,
        vault_secret_name=row.vault_secret_name,
        created_at=row.created_at,
        created_by=row.created_by,
    )


def _to_run(row: ScanRunRow) -> ScanRun:
    return ScanRun(
        id=row.id,
        source_id=row.source_id,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        failure_reason=row.failure_reason,
        table_count=row.table_count,
        started_by=row.started_by,
    )


def _to_column(row: ScanColumnRow) -> ScanColumn:
    return ScanColumn(
        column_name=row.column_name,
        ordinal_position=row.ordinal_position,
        data_type=row.data_type,
        is_nullable=row.is_nullable,
        column_default=row.column_default,
        character_maximum_length=row.character_maximum_length,
        numeric_precision=row.numeric_precision,
        numeric_scale=row.numeric_scale,
        column_comment=row.column_comment,
        estimated_distinct=row.estimated_distinct,
        distinct_ratio=row.distinct_ratio,
        null_fraction=row.null_fraction,
        is_primary_key=row.is_primary_key,
        referenced_schema=row.referenced_schema,
        referenced_table=row.referenced_table,
        referenced_column=row.referenced_column,
    )


class ScanRepository:
    """`scan_*` へのアクセス。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------ ソース

    async def create_source(
        self,
        *,
        namespace: str,
        name: str,
        driver: str,
        host: str,
        port: int,
        database: str,
        username: str,
        auth_mode: str,
        vault_secret_name: str | None,
        created_by: str,
    ) -> ScanSource:
        """ソースを登録する。

        **パスワードを受け取る引数が無い。** 署名の狭さで「保存しない」を
        強制している(`P2B-20` の教訓と同じ形)。
        """
        row = ScanSourceRow(
            namespace=namespace,
            name=name,
            driver=driver,
            host=host,
            port=port,
            database=database,
            username=username,
            auth_mode=auth_mode,
            vault_secret_name=vault_secret_name,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_source(row)

    async def get_source(self, *, namespace: str, name: str) -> ScanSourceRow | None:
        """ソースの行を返す。**名前空間で必ず絞る**(不変条件5)。"""
        stmt = select(ScanSourceRow).where(
            ScanSourceRow.namespace == namespace,
            ScanSourceRow.name == name,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_sources(self, namespace: str) -> list[ScanSource]:
        stmt = (
            select(ScanSourceRow)
            .where(ScanSourceRow.namespace == namespace)
            .order_by(ScanSourceRow.name)
        )
        return [_to_source(row) for row in (await self._session.execute(stmt)).scalars()]

    async def list_all_source_rows(self) -> list[ScanSourceRow]:
        """**全名前空間の**ソースの行を返す(ADR-0042 決定2)。

        **名前空間で絞らない唯一のメソッドである。** 使うのは
        `scan-job`(定期実行の掃引)だけで、`reconcile` と同じ
        **システムの保守処理**の位置にある。

        不変条件5 に反しない — 境界が守るのは**外部入力が別の名前空間へ
        届かないこと**である。このメソッドの呼び出し元は外部入力を
        受け取らない(トリガに引数が無い)。

        **API のハンドラから呼んではいけない。** 名前空間を絞る
        `list_sources` を使う。
        """
        stmt = select(ScanSourceRow).order_by(ScanSourceRow.namespace, ScanSourceRow.name)
        return list((await self._session.execute(stmt)).scalars())

    async def delete_source(self, *, namespace: str, name: str) -> bool:
        """ソースを消す。**run とカタログも CASCADE で消える。**

        存在確認してから削除する(`session.execute()` の戻り値に `rowcount` は
        無い。CLAUDE.md の罠)。
        """
        row = await self.get_source(namespace=namespace, name=name)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    # ------------------------------------------------------------ run

    async def start_run(self, *, source_id: int, started_by: str) -> ScanRunRow:
        """`running` の run を作る(ADR-0041 決定7)。

        **先に書く。** 途中で落ちた run が `running` のまま残ることで、
        「完了していない観測」が区別できる。
        """
        row = ScanRunRow(
            source_id=source_id,
            status=ScanRunStatus.RUNNING.value,
            started_by=started_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def finish_run(self, run: ScanRunRow, *, table_count: int) -> ScanRun:
        """run を `succeeded` にする。"""
        run.status = ScanRunStatus.SUCCEEDED.value
        run.finished_at = datetime.now(UTC)
        run.table_count = table_count
        await self._session.flush()
        return _to_run(run)

    async def fail_run(self, run: ScanRunRow, *, reason: str) -> ScanRun:
        """run を `failed` にする。**`table_count` は `None` のままにする。**

        「0 件だった」と「まだ分からない」を混ぜない。
        """
        run.status = ScanRunStatus.FAILED.value
        run.finished_at = datetime.now(UTC)
        run.failure_reason = reason
        await self._session.flush()
        return _to_run(run)

    async def list_runs(self, *, source_id: int, limit: int = 50) -> list[ScanRun]:
        """新しい順に返す。"""
        stmt = (
            select(ScanRunRow)
            .where(ScanRunRow.source_id == source_id)
            .order_by(ScanRunRow.started_at.desc(), ScanRunRow.id.desc())
            .limit(limit)
        )
        return [_to_run(row) for row in (await self._session.execute(stmt)).scalars()]

    async def get_run(self, *, source_id: int, run_id: int) -> ScanRun | None:
        """run を返す。**ソースで必ず絞る**(不変条件5)。

        `id` だけで引くと、他の名前空間のソースの run を読めてしまう。
        """
        stmt = select(ScanRunRow).where(
            ScanRunRow.id == run_id,
            ScanRunRow.source_id == source_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _to_run(row)

    async def latest_succeeded_run(self, *, source_id: int) -> ScanRun | None:
        """最後に**完了した** run を返す(ADR-0041 決定7)。

        **`running` と `failed` は返さない。** 半端な観測を「これが今の
        スキーマ」として読ませない。
        """
        stmt = (
            select(ScanRunRow)
            .where(
                ScanRunRow.source_id == source_id,
                ScanRunRow.status == ScanRunStatus.SUCCEEDED.value,
            )
            .order_by(ScanRunRow.started_at.desc(), ScanRunRow.id.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _to_run(row)

    # ------------------------------------------------------------ カタログ

    async def record_observations(
        self, run: ScanRunRow, observations: Sequence[TableObservation]
    ) -> None:
        """観測をカタログへ書く。**既存の行を触らない**(ADR-0041 決定6)。"""
        for table in observations:
            table_row = ScanTableRow(
                run_id=run.id,
                schema_name=table.schema_name,
                table_name=table.table_name,
                kind=table.kind,
                estimated_rows=table.estimated_rows,
                table_comment=table.comment,
            )
            self._session.add(table_row)
            await self._session.flush()
            for column in table.columns:
                self._session.add(
                    ScanColumnRow(
                        run_id=run.id,
                        table_id=table_row.id,
                        column_name=column.column_name,
                        ordinal_position=column.ordinal_position,
                        data_type=column.data_type,
                        is_nullable=column.is_nullable,
                        column_default=column.column_default,
                        character_maximum_length=column.character_maximum_length,
                        numeric_precision=column.numeric_precision,
                        numeric_scale=column.numeric_scale,
                        column_comment=column.comment,
                        estimated_distinct=column.estimated_distinct,
                        distinct_ratio=column.distinct_ratio,
                        null_fraction=column.null_fraction,
                        is_primary_key=column.is_primary_key,
                        referenced_schema=column.referenced_schema,
                        referenced_table=column.referenced_table,
                        referenced_column=column.referenced_column,
                    )
                )
        await self._session.flush()

    async def list_catalog(self, *, run_id: int) -> list[ScanTable]:
        """その run の観測を返す。

        **クエリは 2 回で、テーブル数に依らない**(`scan_columns` が `run_id` を
        持っているのはこのためである)。
        """
        table_stmt = (
            select(ScanTableRow)
            .where(ScanTableRow.run_id == run_id)
            .order_by(ScanTableRow.schema_name, ScanTableRow.table_name)
        )
        tables = list((await self._session.execute(table_stmt)).scalars())
        column_stmt = (
            select(ScanColumnRow)
            .where(ScanColumnRow.run_id == run_id)
            .order_by(ScanColumnRow.table_id, ScanColumnRow.ordinal_position)
        )
        columns = list((await self._session.execute(column_stmt)).scalars())

        by_table: dict[int, list[ScanColumn]] = {}
        for row in columns:
            by_table.setdefault(row.table_id, []).append(_to_column(row))

        return [
            ScanTable(
                schema_name=row.schema_name,
                table_name=row.table_name,
                kind=row.kind,
                estimated_rows=row.estimated_rows,
                table_comment=row.table_comment,
                columns=tuple(by_table.get(row.id, ())),
            )
            for row in tables
        ]
