"""namespace rbac

名前空間ごとのロール付与と四眼原則の設定を追加する(ADR-0014、P2A-06)。

**既存の名前空間には `created_by` から `owner` を 1 件付与する。**
暗黙のフォールバック(付与が無ければ全員に許可する等)を作らないと決めたので
(ADR-0014 決定6)、この backfill が無いと更新した瞬間に誰も何もできなくなる。

`require_two_person_approval` の既定は `true`(安全側)。ただし**既存の
名前空間には `false` を入れる**。既に運用している名前空間に対して、更新した
瞬間に「自分が publish したものを自分で approve できない」を課すのは破壊的な
変更である。締めるかどうかは運用者が明示的に選ぶ。

Revision ID: 7c1a4b9d2e30
Revises: 03fff5e0d815
Create Date: 2026-09-09 14:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c1a4b9d2e30"
down_revision: str | Sequence[str] | None = "03fff5e0d815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "namespace_roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace", sa.String(length=63), nullable=False),
        sa.Column("principal_id", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("granted_by", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["namespace"], ["namespaces.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "principal_id", name="uq_namespace_roles_ns_principal"),
    )
    op.create_index("ix_namespace_roles_namespace", "namespace_roles", ["namespace"])

    # 新しい名前空間の既定は true(安全側。ADR-0014 決定4)。
    op.add_column(
        "namespaces",
        sa.Column(
            "require_two_person_approval",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )

    # ---- 既存データの移行 ----
    #
    # **既存の名前空間の作成者を owner にする。** これが無いと、更新した瞬間に
    # 誰も何もできなくなる(ADR-0014 決定6 で暗黙のフォールバックを禁じたため)。
    # `created_by` は Phase 1 から記録されている。
    op.execute(
        sa.text(
            """
            INSERT INTO namespace_roles (namespace, principal_id, role, granted_by)
            SELECT name, created_by, 'owner', created_by
            FROM namespaces
            WHERE created_by <> ''
            ON CONFLICT (namespace, principal_id) DO NOTHING
            """
        )
    )
    # **既存の名前空間は四眼原則を無効で始める。** 運用中の名前空間に、更新した
    # 瞬間に「自分が publish したものを自分で approve できない」を課すのは
    # 破壊的である。締めるかどうかは運用者が明示的に選ぶ。
    op.execute(sa.text("UPDATE namespaces SET require_two_person_approval = false"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("namespaces", "require_two_person_approval")
    op.drop_index("ix_namespace_roles_namespace", table_name="namespace_roles")
    op.drop_table("namespace_roles")
