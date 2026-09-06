#!/bin/sh
# load-snapshot.sh の制御フローに対するテスト(P1-21)。
#
# **なぜ関数を source せずスクリプト全体を実行するのか**
#
# load-snapshot.sh は Blob 一覧の取得や TDB2 の構築など副作用のあるコードを
# トップレベルに持つため、そのまま source して個別の関数を呼ぶことができない
# (lib/validate.test.sh の冒頭コメントに同じ説明がある)。ここで検証したいのは
# 「マニフェストが取得できない・不正な名前空間を丸ごとスキップし、**他の
# 名前空間の読み込みは継続する**」という制御フローそのものであり、
# 純粋関数に切り出せる性質のものではない(curl の失敗と `set -eu` の相互作用が
# 検証対象に含まれる)。
#
# そのため `curl` と `tdb2.tdbloader` をスタブに差し替えて、スクリプトを
# 丸ごと実行し、結果(どの名前空間の assembler が書かれたか)で判定する。
# スタブに差し替えるのは外部プロセスの起動だけで、スクリプト自身のロジックは
# 一切書き換えない。
#
# 実行方法: sh containers/fuseki/load-snapshot.test.sh
#   jq が必要(load-snapshot.sh 自身がマニフェストの解析に使う)。
#   ローカルに jq が無い場合は docker 経由で実行する:
#     docker run --rm -v "$(pwd):/w" -w /w alpine:3.20 sh -c \
#       'apk add --no-cache jq >/dev/null && sh containers/fuseki/load-snapshot.test.sh'
set -eu

script_dir="$(cd "$(dirname "$0")" && pwd)"
loader="${script_dir}/load-snapshot.sh"

failures=0

fail() {
    echo "NG - $1"
    failures=$((failures + 1))
}

pass() {
    echo "ok - $1"
}

assert_exists() {
    if [ -e "$1" ]; then
        pass "$2"
    else
        fail "$2(存在しない: $1)"
    fi
}

assert_absent() {
    if [ -e "$1" ]; then
        fail "$2(存在してはいけない: $1)"
    else
        pass "$2"
    fi
}

assert_log_contains() {
    if grep -q "$1" "${run_log}"; then
        pass "$2"
    else
        fail "$2(ログに '$1' が無い)"
    fi
}

if ! command -v jq >/dev/null 2>&1; then
    echo "NG - jq が見つからないため load-snapshot.sh のテストを実行できません" >&2
    exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

stub_bin="${work}/bin"
jena_home="${work}/jena"
mkdir -p "${stub_bin}" "${jena_home}/bin"

# ---------------------------------------------------------------------------
# スタブ: curl
# ---------------------------------------------------------------------------
# load-snapshot.sh が投げる 4 種類の要求に応える。
#   1. マネージド ID のトークン (IDENTITY_ENDPOINT 方式)
#   2. Blob の一覧 (?restype=container&comp=list)
#   3. 名前空間ごとのマニフェスト (versions/<ns>/_state.json)
#   4. TTL の取得 (-o <target> 付き)
#
# 4 つの名前空間を用意する。
#   good-ns        : マニフェストが正常。approved(current) と in-review の 2 版
#   no-manifest-ns : マニフェストの取得が失敗する(curl -f の 404 相当)
#   bad-manifest-ns: マニフェストが JSON として壊れている
#   draft-only-ns  : マニフェストは正常だが、Blob にある版が載っていない
#                    (= draft。読み込む版が 0 件になる経路)
cat > "${stub_bin}/curl" <<'CURL_STUB'
#!/bin/sh
# 引数から URL(先頭が http のもの)と -o の出力先を取り出す。
url=""
out=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2; continue ;;
        http*) url="$1" ;;
    esac
    shift
done

case "${url}" in
    *"identity/oauth2/token"* | *"IDENTITY"* | *"api-version=2019-08-01"*)
        printf '{"access_token":"faketoken","expires_in":"3599"}'
        exit 0
        ;;
    *"comp=list"*)
        cat <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<EnumerationResults><Blobs>
<Blob><Name>versions/good-ns/1.0.0.ttl</Name></Blob>
<Blob><Name>versions/good-ns/2.0.0.ttl</Name></Blob>
<Blob><Name>versions/no-manifest-ns/1.0.0.ttl</Name></Blob>
<Blob><Name>versions/bad-manifest-ns/1.0.0.ttl</Name></Blob>
<Blob><Name>versions/draft-only-ns/9.9.9.ttl</Name></Blob>
</Blobs></EnumerationResults>
XML
        exit 0
        ;;
    *"/versions/good-ns/_state.json")
        printf '%s' '{"schema":1,"namespace":"good-ns","current":"2.0.0","versions":[{"version":"2.0.0","status":"approved"},{"version":"1.0.0","status":"in-review"}],"generated_at":"t"}'
        exit 0
        ;;
    *"/versions/no-manifest-ns/_state.json")
        # curl -f が 404 で返す終了コード。
        exit 22
        ;;
    *"/versions/bad-manifest-ns/_state.json")
        printf '%s' 'not json at all'
        exit 0
        ;;
    *"/versions/draft-only-ns/_state.json")
        printf '%s' '{"schema":1,"namespace":"draft-only-ns","current":null,"versions":[],"generated_at":"t"}'
        exit 0
        ;;
    *.ttl)
        if [ -n "${out}" ]; then
            printf '%s\n' '<urn:s> <urn:p> <urn:o> .' > "${out}"
        fi
        exit 0
        ;;
