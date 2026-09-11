"""access log triple count

`CONSTRUCT` / `DESCRIBE` のトリプル数を記録する(ADR-0034 決定7、P2A-14)。

**行とトリプルを混ぜない。**

| クエリの形 | returned_row_count | returned_triple_count |
|---|---|---|
| SELECT | 行数 | NULL |
| CONSTRUCT / DESCRIBE | **NULL**(行の概念が無い) | トリプル数 |

`returned_row_count` を NULL 可に変える。**`0` を書かない** — 「0 行返した」と
「行という概念が無い」は違い、`0` にすると「何も返さなかったクエリ」として
数えられてしまう。

**既存の行は変えない。** `ASK` が `0` を記録している既存の振る舞いも変えない
(あれも同じ混同だが、意味を変えると既存の記録の読み方が変わる。`P2B-22`)。

Revision ID: e8b5d2371a94
Revises: c1a6e93f4b28
Create Date: 2026-09-12 08:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8b5d2371a94"
down_revision: str | Sequence[str] | None = "c1a6e93f4b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "access_events",
        sa.Column("returned_triple_count", sa.Integer(), nullable=True),
    )
    op.alter_column("access_events", "returned_row_count", nullable=True)


def downgrade() -> None:
    # **NULL を 0 に埋め戻す。** NOT NULL へ戻す前に値を入れないと失敗する。
    # `0` は「行という概念が無い」を表せないが、下げる先のスキーマには
    # それを表す手段が無い(情報が落ちることを承知で戻す)。
    op.execute("UPDATE access_events SET returned_row_count = 0 WHERE returned_row_count IS NULL")
    op.alter_column("access_events", "returned_row_count", nullable=False)
    op.drop_column("access_events", "returned_triple_count")
