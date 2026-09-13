"""vkg mappings

R2RML マッピングの不変改訂(ADR-0046、P3-01)。

**1 行 = 1 改訂。ソースごとに 1 系列で、有効なのは最大の改訂である。**
`competency_question_sets` と同じ形にしてある(ADR-0022 決定1)。

**Blob には置かない。** マッピングは Fuseki へ射影されないので、
Blob → PostgreSQL の順序(不変条件2)を挟む理由が無い。挟むと孤児 Blob
という故障モードを新しく作る(P2B-12 / P2B-20 と同じ形)。

**`reason` は必須である。** マッピングは実データへの入口の定義なので、
入口が変わった理由が残らないのは監査として成立しない。

**`validated_against_version` の `NULL` は「承認済み版が無かった」**
(決定12)。「検証していない」ではない。登録時点で照合先が存在しなかった
ことを表す。

Revision ID: 2b8f4d6a03c1
Revises: 7d3c5f19ae42
Create Date: 2026-09-13

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2b8f4d6a03c1"
down_revision: str | Sequence[str] | None = "7d3c5f19ae42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vkg_mappings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace", sa.String(length=63), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("triples_map_count", sa.Integer(), nullable=False),
        sa.Column("tables", sa.Text(), nullable=False),
        sa.Column("validated_against_version", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["namespace"], ["namespaces.name"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["scan_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "revision", name="uq_vkg_mappings_source_revision"),
    )
    op.create_index("ix_vkg_mappings_source", "vkg_mappings", ["source_id"])
    op.create_index("ix_vkg_mappings_namespace", "vkg_mappings", ["namespace"])


def downgrade() -> None:
    op.drop_index("ix_vkg_mappings_namespace", table_name="vkg_mappings")
    op.drop_index("ix_vkg_mappings_source", table_name="vkg_mappings")
    op.drop_table("vkg_mappings")
