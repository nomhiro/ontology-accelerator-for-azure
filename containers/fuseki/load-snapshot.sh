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
#   FUSEKI_BASE                 Fuseki のサーバーエリア(既定 /fuseki)
#   TDB_LOCATION                予約データセット "ds" の作成先(EmptyDir 上のパス)
#   LOCAL_TTL_DIR               ローカル開発用。Blob の代わりにここから読む
#
# 名前空間ごとに Blob レイアウト <prefix><namespace>/<version>.ttl(Task 5)に
# 従って TDB2 と assembler を作る(Task 7)。詳細は build_tdb を参照。
set -eu

# 名前空間名・バージョン文字列の検証(validate_namespace / validate_version_file)
# と log() は lib/validate.sh に切り出してある。副作用の無い検証関数だけを
# 単体テストできるようにするため(lib/validate.test.sh)。
script_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "${script_dir}/lib/validate.sh"

: "${ONTOLOGY_BLOB_CONTAINER:=ontologies}"
: "${FUSEKI_BASE:=/fuseki}"
# 何も読み込むものが無いときに空のまま用意する予約データセットの場所。
# config.ttl が静的に定義する "ds"(ontology_core.graphs.RESERVED_DATASET_NAMES)
# に対応する。名前空間ごとの実データは DATABASES_DIR/<namespace> に置くため、
# ここは常に空のままでよい。
: "${TDB_LOCATION:=/fuseki/databases/ds}"
# 名前空間ごとの TDB2 と assembler の生成先。Fuseki は起動時に
# CONFIGURATION_DIR/*.ttl を読み込むため(Jena 公式のディレクトリ規約)、
# これで名前空間ごとのデータセットが立ち上がる。
: "${DATABASES_DIR:=${FUSEKI_BASE}/databases}"
: "${CONFIGURATION_DIR:=${FUSEKI_BASE}/configuration}"
: "${STAGING_DIR:=/fuseki/staging}"
: "${BLOB_PREFIX:=approved/}"
: "${BLOB_API_VERSION:=2021-12-02}"
: "${JENA_HOME:=/opt/jena}"
# ローカル開発 (LOCAL_TTL_DIR) のサンプルには Blob のようなバージョン付きの
# ディレクトリ階層が無い(例: samples/retail-core.ttl は <namespace>.ttl のみ)。
# ファイル名を名前空間、この値をバージョンとして割り当てる。containers/fuseki の
# task-8 相当の本番デプロイ手順が同じファイルを approved/retail-core/1.0.0.ttl
# としてアップロードする想定と合わせている。
: "${LOCAL_TTL_VERSION:=1.0.0}"
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
#
# 名前空間ごとに DATABASES_DIR/<namespace> を持つようになったため、判定は
# TDB_LOCATION(予約データセット "ds" のみ)ではなく DATABASES_DIR 全体を見て、
# 名前空間のディレクトリが 1 つでも残っていれば温存する。
: "${PRESERVE_EXISTING_TDB:=false}"

# 名前空間ごとの assembler を書き出す。Fuseki は起動時に CONFIGURATION_DIR/*.ttl
# を読み込むため、これで名前空間ごとのデータセットが立ち上がる。
#
# クエリ/更新タイムアウトと unionDefaultGraph は config.ttl の :dataset(予約
# データセット "ds")と同じ値にしている。admin API 経由の動的なデータセット作成
# (FusekiStore.create_dataset)にはここが適用されないため、そちらは
# containers/fuseki/templates/config-tdb2 で別途カバーする(Task 7 Step 1b)。
write_assembler() {
    namespace="$1"
    location="$2"
    cat > "${CONFIGURATION_DIR}/${namespace}.ttl" <<EOF
@prefix fuseki: <http://jena.apache.org/fuseki#> .
@prefix ja:     <http://jena.hpl.hp.com/2005/11/Assembler#> .
@prefix rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix tdb2:   <http://jena.apache.org/2016/tdb#> .

<#service> rdf:type fuseki:Service ;
    fuseki:name "${namespace}" ;
    fuseki:endpoint [ fuseki:operation fuseki:query  ; fuseki:name "sparql" ] ;
    fuseki:endpoint [ fuseki:operation fuseki:query  ; fuseki:name "query"  ] ;
    fuseki:endpoint [ fuseki:operation fuseki:update ; fuseki:name "update" ] ;
    fuseki:endpoint [ fuseki:operation fuseki:gsp-rw ; fuseki:name "data"   ] ;
    fuseki:dataset <#dataset> .

<#dataset> rdf:type tdb2:DatasetTDB2 ;
    tdb2:location "${location}" ;
    ja:context [ ja:cxtName "arq:queryTimeout"  ; ja:cxtValue "10000,30000" ] ;
    ja:context [ ja:cxtName "arq:updateTimeout" ; ja:cxtValue "20000,60000" ] ;
    ja:context [ ja:cxtName "tdb2:unionDefaultGraph" ; ja:cxtValue true ] .
EOF
}

