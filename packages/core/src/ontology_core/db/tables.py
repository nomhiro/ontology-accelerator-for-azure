"""正本(PostgreSQL)のテーブル定義。

ここに置くのは「オントロジーそのもの」ではなく、その**メタデータと監査**である。
オントロジー本体(TTL)の正本は Blob 側にあり、DB は所在と状態を持つ
(docs/adr/0003-postgresql-as-system-of-record.md)。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
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
