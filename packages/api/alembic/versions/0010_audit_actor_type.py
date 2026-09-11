"""audit actor type

主体が人間かサービスプリンシパルかを記録する(ADR-0035、P2A-16)。

**`NULL` を残す。`'unknown'` で backfill しない。**

| 列の値 | 意味 |
|---|---|
| `NULL` | **問うていない**(この機能より前に書かれた既存の全行) |
| `'unknown'` | **問うて、分からなかった**(`idtyp` クレームが無かった) |
| `'user'` / `'service-principal'` | 記録した |

既存の行を `'unknown'` で埋めると、「問うていない」が「問うて、分からな
かった」に化ける。書き出しは前者に `ont:actorType` を出さない(ADR-0035
決定5)ので、この区別は外まで届く。ADR-0027 決定4 が却下した backfill と
同じ形である。

**`server_default` も置かない。** 置くと、この機能を通らない経路
(将来の直接 INSERT など)が黙って `'unknown'` を書く。既定値はアプリ側の
`AuditRepository.record` の必須引数として表現する(決定6)。

Revision ID: 4f7a2c8e1b95
Revises: e8b5d2371a94
Create Date: 2026-09-12

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4f7a2c8e1b95"
down_revision: str | Sequence[str] | None = "e8b5d2371a94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("actor_type", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_events", "actor_type")
