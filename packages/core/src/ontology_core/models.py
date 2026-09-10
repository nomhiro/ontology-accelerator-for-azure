"""ドメインモデル。

正本(PostgreSQL / Blob)に保存する概念をここで定義する。
永続化のためのテーブル定義は Phase 1 で `ontology_api` 側に追加する。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class NamespaceRole(StrEnum):
    """名前空間ごとに割り当てるロール。

    Entra ID のアプリロールは粗粒度(プラットフォーム管理者か一般利用者か)に留め、
    名前空間との組み合わせは正本の PostgreSQL で管理して API 層で強制する。
    """

    OWNER = "owner"
    MAINTAINER = "maintainer"
    DATA_STEWARD = "data-steward"
    DATA_ANALYST = "data-analyst"

    @property
    def rank(self) -> int:
        """順序。**上位は下位のすべてを含む**(ADR-0014 決定2)。

        ロールを増やすほど「誰が何をできるか」を人が把握できなくなるため、
        4 段の順序付きに保つ。順序があれば「上位は下位を含む」の一言で
        説明できる。
        """
        return _NAMESPACE_ROLE_RANK[self]

    def covers(self, required: NamespaceRole) -> bool:
        """このロールが `required` を満たすか。"""
        return self.rank >= required.rank


_NAMESPACE_ROLE_RANK: dict[NamespaceRole, int] = {
    NamespaceRole.DATA_ANALYST: 0,
    NamespaceRole.DATA_STEWARD: 1,
    NamespaceRole.MAINTAINER: 2,
    NamespaceRole.OWNER: 3,
}


class PlatformRole(StrEnum):
    """テナント全体に対するロール。"""

    PLATFORM_ADMIN = "platform-admin"
    PLATFORM_VIEWER = "platform-viewer"


class OntologyVersionStatus(StrEnum):
    """オントロジーのバージョンの状態。"""

    DRAFT = "draft"
    IN_REVIEW = "in-review"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class NamespaceRoleAssignment(BaseModel):
    """名前空間へのロール付与(ADR-0014)。"""

    model_config = ConfigDict(frozen=True)

    namespace: str
    principal_id: str = Field(description="Entra のオブジェクト ID")
    role: NamespaceRole
    granted_at: datetime
    granted_by: str


class TermOwner(BaseModel):
    """用語単位の責任者(ADR-0015 決定1)。

    **`namespace_roles` の `owner` ロールとは別の概念である。** ロールは
    「何ができるか」(権限)、責任者は「誰が説明責任を負うか」である。
    名前空間単位の責任者は `owner` ロールが担う(ADR-0014)。用語単位こそが、
    権限では表現できない粒度である。
    """

    model_config = ConfigDict(frozen=True)

    namespace: str
    term_iri: str = Field(description="責任の対象となる用語の絶対 IRI")
    principal_id: str = Field(description="Entra のオブジェクト ID")
    assigned_at: datetime
    assigned_by: str


class OwnerResolutionSource(StrEnum):
    """問い合わせ先をどこから解決したか(ADR-0015 決定2)。

    **代替で解決したことを必ず見せる。** 見えなければ `P2B-06` の
    健全性指標が「責任者が未設定の用語」を数えられない。
    """

    TERM_OWNER = "term-owner"
    NAMESPACE_OWNERS = "namespace-owners"
    UNRESOLVED = "unresolved"


class OwnerResolution(BaseModel):
    """「この用語は誰に聞けばよいか」の答え(ADR-0015 決定2)。"""

    model_config = ConfigDict(frozen=True)

    namespace: str
    term_iri: str
    source: OwnerResolutionSource
    principal_ids: tuple[str, ...] = Field(
        default=(),
        description="問い合わせ先の Entra オブジェクト ID。"
        "`term-owner` なら 1 件、`namespace-owners` なら 1 件以上、"
        "`unresolved` なら 0 件",
    )


class AccessEvent(BaseModel):
    """コンテキストのアクセスログの 1 件(ADR-0018 決定1)。

    **`AuditEvent` とは別物である。** あちらは人の決定(誰が承認したか)、
    こちらは機械の参照(エージェントに何を渡したか)を記録する。ADR-0006 は
    後者を「監査の最後のピース」と呼んでいる — オントロジーの履歴が完全でも、
    実際に何が渡ったかが分からなければ判断の説明は完結しない。
    """

    model_config = ConfigDict(frozen=True)

    namespace: str
    actor: str = Field(description="Entra のオブジェクト ID")
    occurred_at: datetime
    query_text: str = Field(description="クエリ本文(長い場合は切り詰められる)")
    query_hash: str = Field(description="**全文**の SHA-256。同じクエリをまとめるのに使う")
    query_truncated: bool = Field(description="`query_text` が切り詰められているか")
    default_graph_version: str | None = Field(
        default=None,
        description="既定グラフの版(承認済みの現行版)。"
        "**`used_graph_clause` が真なら、これは読んだ版の全体ではない**",
    )
    used_graph_clause: bool = Field(
        description="クエリが `GRAPH` 句を含むか。真なら版の記録が不完全である"
    )
    returned_row_count: int
    returned_term_count: int = Field(
        description="返した用語のうち、その名前空間が発行した IRI の数"
    )


class AccessPage(BaseModel):
    """アクセスログの 1 ページ。`AuditPage` と同じ形。"""

    model_config = ConfigDict(frozen=True)

    events: tuple[AccessEvent, ...]
    next_cursor: int | None = Field(
        default=None,
        description="次のページを取るときに `cursor` へ渡す値。`None` なら最後のページ",
    )


class TermAccess(BaseModel):
    """用語ごとの参照の集約(ADR-0018 決定1)。

    **イベントより長生きする。** 保持期間を過ぎたイベントを消しても
    「最後にいつ参照されたか」は残らなければならない。
    """

    model_config = ConfigDict(frozen=True)

    namespace: str
    term_iri: str
    last_accessed_at: datetime
    access_count: int


class Namespace(BaseModel):
    """オントロジーを隔離する単位。

    Fuseki 側では名前空間ごとに独立したデータセットを割り当てる。任意の SPARQL を
    名前付きグラフへ書き換えて閉じ込める実装は `GRAPH` / `SERVICE` 句で回避され得るため
    採らない(`docs/adr/0001-rdf-store-selection.md`)。
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$", description="スラッグ形式の識別子")
    display_name: str
    description: str = ""
    base_iri: str = Field(description="このオントロジーが発行する IRI の接頭辞")
    created_at: datetime
    created_by: str
    # 四眼原則(ADR-0014 決定4)。既定は有効。
    require_two_person_approval: bool = True