# STAGING_DIR/<namespace>/<version>.ttl から名前空間ごとに TDB2 を構築し、
# DATABASES_DIR/<namespace> に差し替えて CONFIGURATION_DIR/<namespace>.ttl を書く。
#
# 名前空間は Fuseki のデータセット名・Blob のパスと同じ隔離単位である
# (docs/adr/0001-rdf-store-selection.md)。データセット単位で TDB2 を分けることで、
# 別の名前空間のグラフを明示的に GRAPH で名指ししても届かないようにする。
#
# 各 TTL(=バージョン)は名前空間データセットの中で**名前付きグラフ**として読み込む。
# バージョンごとにグラフを分けることで、エージェントがバージョンを固定して
# 参照できるようにするため(docs/adr/0006-ontology-versioning-and-audit.md)。
#
# 名前空間ごとの assembler(write_assembler が書く)で unionDefaultGraph を
# 有効にしているため、既定グラフへのクエリは「その名前空間内の名前付きグラフの
# 和集合」を見る。既定グラフに直接読み込むと逆に見えなくなるので、
# 必ず --graph を指定すること。
build_tdb() {
    for ns_dir in "${STAGING_DIR}"/*/; do
        [ -d "${ns_dir}" ] || continue
        build_namespace_tdb "$(basename "${ns_dir}")" "${ns_dir}"
    done
    rm -rf "${STAGING_DIR}"
}

# 1 つの名前空間の TDB2 を構築して差し替え、assembler を書く。
build_namespace_tdb() {
    namespace="$1"
    ns_staging="$2"
    location="${DATABASES_DIR}/${namespace}"
    # 途中で失敗した TDB2 を Fuseki に読ませないよう、別の場所に作ってから差し替える。
    build_location="${location}.building"
    rm -rf "${build_location}" "${location}.old"
    mkdir -p "${build_location}"

    log "TDB2 を構築します [${namespace}]: ${build_location}"
    for ttl in "${ns_staging}"*.ttl; do
        [ -e "${ttl}" ] || continue
        version="$(basename "${ttl}" .ttl)"
        graph_iri="${GRAPH_IRI_BASE}/${namespace}/${version}"
        log "読み込み [${namespace}]: $(basename "${ttl}") -> ${graph_iri}"
        "${JENA_HOME}/bin/tdb2.tdbloader" \
            --loc="${build_location}" \
            --graph="${graph_iri}" \
            "${ttl}"
    done

    if [ -d "${location}" ]; then
        mv "${location}" "${location}.old"
    fi
    mv "${build_location}" "${location}"
    rm -rf "${location}.old"

    write_assembler "${namespace}" "${location}"
    log "完了しました [${namespace}]: ${location}"
}

# 何も読み込むものが無かったときに予約データセット "ds" を空のまま用意する。
# CONFIGURATION_DIR は前回までの実行分をすべて捨ててから空で作り直す
# (トリプルストアは再構築可能な射影であり、今回の Blob に無い名前空間の
# assembler を残さない。docs/adr/0002-triple-store-as-rebuildable-projection.md)。
prepare_empty_tdb() {
    mkdir -p "${TDB_LOCATION}"
    rm -rf "${CONFIGURATION_DIR}"
    mkdir -p "${CONFIGURATION_DIR}"
    rm -rf "${STAGING_DIR}"
}

# ---------------------------------------------------------------------------
# 既存の TDB2 を温存する構成なら TDB2 は再構築せず、CONFIGURATION_DIR だけ復元する
# ---------------------------------------------------------------------------
# infra/modules/fuseki.bicep がボリュームマウントするのは DATABASES_DIR
# (FUSEKI_BASE/databases)だけで、CONFIGURATION_DIR(FUSEKI_BASE/configuration)は
# コンテナのエフェメラルな書き込み層にある。そのためコンテナが再作成されると
# TDB2(databases/<namespace>)はボリューム上に残るが、対応する assembler は
# ゼロから始まる。ここで何もせず exit すると、Fuseki は名前空間データセットの
# 存在を知らないまま起動し、以後そのレプリカでは予約データセット "ds"(空)
# しか応答しなくなる — データは物理的に残っているのに完全に見えなくなる。
# そのため温存モードでも、既存の名前空間ディレクトリごとに assembler だけは
# 書き直す(TDB2 自体は再構築しない。温存の目的はそこにあるため)。
if [ "${PRESERVE_EXISTING_TDB}" = "true" ]; then
    found_existing=0
    for existing in "${DATABASES_DIR}"/*/; do
        [ -d "${existing}" ] || continue
        existing_name="$(basename "${existing}")"
        if [ "${existing_name}" = "ds" ]; then
            continue
        fi
        if [ -z "$(ls -A "${existing}" 2>/dev/null)" ]; then
            continue
        fi
        found_existing=1
    done

    if [ "${found_existing}" -eq 1 ]; then
        log "既存の TDB2 を温存します (PRESERVE_EXISTING_TDB=true): ${DATABASES_DIR}"
        mkdir -p "${CONFIGURATION_DIR}"
        for existing in "${DATABASES_DIR}"/*/; do
            [ -d "${existing}" ] || continue
            existing_name="$(basename "${existing}")"
            if [ "${existing_name}" = "ds" ]; then
                continue
            fi
            if [ -z "$(ls -A "${existing}" 2>/dev/null)" ]; then
                continue
            fi
            if ! validate_namespace "${existing_name}"; then
                continue
            fi
            write_assembler "${existing_name}" "${existing%/}"
            log "assembler を復元しました [${existing_name}]"
        done
        exit 0
    fi
fi

# CONFIGURATION_DIR は前回までの実行分をすべて捨ててから空で作り直す
# (今回の Blob に無い名前空間の assembler を残さないため。DATABASES_DIR の
# 名前空間ディレクトリ自体は残っても、対応する assembler が無ければ Fuseki からは
# 見えないので、そちらは build_namespace_tdb の名前空間単位の差し替えに任せる)。
rm -rf "${STAGING_DIR}" "${CONFIGURATION_DIR}"
mkdir -p "${STAGING_DIR}" "${DATABASES_DIR}" "${CONFIGURATION_DIR}"

# ---------------------------------------------------------------------------
# ローカル開発: Blob の代わりにマウントしたディレクトリから読む
# ---------------------------------------------------------------------------
if [ -z "${AZURE_STORAGE_ACCOUNT_URL:-}" ]; then
    if [ -n "${LOCAL_TTL_DIR:-}" ] && [ -d "${LOCAL_TTL_DIR}" ]; then
        log "ローカルの TTL を読み込みます: ${LOCAL_TTL_DIR}"
        found=0
        for ttl in "${LOCAL_TTL_DIR}"/*.ttl; do
            [ -e "${ttl}" ] || continue
            namespace="$(basename "${ttl}" .ttl)"
            if ! validate_namespace "${namespace}"; then
                continue
            fi
            mkdir -p "${STAGING_DIR}/${namespace}"
            cp "${ttl}" "${STAGING_DIR}/${namespace}/${LOCAL_TTL_VERSION}.ttl"
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
# `blob_names` は改行区切りだが、Blob 名自体に空白を含み得る。無引用の
# `for name in ${blob_names}` は IFS でワード分割するため、空白を含む Blob 名が
# 2 つの偽名に分かれて存在しないパスへの `curl -f` が失敗し、`set -e` で
# ローダ全体が落ちてしまう(1 個の Blob 名のせいで全名前空間が読み込めなくなる)。
# 「Blob 名を信用しない」という同じテーマのため、改行だけを区切りとして扱う
# `while read` に変える。パイプ(`|`)ではなくヒアドキュメント(`<<`)で渡すことで
# サブシェルを作らず、ループ内で更新する `count` がループの外でも見える。
while IFS= read -r name; do
    [ -n "${name}" ] || continue
    # Blob レイアウトは <prefix><namespace>/<version>.ttl(ontology_core.blob)。
    # BLOB_PREFIX を取り除いた残りを名前空間とバージョンファイルに分解する。
    relative="${name#"${BLOB_PREFIX}"}"
    namespace="${relative%%/*}"
    version_file="${relative#*/}"
    if [ "${namespace}" = "${relative}" ] || [ -z "${version_file}" ]; then
        log "名前空間の階層が無い Blob をスキップします: ${name}"
        continue
    fi
    if ! validate_namespace "${namespace}"; then
        continue
    fi
    # version_file は Blob の一覧応答からそのまま来る値で、namespace と違って
    # 文字種を検証していなかった。Blob 名を "approved/alpha/../../evil.ttl" の
    # ように作ると namespace="alpha"(検証通過)、version_file="../../evil.ttl"
    # となり、curl -o が STAGING_DIR の外へ任意のファイルを書き込めてしまう。
    if ! validate_version_file "${version_file}"; then
        continue
    fi
    mkdir -p "${STAGING_DIR}/${namespace}"
    target="${STAGING_DIR}/${namespace}/${version_file}"
    log "取得: ${name}"
    curl -fsSL \
        -H "Authorization: Bearer ${token}" \
        -H "x-ms-version: ${BLOB_API_VERSION}" \
        -o "${target}" \
        "${container_url}/${name}"
    count=$((count + 1))
done <<BLOB_NAMES
${blob_names}
BLOB_NAMES
log "${count} 件の TTL を取得しました"

build_tdb
