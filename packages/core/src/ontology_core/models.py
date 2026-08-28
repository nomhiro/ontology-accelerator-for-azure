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
