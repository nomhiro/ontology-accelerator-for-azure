"""term owners

用語単位の責任者を追加する(ADR-0015、P2B-04)。

**backfill は無い。** `0002_namespace_rbac` は既存の名前空間に `owner` を
付与しなければ「更新した瞬間に誰も何もできなくなる」ため backfill が必須
だったが、こちらは違う。

- 責任者は**権限ではない**ので、無くても操作は止まらない
- 用語単位の責任者は既存のどの列からも導出できない。`created_by` は「その版を
  publish した主体」であって、個々の用語について答えるべき人ではない
  (ADR-0009 決定4 がまさにこれを区別している)
- 推測で埋めると「責任者がいる」と誤認させる。**責任者が未設定であることは
  健全性指標(`P2B-06`)が可視化すべき事実**であり、隠してはいけない

Revision ID: bbd4cd867a74
Revises: 7c1a4b9d2e30
Create Date: 2026-09-10 00:00:32.595981

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bbd4cd867a74"
down_revision: str | Sequence[str] | None = "7c1a4b9d2e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "term_owners",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace", sa.String(length=63), nullable=False),
        sa.Column("term_iri", sa.String(length=1024), nullable=False),
        sa.Column("principal_id", sa.String(length=255), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("assigned_by", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["namespace"], ["namespaces.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "term_iri", name="uq_term_owners_ns_term"),
    )
    op.create_index("ix_term_owners_namespace", "term_owners", ["namespace"])
    # 責任者ごとの逆引き(ADR-0015 の未解決事項)に備える。テーブルが育ってから
    # ALTER するより、空のうちに張るほうが安い。
    op.create_index("ix_term_owners_principal", "term_owners", ["principal_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_term_owners_principal", table_name="term_owners")
    op.drop_index("ix_term_owners_namespace", table_name="term_owners")
    op.drop_table("term_owners")
