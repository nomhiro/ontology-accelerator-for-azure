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
    Integer,
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


class TermOwnerRow(Base):
    """用語単位の責任者(ADR-0015 決定1)。

    **`namespace_roles` とは別のテーブルである。** ロールは権限、責任者は
    説明責任で、概念が違う(ADR-0014 が用語単位を ADR-0015 に送った)。

    `(namespace, term_iri)` を一意にする。1 つの用語に責任者は 1 人である
    (説明責任が分散するとその意味を失う。ADR-0015 決定1)。

    **用語が実在するかは検査しない**(ADR-0015 決定4)。ストアは再構築可能な
    射影であって正本ではないため、存在確認は正本への書き込みを射影の可用性に
    依存させる(不変条件2・3 が禁じている向き)。
    """

    __tablename__ = "term_owners"
    __table_args__ = (
        UniqueConstraint("namespace", "term_iri", name="uq_term_owners_ns_term"),
        Index("ix_term_owners_namespace", "namespace"),
        # 責任者ごとの逆引き(ADR-0015 の未解決事項)に備える。今は使わないが、
        # 後から張るとテーブルが育ってから ALTER することになる。
        Index("ix_term_owners_principal", "principal_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(
        ForeignKey("namespaces.name", ondelete="CASCADE"), nullable=False
    )
    term_iri: Mapped[str] = mapped_column(String(1024), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    assigned_by: Mapped[str] = mapped_column(String(255), nullable=False)


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


class AccessEventRow(Base):
    """コンテキストのアクセスログ(ADR-0018 決定1)。**クエリ 1 回 = 1 行。**

    `audit_events` とは**性質が違う**。あちらは人の決定の記録で件数が緩やかに
    増え、消す理由が無いので追記専用にしている(ADR-0011 決定2)。こちらは
    機械の参照の記録で、エージェントの稼働に比例して無限に伸びる。そのため
    **保持期間があり、`DELETE` を与える**。ただし削除は運用者の明示的な操作に
    限り、**削除したこと自体を `audit_events` に記録する**(決定2)。

    **返した用語の一覧は載せない。** 1 クエリで数千の用語を返しうるので、
    行が非有界に育つ。オントロジーは不変リビジョンなので(不変条件7)、
    記録したクエリと版で再実行できる。件数だけを持つ。
    """

    __tablename__ = "access_events"
    __table_args__ = (
        Index("ix_access_events_namespace_occurred", "namespace", "occurred_at"),
        Index("ix_access_events_actor", "actor"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(63), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # クエリは切り詰めて保存し、全文のハッシュを別に持つ。
    # **同じクエリをまとめられるようにするため**にハッシュが要る(切り詰めた
    # 文字列が一致しても元のクエリが同じとは限らない)。
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    query_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # `GRAPH` 句なしのクエリは承認済みの現行版だけを見る(ADR-0010 決定5)。
    # 明示した場合は他の版を読みうるので、**版の欄が不完全であることを
    # `used_graph_clause` で伝える**(ADR-0018 決定7)。
    default_graph_version: Mapped[str | None] = mapped_column(String(64), default=None)
    used_graph_clause: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    returned_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    returned_term_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TermAccessRow(Base):
    """用語ごとの参照の集約(ADR-0018 決定1)。**用語 1 件 = 1 行(更新)。**

    **イベントから導出できるが、イベントより長生きする。** 保持期間を過ぎた
    イベントを消しても「最後にいつ参照されたか」は残らなければならない
    (消した瞬間に「90 日参照されていない」が計算不能になる)。そのため
    再構築可能な派生物ではなく、独立した記録として扱う。

    **その名前空間が発行した IRI だけを記録する**(決定6)。健全性指標が
    答えたいのは「自分のオントロジーのどの用語が使われていないか」であり、
    `rdf:type` の参照回数は指標にならない。副作用として行数に**その名前空間の
    用語数で上限が付く**(エージェントの稼働に比例して増えない)。
    """

    __tablename__ = "term_access"
    __table_args__ = (
        UniqueConstraint("namespace", "term_iri", name="uq_term_access_ns_term"),
        Index("ix_term_access_namespace_last", "namespace", "last_accessed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(
        ForeignKey("namespaces.name", ondelete="CASCADE"), nullable=False
    )
    term_iri: Mapped[str] = mapped_column(String(1024), nullable=False)
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


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
