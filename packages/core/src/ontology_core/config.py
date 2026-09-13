"""環境変数から読み込む設定。

このファイルはデプロイ時に渡す環境変数名の**正本**である。
`infra/modules/*.bicep` が設定する名前と一致していなければならない。
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 既定のエンドポイントに埋め込んでいる Fuseki のポート(`P2A-13`)。
# **下の 4 つの既定値と一致していなければならない。** テストが固定している。
_DEFAULT_FUSEKI_PORT = 3030


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

    # ローカルで Fuseki を別のポートに動かすときの逃げ道(`P2A-13`)。
    #
    # **`docker-compose.yml` と `pytest` が既に同じ変数を読む。** ここで読まないと
    # 「ポート 3030 が別プロジェクトと衝突したら `FUSEKI_PORT` を指定する」という
    # 案内が `scripts/check-questions.py` に届かず、**別プロセスの Fuseki に
    # クエリが飛んで HTTP 405 になる**(実測。質問が落ちたように見えるので
    # 症状が分かりにくい)。
    #
    # **デプロイ環境では効かない。** Bicep が 4 つのエンドポイントを明示的に
    # 注入するので、下の検証で書き換えの対象外になる。
    fuseki_port: int = Field(default=_DEFAULT_FUSEKI_PORT, alias="FUSEKI_PORT")

    # ---- ソース DB のスキャン(ADR-0041、`P2A-01`) ----
    #
    # 接続を許すホスト(カンマ区切り)。**既定は空で、そのときスキャンは
    # 使えない**(決定5)。任意のホストへ接続できる口は、認証済みの主体に
    # **内部ネットワークの到達性を調べる手段**を与える(接続の成否だけで
    # 十分な情報になる)。SPARQL の `SERVICE` を既定で禁止したのと同じ判断で
    # ある。
    #
    # **「設定が無ければどこへでも」にしない**(不変条件11)。
    scan_allowed_hosts: str = Field(default="", alias="SCAN_ALLOWED_HOSTS")

    # ソース DB のパスワードを取り出す Key Vault の URL。
    # `auth_mode=key-vault-secret` のときだけ使う。**秘密の値はここにも
    # カタログにも入らない** — 名前で引いて、接続のたびに解決する(決定4)。
    scan_vault_url: str = Field(default="", alias="SCAN_VAULT_URL")

    @property
    def scan_allowed_host_list(self) -> list[str]:
        """`SCAN_ALLOWED_HOSTS` を分割して返す。"""
        return [host.strip() for host in self.scan_allowed_hosts.split(",") if host.strip()]

    # ---- オントロジー候補の生成(ADR-0043、`P2A-02`) ----
    #
    # Azure OpenAI(Microsoft Foundry)のエンドポイント。**空なら候補の生成は
    # 使えない**(不変条件11 と同じ向き — 設定が無ければ機能しない)。
    model_endpoint: str = Field(default="", alias="MODEL_ENDPOINT")

    # モデルのデプロイ名。Bicep が作ったデプロイの名前が入る。
    model_deployment: str = Field(default="", alias="MODEL_DEPLOYMENT")

    # モデル名。**監査に書くためだけに持つ**(ADR-0043 決定10)。
    # 呼び出しに使うのはデプロイ名である。
    model_name: str = Field(default="", alias="MODEL_NAME")

    # 推論の API バージョン。
    model_api_version: str = Field(default="2024-10-21", alias="MODEL_API_VERSION")

    # 1 回の呼び出しのタイムアウト(秒)。オントロジーの生成は長い出力になる。
    model_timeout_seconds: float = Field(default=180.0, alias="MODEL_TIMEOUT_SECONDS")

    # 検証に落ちたときに何回まで再試行するか(ADR-0043 決定4)。
    # **上限に達したら部分的に正しい Turtle を返さずに断る。**
    proposal_max_attempts: int = Field(default=3, ge=1, le=10, alias="PROPOSAL_MAX_ATTEMPTS")

    # 1 回の生成で渡せるテーブル数の上限(ADR-0043 決定6)。
    # **超えたら切り詰めずに 413 で断る** — 候補の Turtle には「一部である」と
    # 書く場所が無い(ADR-0034 決定4 と同じ「封筒が無い」問題)。
    proposal_max_tables: int = Field(default=40, ge=1, le=500, alias="PROPOSAL_MAX_TABLES")

    # モデルに許す出力トークン数。オントロジー 1 つ分の Turtle を収める。
    proposal_max_output_tokens: int = Field(
        default=16000, ge=1000, le=128000, alias="PROPOSAL_MAX_OUTPUT_TOKENS"
    )

    # ---- 用語のベクトル検索(ADR-0050、`P3-02`) ----
    #
    # 埋め込みモデルのデプロイ名。**空なら埋め込みの作成はできない**
    # (不変条件11 と同じ向き — 設定が無ければ機能しない)。
    #
    # **検索そのものは空でも動く。** 3-gram の経路は PostgreSQL だけで
    # 完結するので、モデルが未設定でも「表記で当てる」検索は使える。
    # そのとき応答は `vector_available: false` を返す — **「該当なし」と
    # 見分けがつかない空の結果を返さない**(ADR-0050 決定8)。
    embedding_deployment: str = Field(default="", alias="EMBEDDING_DEPLOYMENT")

    # 埋め込みモデル名。**行に保存して監査に使う** — モデルを変えたときに
    # 「どの行を作り直すべきか」を判定する唯一の手掛かりである。
    embedding_model_name: str = Field(default="", alias="EMBEDDING_MODEL_NAME")

    # 埋め込みの次元。
    #
    # **`ontology_core.embedding.EMBEDDING_DIMENSIONS` を import しない。**
    # `ontology_core/__init__.py` が config を読むので、import すると
    # **`import ontology_core` のたびに rdflib が読み込まれる**
    # (embedding は rdflib に依存する)。値の一致は
    # `test_embedding_dimensions_contract.py` が Bicep とマイグレーションも
    # 含めて機械的に突き合わせる。
    #
    # **上限を 2000 にしてある。** pgvector の hnsw 索引の上限である
    # (実測。2001 以上で `column cannot have more than 2000 dimensions`)。
    embedding_dimensions: int = Field(default=1536, ge=1, le=2000, alias="EMBEDDING_DIMENSIONS")

    # 埋め込みの API バージョン。
    embedding_api_version: str = Field(default="2024-10-21", alias="EMBEDDING_API_VERSION")

    # 1 回の呼び出しのタイムアウト(秒)。**候補の生成より短い** —
    # 埋め込みの出力は固定長で、長い生成を待つ理由が無い。
    embedding_timeout_seconds: float = Field(
        default=60.0, gt=0, le=600, alias="EMBEDDING_TIMEOUT_SECONDS"
    )

    # 3-gram の経路の閾値(ADR-0050 決定6)。
    #
    # **`similarity` ではなく `word_similarity` を使う。** 実測で、
    # `pg_trgm` の既定閾値 0.3 の `similarity` は**「顧客ID」で 1 件も
    # 当たらなかった**(長い `source_text` の中の短い部分一致は全体の
    # 類似度を上げないため)。`word_similarity` なら 1.000 で当たる。
    #
    # **既定は 0.6 — PostgreSQL 自身が `<%` に対して選んだ値である**
    # (実測: `pg_trgm.word_similarity_threshold` = 0.6、
    # `similarity_threshold` = 0.3、`strict_word_similarity_threshold` = 0.5)。
    #
    # **緩めない。** 0.3 にすると「顧客ID」で `Customer` と `customerName`
    # まで当たる(実測 0.400)。それは**概念の隣**であって表記の一致では
    # なく、ベクトルの経路の担当である。2 つの経路の役割を混ぜると、
    # `route` を返している意味が薄れる。
    search_trigram_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0, alias="SEARCH_TRIGRAM_THRESHOLD"
    )

    # ---- 仮想グラフ(Ontop VKG。ADR-0046、`P3-01`) ----
    #
    # ソースごとの SPARQL エンドポイントのテンプレート。`{namespace}` と
    # `{source}` を差し替える。**Ontop の 1 インスタンスは 1 つの DB しか
    # 見ない**(`--db-url` が 1 つ)ため、宛先はソースごとに変わる。
    #
    # **空なら仮想グラフの照会は使えない**(不変条件11 と同じ向き)。
    # 空のまま照会すると **503 で「設定されていません」と言う** —
    # 空の結果を返すと「該当する行が無い」と見分けがつかず、運用者が
    # 設定漏れに気づけない(ADR-0046 決定11)。
    vkg_endpoint_template: str = Field(default="", alias="VKG_ENDPOINT_TEMPLATE")

    # 仮想グラフへの照会のタイムアウト(秒)。**Fuseki より長くしてある** —
    # 問い合わせの先は顧客の DB であり、結合の重さはこちらで決められない。
    vkg_query_timeout_seconds: int = Field(
        default=60, ge=1, le=600, alias="VKG_QUERY_TIMEOUT_SECONDS"
    )

    # ---- SPARQL のガードレール ----
    # SERVICE 句は任意の URL へ HTTP リクエストを飛ばせるため、既定で禁止する
    # (Azure IMDS 169.254.169.254 等への SSRF を防ぐ)。
    sparql_allow_service: bool = Field(default=False, alias="SPARQL_ALLOW_SERVICE")
    # 時間の上限。Fuseki 側の arq:queryTimeout(config.ttl 等)と揃えて多層防御にする。
    # こちらは**実際に効いている**(guards.py がクエリ実行前にチェックする)。
    sparql_query_timeout_seconds: int = Field(default=30, alias="SPARQL_QUERY_TIMEOUT_SECONDS")
    # 件数の上限。**強制している**(`P2A-08`、ADR-0025)。
    #
    # クエリに `LIMIT` を後付けするのではなく、**API の境界で応答の行数を切り、
    # 切り詰めたことを必ず見せる**(ヘッダ、MCP では本文)。`LIMIT` の注入は
    # 副問い合わせや集約で意味が変わり、`CONSTRUCT` では解の数とトリプル数が
    # 違うため採らない(ADR-0025 決定1)。
    #
    # **ストア側では止められない。** Fuseki 6.2.0 / Jena ARQ 6.2.0 に行数の
    # 上限は無い(`fuseki:queryLimit` は語彙にあるが実装に読み手がおらず、
    # 実測でも効かなかった)。`arq:queryTimeout` は効くので**時間は止められるが
    # 行数は止められない**。
    #
    # **0 以下は設定の誤りとして起動時に落とす**(決定7)。「0 なら無制限」と
    # いう解釈を作らない — この設定の目的は上限をかけることである。
    sparql_max_results: int = Field(default=10_000, ge=1, alias="SPARQL_MAX_RESULTS")

    # `CONSTRUCT` / `DESCRIBE` が返すトリプル数の上限(ADR-0034 決定4、`P2A-14`)。
    #
    # **行数とは別の量である。** 行数の上限をトリプル数に流用すると、
    # 1 行が何トリプルにもなる `CONSTRUCT` で実質の上限が変わってしまう。
    #
    # **超えたら切り詰めずに 413 で断る**(行数とは違う判断)。RDF には
    # 「切り詰めた」と書く封筒が無く、ヘッダに書いてもエージェントは見ない
    # (ADR-0017 決定3)ので、**不完全なグラフが完全なものとして届く**。
    #
    # 0 以下は設定の誤りとして起動時に落とす(`SPARQL_MAX_RESULTS` と同じ)。
    sparql_max_triples: int = Field(default=50_000, ge=1, alias="SPARQL_MAX_TRIPLES")

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
    # 保持ポリシー(ADR-0019、P2B-02)。名前付きグラフに残す `superseded` の**個数**。
    #
    # **以前はローダ側の環境変数で、しかも実装は真偽値だった**(0 以外なら全部
    # 載る)。`SUPERSEDED_RETAIN=2` と書いた運用者は「直近 2 版を残す」と読むので、
    # 静かに期待と違う結果になっていた(ADR-0019 問題1)。
    #
    # **ポリシーを知る必要があるのは API だけである。** マニフェストを作るのは
    # API で、ローダは判断済みの結果を解釈するだけになった(決定1)。ローダ側の
    # 同名の環境変数は、`projection` を持たない古いマニフェストに落ちたときの
    # ためだけに残っている。
    #
    # 既定は 0(載せない)。ADR-0010 が保持ポリシーの既定値を未決として残して
    # いるため、**増やす方向の変更だけが必要**な安全側に置く。
    superseded_retain: int = Field(default=0, alias="SUPERSEDED_RETAIN")
    # 公開済み TTL(状態は draft/in-review/approved/superseded 全部含む)を置く
    # Blob のプレフィックス。ローダの BLOB_PREFIX と揃える。
    # ADR-0010 決定8: `approved/` は draft を含む全版を格納するため実態と
    # 食い違っていた。`versions/` に改名した(未リリースなので破壊的変更を許容)。
    ontology_blob_prefix: str = Field(default="versions/", alias="BLOB_PREFIX")

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

    @model_validator(mode="after")
    def _apply_fuseki_port(self) -> Self:
        """`FUSEKI_PORT` を 4 つのエンドポイントの既定に反映する(`P2A-13`)。

        **明示的に渡されたエンドポイントは書き換えない。** デプロイ環境では
        Bicep が内部 ingress の FQDN を注入するので、そちらが常に勝つ。
        `model_fields_set` は「実際に与えられたフィールド」だけを持つので、
        既定値のままかどうかをここで判定できる。

        既定値の文字列から `:3030/` を差し替える形にしているのは、**URL の形を
        2 か所に書かないため**である(形が増えると片方だけ直す事故が起きる)。
        """
        if self.fuseki_port == _DEFAULT_FUSEKI_PORT:
            return self
        for field in (
            "sparql_query_endpoint",
            "sparql_update_endpoint",
            "sparql_gsp_endpoint",
            "fuseki_admin_endpoint",
        ):
            if field in self.model_fields_set:
                continue
            current: str = getattr(self, field)
            setattr(
                self,
                field,
                current.replace(f":{_DEFAULT_FUSEKI_PORT}/", f":{self.fuseki_port}/", 1),
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """プロセス内で共有する設定インスタンスを返す。"""
    return Settings()
