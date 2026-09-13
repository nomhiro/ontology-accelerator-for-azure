"""term embeddings

用語の埋め込み(ADR-0050、P3-02)。

**再構築可能な射影である**(不変条件1 と同じ形)。正本は Blob の TTL で、
この表はそこから作り直せる。

**拡張はここで作らない。** alembic の env.py は
`SET ROLE ontology_owner`(NOLOGIN、azure_pg_admin ではない)で走るので、
`CREATE EXTENSION` ができない。デプロイ環境は `scripts/bootstrap-db.py`、
ローカルは `just up` と `packages/api/tests/conftest.py` が作る。
**無ければこのマイグレーションが `type "vector" does not exist` で
明示的に落ちる** — 黙って進まない。

**次元は 1536 に固定する。** pgvector の hnsw 索引は 2000 次元までである
(実測。2001 以上で `column cannot have more than 2000 dimensions for
hnsw index`)。`text-embedding-3-large`(3072)は `halfvec` へのキャストが
要るので採らない。

**索引を 2 つ作る。**

| 索引 | 何に使うか |
|---|---|
| `hnsw (embedding vector_cosine_ops)` | ベクトルの経路(概念の近さ) |
| `gin (source_text gin_trgm_ops)` | 3-gram の経路(表記の部分一致) |

3-gram を使うのは、**日本語の形態素解析が使えない**ためである
(`pg_bigm` / `pgroonga` は Azure の対応拡張一覧に無い)。

Revision ID: 8a2d6c4b91e7
Revises: 5c1e8b7f4a20
Create Date: 2026-09-13

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "8a2d6c4b91e7"
down_revision: str | Sequence[str] | None = "5c1e8b7f4a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 列の次元。`ontology_core.embedding.EMBEDDING_DIMENSIONS` と同じ値である。
#:
#: **マイグレーションは定数を import しない。** 過去のマイグレーションが
#: 現在のコードに依存すると、コードを変えた瞬間に**過去の履歴の意味が
#: 変わる**(alembic のマイグレーションは一度書いたら不変であるべき)。
EMBEDDING_DIMENSIONS = 1536


def upgrade() -> None:
    op.create_table(
        "term_embeddings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace", sa.String(length=63), nullable=False),
        sa.Column("term_iri", sa.String(length=1024), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column("built_from_version", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("deprecated", sa.Boolean(), nullable=False),
        sa.Column(
            "built_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["namespace"], ["namespaces.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "term_iri", name="uq_term_embeddings_ns_term"),
    )
    op.create_index("ix_term_embeddings_namespace", "term_embeddings", ["namespace"])
    # ベクトルの経路。**cosine で引く**(`<=>`)。
    op.execute(
        "CREATE INDEX ix_term_embeddings_vector ON term_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    # 3-gram の経路。**日本語の部分一致に使う**(形態素解析は使えない)。
    op.execute(
        "CREATE INDEX ix_term_embeddings_trgm ON term_embeddings "
        "USING gin (source_text gin_trgm_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_term_embeddings_trgm", table_name="term_embeddings")
    op.drop_index("ix_term_embeddings_vector", table_name="term_embeddings")
    op.drop_index("ix_term_embeddings_namespace", table_name="term_embeddings")
    op.drop_table("term_embeddings")
