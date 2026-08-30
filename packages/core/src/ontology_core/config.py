"""環境変数から読み込む設定。

このファイルはデプロイ時に渡す環境変数名の**正本**である。
`infra/modules/*.bicep` が設定する名前と一致していなければならない。
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthMode(StrEnum):
    """認証モード。

    `DISABLED` は**ローカル開発専用**。Entra ID にアプリ登録できない利用者が
    ひとまず動かせるようにするための逃げ道であり、デプロイ環境で使ってはならない。
    """

    ENTRA = "entra"
    DISABLED = "disabled"


class Settings(BaseSettings):
    """全サービス共通の設定。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 認証 ----
    auth_mode: AuthMode = Field(default=AuthMode.ENTRA, alias="AUTH_MODE")
    entra_tenant_id: str = Field(default="", alias="ENTRA_TENANT_ID")
    entra_api_audience: str = Field(default="", alias="ENTRA_API_AUDIENCE")

    # ---- SPARQL ストア(SPARQL 1.1 Protocol が境界) ----
    # 既存の GraphDB / Stardog / Neptune 等を使う場合はここを差し替えるだけでよい。
    #
    # `{dataset}` はリテラルのプレースホルダで、`FusekiStore._resolve` がリクエストごとに
    # 名前空間名へ置換する。名前空間ごとにデータセットを分けて物理的に隔離する設計
    # (docs/adr/0001-rdf-store-selection.md)の帰結であり、"ds" などの固定値に戻すと
    # 名前空間の隔離が機能しなくなる(すべてのクエリが同じ 1 つのデータセットに向いてしまい、
    # Task 7 以降 "ds" は空の予約データセットなので常に 0 件になる)。
    sparql_query_endpoint: str = Field(
        default="http://localhost:3030/{dataset}/sparql", alias="SPARQL_QUERY_ENDPOINT"
    )
    sparql_update_endpoint: str = Field(
        default="http://localhost:3030/{dataset}/update", alias="SPARQL_UPDATE_ENDPOINT"
    )
    sparql_gsp_endpoint: str = Field(
        default="http://localhost:3030/{dataset}/data", alias="SPARQL_GSP_ENDPOINT"
    )
    fuseki_admin_endpoint: str = Field(
        default="http://localhost:3030/$/", alias="FUSEKI_ADMIN_ENDPOINT"
    )
    fuseki_admin_user: str = Field(default="admin", alias="FUSEKI_ADMIN_USER")
    fuseki_admin_password: str = Field(default="", alias="FUSEKI_ADMIN_PASSWORD")

    # ---- SPARQL のガードレール ----
    # SERVICE 句は任意の URL へ HTTP リクエストを飛ばせるため、既定で禁止する
    # (Azure IMDS 169.254.169.254 等への SSRF を防ぐ)。
    sparql_allow_service: bool = Field(default=False, alias="SPARQL_ALLOW_SERVICE")
    # 時間の上限。Fuseki 側の arq:queryTimeout(config.ttl 等)と揃えて多層防御にする。
    # こちらは**実際に効いている**(guards.py がクエリ実行前にチェックする)。
    sparql_query_timeout_seconds: int = Field(default=30, alias="SPARQL_QUERY_TIMEOUT_SECONDS")
    # 件数の上限。**Phase 1 では未強制。** ここで値を保持し Bicep が注入しているため
    # 「効いている」と誤誘導しやすいが、`guards.py` を含めどこにも LIMIT を注入する
    # 実装が無い(ブランチ全体レビュー M-2)。任意の SPARQL に LIMIT を後付けするのは
    # 副問い合わせや CONSTRUCT で壊れるため安価な強制手段が無く、Phase 2 で対応する。
    # README.md の「動作を確認済み(ローカル)」節にも同じ注記がある。
    sparql_max_results: int = Field(default=10_000, alias="SPARQL_MAX_RESULTS")

    # ---- 正本(PostgreSQL) ----
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_database: str = Field(default="ontology", alias="POSTGRES_DATABASE")
    postgres_user: str = Field(default="ontology", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")
    # 接続文字列を直接与えたい場合に使う。空なら postgres_* から組み立てる。
    database_url: str = Field(default="", alias="DATABASE_URL")

    # ---- 正本(Blob: バージョン付き TTL) ----
    azure_storage_account_url: str = Field(default="", alias="AZURE_STORAGE_ACCOUNT_URL")
    # ローカル専用: Azurite への接続文字列。設定されていれば `from_account_url` +
    # DefaultAzureCredential より優先する(dependencies.py の blob_store を参照)。
    # Azurite は HTTP・共有キー認証のみで DefaultAzureCredential を受け付けないため、
    # これが無いとローカルで Blob 依存の経路(publish / versions / delete)を
    # 一切動かせない(final-fix-brief.md 修正1 / I-1)。デプロイ環境では設定しないこと
    # (Bicep はマネージド ID 用に AZURE_STORAGE_ACCOUNT_URL だけを注入する)。
    azure_storage_connection_string: str = Field(
        default="", alias="AZURE_STORAGE_CONNECTION_STRING"
    )
    ontology_blob_container: str = Field(default="ontologies", alias="ONTOLOGY_BLOB_CONTAINER")
    # 名前付きグラフ IRI の接頭辞。containers/fuseki/load-snapshot.sh と
    # infra/modules/fuseki.bicep の graphIriBase と同じ値でなければならない。
    graph_iri_base: str = Field(default="urn:ontology:graph", alias="GRAPH_IRI_BASE")
    # 承認済み TTL を置く Blob のプレフィックス。ローダの BLOB_PREFIX と揃える。
    ontology_blob_prefix: str = Field(default="approved/", alias="BLOB_PREFIX")

    # ---- Azure 共通 ----
    azure_client_id: str = Field(default="", alias="AZURE_CLIENT_ID")
    applicationinsights_connection_string: str = Field(
        default="", alias="APPLICATIONINSIGHTS_CONNECTION_STRING"
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ---- MCP サーバー ----
    mcp_read_only: bool = Field(default=True, alias="MCP_READ_ONLY")
    core_api_url: str = Field(default="http://localhost:8000", alias="CORE_API_URL")

    # MCP の Streamable HTTP トランスポートが受け付ける Host ヘッダ(カンマ区切り)。
    # SDK は DNS リバインディング対策として Host を検証し、既定では 127.0.0.1 しか
    # 許可しない。Container Apps の FQDN でアクセスすると 421 Invalid Host header に
    # なるため、デプロイ時は Bicep が自身の FQDN を設定する。
    # 空のときは検証を無効化する(ローカル開発とポートフォワード経由の利用のため)。
    mcp_allowed_hosts: str = Field(default="", alias="MCP_ALLOWED_HOSTS")

    @property
    def mcp_allowed_host_list(self) -> list[str]:
        """`MCP_ALLOWED_HOSTS` を分割して返す。"""
        return [host.strip() for host in self.mcp_allowed_hosts.split(",") if host.strip()]

    @property
    def async_postgres_dsn(self) -> str:
        """SQLAlchemy async エンジン用の DSN。DSN の正本はこの property のみ。

        `DATABASE_URL` が与えられていればそれを優先する。パスワードが空の場合は
        Entra ID のトークンを実行時に取得して渡すため、DSN にはパスワードを含めない。
        Entra ID のトークンには有効期限があり、DSN 文字列に一度だけ埋め込むと
        期限切れ後に接続できなくなる。そのためパスワード(または Entra トークン)は
        DSN ではなく SQLAlchemy の `connect_args["password"]` に callable を渡し、
        接続のたびに評価させる方式を取る(asyncpg は callable を受け付ける)。
        DSN に平文パスワードを埋め込む実装に戻さないこと。
        """
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_database}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """プロセス内で共有する設定インスタンスを返す。"""
    return Settings()
