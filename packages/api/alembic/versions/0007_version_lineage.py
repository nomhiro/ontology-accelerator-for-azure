"""version lineage

何から編集したかを記録する(ADR-0027、P2A-15)。

**2 列で 3 状態を表す**(決定1)。1 本の nullable 列にすると、
「宣言されなかった」と「先行する版が無かった」が同じ NULL になり、
**「分からない」が「派生していない」として読める**。

| edited_from_recorded | edited_from | 意味 |
|---|---|---|
| false | NULL | 分からない(宣言されなかった)。**既存の全行がこれ** |
| true | NULL | この名前空間に先行する版が無かった(最初の版) |
| true | '1.0.0' | 1.0.0 から編集した |

**backfill はしない**(決定4)。版の id の順に並べて直前の版を親とすれば
それらしい系譜が作れるが、**承認の順序は派生ではない**。
[ADR-0026](../../../../docs/adr/0026-provenance-export.md) 決定2 が API の
手前で拒否したことを正本に書き込む形になり、正本に書いた推測は後から事実と
区別できない(0004_access_log / 0005_competency_question_sets /
0006_term_mappings と同じ判断)。

**既定を false にするのが安全側である。** true を既定にすると、既存の全行が
「先行する版は無かった」と主張し始める。

**外部キーは張らない**(決定3 の結果)。書き込み時に「当時の最新版」と一致
することを検査しているので実在は保証され、版の行は個別には削除されない
(不変条件7。名前空間の削除は namespace の CASCADE で全版が消える)。

Revision ID: 9c4d18ba5e72
Revises: 6b2f91c07e48
Create Date: 2026-09-12 02:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c4d18ba5e72"
down_revision: str | Sequence[str] | None = "6b2f91c07e48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ontology_versions",
        sa.Column("edited_from", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ontology_versions",
        sa.Column(
            "edited_from_recorded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ontology_versions", "edited_from_recorded")
    op.drop_column("ontology_versions", "edited_from")
