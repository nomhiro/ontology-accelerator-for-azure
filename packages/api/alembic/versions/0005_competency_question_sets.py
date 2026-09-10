"""competency question sets

想定質問の集合を名前空間に紐づける(ADR-0022、P2B-14)。

**1 行 = 1 改訂。名前空間ごとに 1 系列で、有効なのは最大の改訂である。**
改訂は書き換えない(不変条件7 と同じ形)。

**backfill は無い。** 何を要求すべきかはその名前空間の持ち主しか知らないので、
既定の質問集合を発明できない。**発明すると「基準を満たしている」と誤認させる** —
この機能が答えたいのはまさにそれなので、埋めてはいけない
(`0004_access_log` が参照の履歴を推測で埋めなかったのと同じ判断)。

そのため**質問集合が無い名前空間は承認をブロックしない**(ADR-0022 決定7)。
既存のすべての名前空間がこのマイグレーションの直後に承認不能になっては
いけない。定めていないことは健全性指標(`competency_question_count`)で見える。

`namespaces` への外部キー(`ON DELETE CASCADE`)を張るのは、名前空間を消した
ときに孤児が残らないようにするため。**`audit_events` と違って残す理由が無い** —
受け入れ基準はその名前空間のためのもので、名前空間が消えれば意味を失う
(誰がいつ基準を変えたかという決定の記録は `audit_events` 側に残る)。

Revision ID: 3a7e6c1b4d90
Revises: f5fa408ccb57
Create Date: 2026-09-11 09:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3a7e6c1b4d90"
down_revision: str | Sequence[str] | None = "f5fa408ccb57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "competency_question_sets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace", sa.String(length=63), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["namespace"], ["namespaces.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace", "revision", name="uq_competency_question_sets_ns_revision"
        ),
    )
    op.create_index(
        "ix_competency_question_sets_namespace", "competency_question_sets", ["namespace"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_competency_question_sets_namespace", table_name="competency_question_sets")
    op.drop_table("competency_question_sets")
