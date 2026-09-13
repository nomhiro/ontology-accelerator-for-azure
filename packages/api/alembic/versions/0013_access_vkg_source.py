"""access log vkg source

仮想グラフへの照会をアクセスログに残す(ADR-0048、P3-08)。

**既存の行の意味を変えない。** `vkg_source` が `NULL` の行は
「オントロジーへの照会」であり、これは既存の全行と同じ意味である。
列を足すときに過去の行の読み方が変わると、記録そのものが信用できなくなる。

**この列があるから `default_graph_version` の `NULL` が曖昧でなくなる。**

| `vkg_source` | `default_graph_version` が `NULL` の意味 |
|---|---|
| `NULL` | 承認済み版が無かった |
| 非 `NULL` | **版の概念が無い**(仮想グラフ) |

`vkg_mapping_revision` は版の代わりである。「どの定義の入口を通して
実データを返したか」が監査の問いであり、仮想グラフではマッピングの改訂が
それに当たる。

**索引を足す。** ソースで絞る照会(「この顧客 DB に誰が何を聞いたか」)が
監査の主用途になるため。

Revision ID: 5c1e8b7f4a20
Revises: 2b8f4d6a03c1
Create Date: 2026-09-13

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c1e8b7f4a20"
down_revision: str | Sequence[str] | None = "2b8f4d6a03c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("access_events", sa.Column("vkg_source", sa.String(length=63), nullable=True))
    op.add_column("access_events", sa.Column("vkg_mapping_revision", sa.Integer(), nullable=True))
    op.create_index(
        "ix_access_events_vkg_source",
        "access_events",
        ["namespace", "vkg_source", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_access_events_vkg_source", table_name="access_events")
    op.drop_column("access_events", "vkg_mapping_revision")
    op.drop_column("access_events", "vkg_source")
