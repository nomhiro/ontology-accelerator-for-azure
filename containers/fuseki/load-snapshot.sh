#!/bin/sh
# init コンテナのエントリポイント。
#
# 正本(Blob 上のバージョン付き TTL)からローカルの TDB2 を作り直す。
# トリプルストアは再構築可能な射影であるという設計の実装部分にあたる。
# docs/adr/0002-triple-store-as-rebuildable-projection.md を参照。
#
# 環境変数:
#   AZURE_STORAGE_ACCOUNT_URL   例: https://stxxxx.blob.core.windows.net
#   ONTOLOGY_BLOB_CONTAINER     例: ontologies
#   AZURE_CLIENT_ID             ユーザー割り当てマネージド ID のクライアント ID
#   TDB_LOCATION                TDB2 の作成先(EmptyDir 上のパス)
#   LOCAL_TTL_DIR               ローカル開発用。Blob の代わりにここから読む
set -eu

: "${ONTOLOGY_BLOB_CONTAINER:=ontologies}"
: "${TDB_LOCATION:=/fuseki/databases/ds}"
: "${STAGING_DIR:=/fuseki/staging}"
: "${BLOB_PREFIX:=approved/}"
: "${BLOB_API_VERSION:=2021-12-02}"
: "${JENA_HOME:=/opt/jena}"
# 名前付きグラフ IRI の接頭辞。Phase 1 で名前空間とバージョンを含む形に整える。
#
# デプロイ環境では infra/modules/fuseki.bicep の graphIriBase パラメータが
# この値を上書きする。ここの既定値を変えるときは Bicep 側も合わせること
# (グラフ IRI の体系はバージョン管理の中心的な値なので、IaC 側で明示している)。
: "${GRAPH_IRI_BASE:=urn:ontology:graph}"
# 既存の TDB2 があるとき再構築せずそのまま使うか。
#
# graphPersistence=azureFiles は「ストア自体を正本にしたい」利用者向けの構成で、
# そこで毎回 Blob から作り直すとストア側の更新を失う。Bicep がそのモードでのみ
# true を渡す。既定 (ephemeral) では毎回作り直す。
: "${PRESERVE_EXISTING_TDB:=false}"

log() { echo "load-snapshot: $*" >&2; }