esac
echo "stub curl: 想定外の URL: ${url}" >&2
exit 1
CURL_STUB
chmod +x "${stub_bin}/curl"

# ---------------------------------------------------------------------------
# スタブ: tdb2.tdbloader
# ---------------------------------------------------------------------------
# 呼び出しをそのまま記録する。--graph の有無で「名前付きグラフのみ」と
# 「既定グラフにも」を区別できるようにする。
cat > "${jena_home}/bin/tdb2.tdbloader" <<'TDB_STUB'
#!/bin/sh
echo "tdbloader $*" >> "${TDBLOADER_LOG}"
exit 0
TDB_STUB
chmod +x "${jena_home}/bin/tdb2.tdbloader"

# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------
fuseki_base="${work}/fuseki"
run_log="${work}/run.log"
TDBLOADER_LOG="${work}/tdbloader.log"
export TDBLOADER_LOG
: > "${TDBLOADER_LOG}"

set +e
env \
    PATH="${stub_bin}:${PATH}" \
    AZURE_STORAGE_ACCOUNT_URL="https://stfake.blob.core.windows.net" \
    ONTOLOGY_BLOB_CONTAINER="ontologies" \
    IDENTITY_ENDPOINT="http://127.0.0.1/identity/oauth2/token" \
    IDENTITY_HEADER="fakeheader" \
    JENA_HOME="${jena_home}" \
    FUSEKI_BASE="${fuseki_base}" \
    TDB_LOCATION="${fuseki_base}/databases/ds" \
    DATABASES_DIR="${fuseki_base}/databases" \
    CONFIGURATION_DIR="${fuseki_base}/configuration" \
    STAGING_DIR="${work}/staging" \
    SUPERSEDED_RETAIN="0" \
    sh "${loader}" > "${run_log}" 2>&1
loader_status=$?
set -e

# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------
# 本題: 1 件の設定不備が他の名前空間を巻き込んで全滅させないこと。
if [ "${loader_status}" -eq 0 ]; then
    pass "設定不備のある名前空間があってもローダ全体は成功で終わる"
else
    fail "ローダが終了コード ${loader_status} で失敗した"
    echo "---- 実行ログ ----"
    cat "${run_log}"
    echo "------------------"
fi

assert_exists "${fuseki_base}/configuration/good-ns.ttl" \
    "マニフェストが正常な名前空間は読み込まれる(assembler が書かれる)"
assert_exists "${fuseki_base}/databases/good-ns" \
    "マニフェストが正常な名前空間の TDB2 が差し替わる"

assert_absent "${fuseki_base}/configuration/no-manifest-ns.ttl" \
    "マニフェストが取得できない名前空間はスキップされる"
assert_absent "${fuseki_base}/configuration/bad-manifest-ns.ttl" \
    "マニフェストが不正な名前空間はスキップされる"
assert_absent "${fuseki_base}/configuration/draft-only-ns.ttl" \
    "読み込む版が 0 件の名前空間はスキップされる"

assert_log_contains "_state.json が取得できません" \
    "マニフェストの取得失敗が理由付きでログに出る"
assert_log_contains "_state.json が不正な形式です" \
    "マニフェストの不正が理由付きでログに出る"
# P1-22: スキップの理由が区別できること。
assert_log_contains "理由: not-in-manifest" \
    "マニフェストに載っていない版のスキップ理由がログに出る(P1-22)"
assert_log_contains "読み込む版が無かったため名前空間をスキップします" \
    "読み込む版が 0 件だったことがログに出る"

# 正常な名前空間の中で、状態に応じた射影先が使われていること
# (approved かつ current は名前付きグラフ + 既定グラフ、in-review は名前付きのみ)。
if grep -q -- "--graph=urn:ontology:graph/good-ns/2.0.0" "${TDBLOADER_LOG}"; then
    pass "approved(current)の版が名前付きグラフへ読み込まれる"
else
    fail "approved(current)の版が名前付きグラフへ読み込まれていない"
fi
if grep -q -- "--graph=urn:ontology:graph/good-ns/1.0.0" "${TDBLOADER_LOG}"; then
    pass "in-review の版が名前付きグラフへ読み込まれる"
else
    fail "in-review の版が名前付きグラフへ読み込まれていない"
fi
# `--graph` の無い呼び出し(= 既定グラフ)がちょうど 1 回。既定グラフに
# 複数版が載ると Critical(P1-C1)が再来する。
default_loads="$(grep -c -v -- "--graph=" "${TDBLOADER_LOG}" || true)"
if [ "${default_loads}" = "1" ]; then
    pass "既定グラフへの読み込みはちょうど 1 回(承認済み現行版のみ)"
else
    fail "既定グラフへの読み込みが ${default_loads} 回(1 回であるべき)"
    cat "${TDBLOADER_LOG}"
fi

# 設定不備のある名前空間のデータが混入していないこと。
if grep -q "no-manifest-ns\|bad-manifest-ns\|draft-only-ns" "${TDBLOADER_LOG}"; then
    fail "スキップしたはずの名前空間が tdbloader に渡っている"
    cat "${TDBLOADER_LOG}"
else
    pass "スキップした名前空間は tdbloader に渡らない"
fi

if [ "${failures}" -gt 0 ]; then
    echo "失敗: ${failures} 件" >&2
    exit 1
fi
echo "すべて成功しました(load-snapshot.sh の制御フロー)"
