"""access log

コンテキストのアクセスログを追加する(ADR-0018、P2B-05)。

**2 つのテーブルに分けるのは保持期間が違うため**である(ADR-0018 決定1)。

- `access_events`: クエリ 1 回 = 1 行。監査(ADR-0006 決定4)。**保持期間がある**
- `term_access`: 用語 1 件 = 1 行(更新)。健全性指標(ADR-0009 決定5)。
  **イベントより長生きする** — 保持期間を過ぎたイベントを消しても
  「最後にいつ参照されたか」は残らなければならない

**backfill は無い。** 過去のクエリは記録されていないので、参照の履歴は
どこからも導出できない。**推測で埋めると「使われている」と誤認させる** —
健全性指標が答えたいのはまさにそれなので、埋めてはいけない。

`term_access` に `namespaces` への外部キー(`ON DELETE CASCADE`)を張るのは、
名前空間を消したときに孤児が残らないようにするため。`access_events` には
張らない — **名前空間を消した後も「誰がいつ何を問い合わせたか」は監査として
残るべきである**(消えると、消した名前空間への参照の記録が失われる)。

Revision ID: f5fa408ccb57
Revises: bbd4cd867a74
Create Date: 2026-09-10 22:48:47.681693

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5fa408ccb57"
down_revision: str | Sequence[str] | None = "bbd4cd867a74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "access_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace", sa.String(length=63), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("query_truncated", sa.Boolean(), nullable=False),
        sa.Column("default_graph_version", sa.String(length=64), nullable=True),
        sa.Column("used_graph_clause", sa.Boolean(), nullable=False),
        sa.Column("returned_row_count", sa.Integer(), nullable=False),
        sa.Column("returned_term_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_access_events_actor", "access_events", ["actor"])
    op.create_index(
        "ix_access_events_namespace_occurred", "access_events", ["namespace", "occurred_at"]
    )

    op.create_table(
        "term_access",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace", sa.String(length=63), nullable=False),
        sa.Column("term_iri", sa.String(length=1024), nullable=False),
        sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("access_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["namespace"], ["namespaces.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "term_iri", name="uq_term_access_ns_term"),
    )
    # 「最後の参照が古い用語」を引くための索引。健全性指標の主クエリである。
    op.create_index(
        "ix_term_access_namespace_last", "term_access", ["namespace", "last_accessed_at"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_term_access_namespace_last", table_name="term_access")
    op.drop_table("term_access")
    op.drop_index("ix_access_events_namespace_occurred", table_name="access_events")
    op.drop_index("ix_access_events_actor", table_name="access_events")
    op.drop_table("access_events")