# STAGING_DIR に集めた TTL から TDB2 を構築して所定の位置へ差し替える。
#
# 各 TTL は**名前付きグラフ**として読み込む。オントロジーのバージョンごとにグラフを
# 分けることで、エージェントがバージョンを固定して参照できるようにするため
# (docs/adr/0006-ontology-versioning-and-audit.md)。
#
# なお config.ttl で unionDefaultGraph を有効にしているため、既定グラフへの
# クエリは「名前付きグラフの和集合」を見る。既定グラフに直接読み込むと
# 逆に見えなくなるので、必ず --graph を指定すること。
build_tdb() {
    # 途中で失敗した TDB2 を Fuseki に読ませないよう、別の場所に作ってから差し替える。
    build_location="${TDB_LOCATION}.building"
    rm -rf "${build_location}" "${TDB_LOCATION}.old"
    mkdir -p "${build_location}"

    log "TDB2 を構築します: ${build_location}"
    for ttl in "${STAGING_DIR}"/*.ttl; do
        [ -e "${ttl}" ] || continue
        graph_iri="${GRAPH_IRI_BASE}/$(basename "${ttl}" .ttl)"
        log "読み込み: $(basename "${ttl}") -> ${graph_iri}"
        "${JENA_HOME}/bin/tdb2.tdbloader" \
            --loc="${build_location}" \
            --graph="${graph_iri}" \
            "${ttl}"
    done

    if [ -d "${TDB_LOCATION}" ]; then
        mv "${TDB_LOCATION}" "${TDB_LOCATION}.old"
    fi
    mv "${build_location}" "${TDB_LOCATION}"
    rm -rf "${TDB_LOCATION}.old" "${STAGING_DIR}"

    log "完了しました: ${TDB_LOCATION}"
}

# 何も読み込むものが無かったときに空の TDB2 を用意する。
prepare_empty_tdb() {
    mkdir -p "${TDB_LOCATION}"
    rm -rf "${STAGING_DIR}"
}

# ---------------------------------------------------------------------------
# 既存の TDB2 を温存する構成なら何もしない
# ---------------------------------------------------------------------------
if [ "${PRESERVE_EXISTING_TDB}" = "true" ] && [ -d "${TDB_LOCATION}" ] \
    && [ -n "$(ls -A "${TDB_LOCATION}" 2>/dev/null)" ]; then
    log "既存の TDB2 を温存します (PRESERVE_EXISTING_TDB=true): ${TDB_LOCATION}"
    exit 0
fi

rm -rf "${STAGING_DIR}"
mkdir -p "${STAGING_DIR}" "$(dirname "${TDB_LOCATION}")"

# ---------------------------------------------------------------------------
# ローカル開発: Blob の代わりにマウントしたディレクトリから読む
# ---------------------------------------------------------------------------
if [ -z "${AZURE_STORAGE_ACCOUNT_URL:-}" ]; then
    if [ -n "${LOCAL_TTL_DIR:-}" ] && [ -d "${LOCAL_TTL_DIR}" ]; then
        log "ローカルの TTL を読み込みます: ${LOCAL_TTL_DIR}"
        found=0
        for ttl in "${LOCAL_TTL_DIR}"/*.ttl; do
            [ -e "${ttl}" ] || continue
            cp "${ttl}" "${STAGING_DIR}/"
            found=1
        done
        if [ "${found}" -eq 1 ]; then
            build_tdb
            exit 0
        fi
        log "${LOCAL_TTL_DIR} に TTL が見つかりませんでした"
    else
        log "AZURE_STORAGE_ACCOUNT_URL が未設定のため取得をスキップします(ローカル開発想定)"
    fi
    prepare_empty_tdb
    exit 0
fi

# ---------------------------------------------------------------------------
# マネージド ID でストレージ用のトークンを取得する
# ---------------------------------------------------------------------------
# 取得方式が実行環境で違う点に注意。
#   - Container Apps / App Service / Functions:
#       IDENTITY_ENDPOINT に X-IDENTITY-HEADER を付けて要求する。
#       169.254.169.254 の IMDS は**応答しない**。
#   - VM / AKS / スケールセット:
#       169.254.169.254 の IMDS を Metadata: true ヘッダで叩く。
# 前者を優先し、無い環境では後者にフォールバックする。
token_resource="https%3A%2F%2Fstorage.azure.com%2F"

fetch_token() {
    if [ -n "${IDENTITY_ENDPOINT:-}" ] && [ -n "${IDENTITY_HEADER:-}" ]; then
        log "マネージド ID のトークンを取得します (IDENTITY_ENDPOINT 方式)"
        token_url="${IDENTITY_ENDPOINT}?api-version=2019-08-01&resource=${token_resource}"
        if [ -n "${AZURE_CLIENT_ID:-}" ]; then
            token_url="${token_url}&client_id=${AZURE_CLIENT_ID}"
        fi
        curl -fsSL -H "X-IDENTITY-HEADER: ${IDENTITY_HEADER}" "${token_url}"
        return
    fi

    log "マネージド ID のトークンを取得します (IMDS 方式)"
    token_url="http://169.254.169.254/metadata/identity/oauth2/token"
    token_url="${token_url}?api-version=2018-02-01&resource=${token_resource}"
    if [ -n "${AZURE_CLIENT_ID:-}" ]; then
        token_url="${token_url}&client_id=${AZURE_CLIENT_ID}"
    fi
    curl -fsSL -H 'Metadata: true' "${token_url}"
}

token=$(fetch_token | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')

if [ -z "${token}" ]; then
    log "トークンの取得に失敗しました。マネージド ID の割り当てとロール付与を確認してください"
    exit 1
fi

# ---------------------------------------------------------------------------
# 承認済み TTL の一覧を取得して取り出す
# ---------------------------------------------------------------------------
container_url="${AZURE_STORAGE_ACCOUNT_URL%/}/${ONTOLOGY_BLOB_CONTAINER}"
list_url="${container_url}?restype=container&comp=list&prefix=${BLOB_PREFIX}"

log "Blob の一覧を取得します: ${container_url} (prefix=${BLOB_PREFIX})"
blob_names=$(curl -fsSL \
    -H "Authorization: Bearer ${token}" \
    -H "x-ms-version: ${BLOB_API_VERSION}" \
    "${list_url}" \
    | tr '<' '\n' | sed -n 's/^Name>\(.*\.ttl\)$/\1/p')

if [ -z "${blob_names}" ]; then
    log "承認済みのオントロジーが見つかりませんでした。空の TDB2 を作成します"
    prepare_empty_tdb
    exit 0
fi

count=0
for name in ${blob_names}; do
    target="${STAGING_DIR}/$(echo "${name}" | tr '/' '_')"
    log "取得: ${name}"
    curl -fsSL \
        -H "Authorization: Bearer ${token}" \
        -H "x-ms-version: ${BLOB_API_VERSION}" \
        -o "${target}" \
        "${container_url}/${name}"
    count=$((count + 1))
done
log "${count} 件の TTL を取得しました"

build_tdb