class OntologyVersion(BaseModel):
    """不変のリビジョン。

    承認済みの成果物は Blob 上のバージョン付き TTL が正本であり、トリプルストアには
    バージョンごとの名前付きグラフとして射影する。エージェントはバージョンを固定して
    参照できる。
    """

    model_config = ConfigDict(frozen=True)

    namespace: str
    version: str = Field(description="semver。例: 1.4.0")
    content_hash: str = Field(description="TTL 本体の SHA-256。同一性の判定に使う")
    status: OntologyVersionStatus
    graph_iri: str = Field(description="射影先の名前付きグラフ IRI")
    blob_path: str = Field(description="正本 TTL の Blob 上のパス")
    created_at: datetime
    created_by: str
    approved_at: datetime | None = None
    approved_by: str | None = None
    # 射影(Fuseki への反映)が完了した時刻。NULL なら未射影で reconcile の対象。
    projected_at: datetime | None = None


class AuditEvent(BaseModel):
    """監査証跡の 1 件。

    「誰が・いつ・何を・なぜ」を記録する。表現には W3C PROV-O を用いて
    W3C 標準忠実の方針と揃える(`docs/adr/0006-ontology-versioning-and-audit.md`)。
    """

    model_config = ConfigDict(frozen=True)

    namespace: str
    action: str = Field(description="proposed / approved / rejected / published など")
    actor: str = Field(description="Entra ID のオブジェクト ID もしくはサービスプリンシパル")
    occurred_at: datetime
    subject: str = Field(description="対象。オントロジーのバージョンやマッピングの識別子")
    reason: str = ""
    diff: str | None = Field(default=None, description="前バージョンとの差分")


class AuditPage(BaseModel):
    """監査照会の 1 ページ(`P2B-11`)。

    **`next_cursor` が `None` なら、それが最後のページである。** 「返った件数が
    `limit` より少ないから最後」という判定に頼らせない — 境界ちょうどのときに
    余分な 1 往復が要るだけでなく、**呼び出し側が判定を間違える**。
    """

    model_config = ConfigDict(frozen=True)

    events: tuple[AuditEvent, ...]
    next_cursor: int | None = Field(
        default=None,
        description="次のページを取るときに `cursor` へ渡す値。`None` なら最後のページ",
    )
