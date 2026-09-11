"""term mappings

領域間マッピングを追加する(ADR-0023、P2B-10)。

**backfill は無い。** 「営業の優良顧客と経理の優良顧客が同じか」は、どちらの
名前空間の持ち主にも聞かずに決められない。用語名が似ていることから推測して
埋めると、**誰も宣言していないマッピングが「宣言された事実」として現れる** —
この機能が守ろうとしているのはまさにその逆(相違が消されずに記録されること)
なので、埋めてはいけない(`0004_access_log` / `0005_competency_question_sets`
と同じ判断)。

`target_term` に索引を張るのは、**「自分の用語に対して他の名前空間から張られて
いるマッピング」(incoming)を引くため**である(ADR-0023 決定3)。張った側から
しか見えない設計にすると、相手の主張に気づけない。

`namespaces` への外部キー(`ON DELETE CASCADE`)は始点側だけに張る。
**終点側には張れない** — 終点は外部語彙(SKOS、schema.org)でありうるので、
このシステムの名前空間である保証が無い(決定6)。その結果、**終点の名前空間を
削除しても、そこを指すマッピングは残る**。これは意図した振る舞いである
(「消えた領域を指していた」という事実は記録として意味を持つ)。

Revision ID: 6b2f91c07e48
Revises: 3a7e6c1b4d90
Create Date: 2026-09-11 11:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6b2f91c07e48"
down_revision: str | Sequence[str] | None = "3a7e6c1b4d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "term_mappings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace", sa.String(length=63), nullable=False),
        sa.Column("source_term", sa.String(length=1024), nullable=False),
        sa.Column("target_term", sa.String(length=1024), nullable=False),
        sa.Column("predicate", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("declared_by", sa.String(length=255), nullable=False),
        sa.Column(
            "declared_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["namespace"], ["namespaces.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace", "source_term", "target_term", name="uq_term_mappings_ns_source_target"
        ),
    )
    op.create_index("ix_term_mappings_namespace", "term_mappings", ["namespace"])
    op.create_index("ix_term_mappings_target", "term_mappings", ["target_term"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_term_mappings_target", table_name="term_mappings")
    op.drop_index("ix_term_mappings_namespace", table_name="term_mappings")
    op.drop_table("term_mappings")
