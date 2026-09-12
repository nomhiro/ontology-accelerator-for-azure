"""source scan catalog

ソース DB のスキーマを蓄積するカタログ(ADR-0041、P2A-01)。

**資格情報を持たない。** `scan_sources` は接続の**行き先**と、秘密の
**在り処**(Key Vault の秘密名)だけを持つ。パスワードの列は無い —
不変条件6(DSN にパスワードを埋めない)と同じ判断であり、API でも
受け取らない。

**観測を積む。上書きしない。** `scan_runs` が 1 回のスキャン、
`scan_tables` / `scan_columns` がそのときの観測である。スキーマは変わるので、
上書きすると「列が消えたこと」が分からなくなる。

**「無い」と「0」を区別する列がある。**

| 列 | `NULL` の意味 |
|---|---|
| `scan_runs.table_count` | 完了していない(`0 件だった`ではない) |
| `scan_tables.estimated_rows` | `ANALYZE` が走っていない(`0 行`ではない) |
| `scan_columns.estimated_distinct` | `n_distinct` が負(= 比率)だったか、統計が無い |
| `scan_columns.distinct_ratio` | `n_distinct` が絶対数だったか、統計が無い |

`estimated_distinct` と `distinct_ratio` は**排他**である。PostgreSQL の
`n_distinct` は負の値を「行数に対する比率」として使うので、絶対数に換算せず
形を分ける(換算には行数の推定が必要で、それが `NULL` のときに存在しない数を
作ることになる)。

Revision ID: 7d3c5f19ae42
Revises: 4f7a2c8e1b95
Create Date: 2026-09-12

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d3c5f19ae42"
down_revision: str | Sequence[str] | None = "4f7a2c8e1b95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("driver", sa.String(length=32), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("auth_mode", sa.String(length=32), nullable=False),
        # **秘密の「名前」だけである。** 値の列は作らない。
        sa.Column("vault_secret_name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["namespace"], ["namespaces.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "name", name="uq_scan_sources_ns_name"),
    )
    op.create_index("ix_scan_sources_namespace", "scan_sources", ["namespace"])

    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("table_count", sa.Integer(), nullable=True),
        sa.Column("started_by", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["scan_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scan_runs_source_started", "scan_runs", ["source_id", "started_at"])

    op.create_table(
        "scan_tables",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=255), nullable=False),
        sa.Column("table_name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("estimated_rows", sa.Integer(), nullable=True),
        sa.Column("table_comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["scan_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "schema_name", "table_name", name="uq_scan_tables_run_table"),
    )
    op.create_index("ix_scan_tables_run", "scan_tables", ["run_id"])

    op.create_table(
        "scan_columns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("table_id", sa.Integer(), nullable=False),
        sa.Column("column_name", sa.String(length=255), nullable=False),
        sa.Column("ordinal_position", sa.Integer(), nullable=False),
        sa.Column("data_type", sa.String(length=255), nullable=False),
        sa.Column("is_nullable", sa.Boolean(), nullable=False),
        sa.Column("column_default", sa.Text(), nullable=True),
        sa.Column("character_maximum_length", sa.Integer(), nullable=True),
        sa.Column("numeric_precision", sa.Integer(), nullable=True),
        sa.Column("numeric_scale", sa.Integer(), nullable=True),
        sa.Column("column_comment", sa.Text(), nullable=True),
        sa.Column("estimated_distinct", sa.Integer(), nullable=True),
        sa.Column("distinct_ratio", sa.Float(), nullable=True),
        sa.Column("null_fraction", sa.Float(), nullable=True),
        sa.Column("is_primary_key", sa.Boolean(), nullable=False),
        sa.Column("referenced_schema", sa.String(length=255), nullable=True),
        sa.Column("referenced_table", sa.String(length=255), nullable=True),
        sa.Column("referenced_column", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["scan_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["table_id"], ["scan_tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("table_id", "column_name", name="uq_scan_columns_table_column"),
    )
    op.create_index("ix_scan_columns_run", "scan_columns", ["run_id"])
    op.create_index("ix_scan_columns_table", "scan_columns", ["table_id"])


def downgrade() -> None:
    # **依存の逆順で落とす。** 子から先に落とさないと外部キーで失敗する。
    op.drop_index("ix_scan_columns_table", table_name="scan_columns")
    op.drop_index("ix_scan_columns_run", table_name="scan_columns")
    op.drop_table("scan_columns")
    op.drop_index("ix_scan_tables_run", table_name="scan_tables")
    op.drop_table("scan_tables")
    op.drop_index("ix_scan_runs_source_started", table_name="scan_runs")
    op.drop_table("scan_runs")
    op.drop_index("ix_scan_sources_namespace", table_name="scan_sources")
    op.drop_table("scan_sources")
