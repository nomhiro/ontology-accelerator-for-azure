"""namespace retirement

名前空間の退役を追加する(ADR-0032、P2B-19)。

`DELETE /namespaces/{name}` の 409 が「公開済みオントロジーを含む名前空間の
削除は Phase 2(監査経路)で対応します」と約束していた中身である。
ADR-0024 決定4 が挙げた 3 つの選択肢のうち「**正本を残し、射影だけ止める**」を
採った。

**削除ではない。** Blob の TTL も PostgreSQL の版と監査もそのまま残る
(不変条件7)。止まるのは射影と内容の増設だけである。

**backfill は無い。** 既存の名前空間はすべて現役(`retired_at IS NULL`)で
始まる。既定を「退役」にすると、マイグレーションを当てた瞬間に全名前空間の
射影が止まる。

**`retired_at` が NULL かどうかが唯一の判定である。** 文字列の状態機械に
しないのは、名前空間の状態が他に無く、値を足すたびに移行が要る形を作らない
ため(ADR-0032 決定1)。

Revision ID: c1a6e93f4b28
Revises: 9c4d18ba5e72
Create Date: 2026-09-12 06:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a6e93f4b28"
down_revision: str | Sequence[str] | None = "9c4d18ba5e72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "namespaces",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("namespaces", sa.Column("retired_by", sa.String(length=255), nullable=True))
    op.add_column("namespaces", sa.Column("retired_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("namespaces", "retired_reason")
    op.drop_column("namespaces", "retired_by")
    op.drop_column("namespaces", "retired_at")
