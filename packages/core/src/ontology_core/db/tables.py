"""正本(PostgreSQL)のテーブル定義。

ここに置くのは「オントロジーそのもの」ではなく、その**メタデータと監査**である。
オントロジー本体(TTL)の正本は Blob 側にあり、DB は所在と状態を持つ
(docs/adr/0003-postgresql-as-system-of-record.md)。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """全テーブルの基底。"""


class NamespaceRow(Base):
    """名前空間。Fuseki のデータセット 1 つに対応する。"""

    __tablename__ = "namespaces"

    name: Mapped[str] = mapped_column(String(63), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    base_iri: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(255))
    # 四眼原則(ADR-0014 決定4)。**既定は有効**。
    #
    # 無条件に強制すると運用者 1 人のデプロイ(postdeploy が同梱サンプルを
    # publish → submit → approve する経路)が動かなくなるため、名前空間ごとの
    # 設定にしている。既定を有効にするのは、規制産業での採用可否を左右する
    # 要件(ADR-0006)であり**安全側を既定にする**ため。緩めるのは明示的な
    # 選択であるべきで、逆(既定で緩く、締めるのを忘れる)は事故になる。
    require_two_person_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class NamespaceRoleRow(Base):
    """名前空間ごとのロール付与(ADR-0014 決定1)。

    **`principal_id` は Entra のオブジェクト ID である。** UPN や表示名では
    ない。UPN は変わりうるし、ゲストの `#EXT#` 形式は書き換えの罠がある
    (`P1-11` で実際に踏んだ)。

    1 人が 1 つの名前空間に持つロールは 1 つ(`(namespace, principal_id)` を
    一意にする)。ロールは順序付きで上位が下位を含むため、複数ロールの合成を
    考える必要がない。
    """

    __tablename__ = "namespace_roles"
    __table_args__ = (
        UniqueConstraint("namespace", "principal_id", name="uq_namespace_roles_ns_principal"),
        Index("ix_namespace_roles_namespace", "namespace"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(
        ForeignKey("namespaces.name", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False)


class OntologyVersionRow(Base):
    """オントロジーの不変リビジョン。

    同一の名前空間で同じ content_hash を二重登録しない(冪等性の根拠)。
    """

    __tablename__ = "ontology_versions"
    __table_args__ = (
        UniqueConstraint("namespace", "version", name="uq_ontology_versions_ns_version"),
        UniqueConstraint("namespace", "content_hash", name="uq_ontology_versions_ns_hash"),
        Index("ix_ontology_versions_namespace", "namespace"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(
        ForeignKey("namespaces.name", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    graph_iri: Mapped[str] = mapped_column(Text)
    blob_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    approved_by: Mapped[str | None] = mapped_column(String(255), default=None)
    # 射影が完了した時刻。NULL なら未射影で、reconcile の対象になる。
    projected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AuditEventRow(Base):
    """監査証跡。誰が・いつ・何を・なぜ。"""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_namespace_occurred", "namespace", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(63), nullable=False)
    action: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    subject: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    diff: Mapped[str | None] = mapped_column(Text, default=None)
