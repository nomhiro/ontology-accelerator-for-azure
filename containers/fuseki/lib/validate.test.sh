#!/bin/sh
# validate.sh の検証関数に対するテスト。
#
# load-snapshot.sh 本体は Blob 一覧の取得や TDB2 の構築など副作用のあるコードを
# トップレベルに持つため、そのまま source してテストすることができない。
# 検証関数だけを lib/validate.sh に切り出してあるので、ここではそれだけを
# source して直接呼ぶ。
#
# 実行方法: sh containers/fuseki/lib/validate.test.sh
set -eu

script_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "${script_dir}/validate.sh"

failures=0

# 引数: 説明, 関数名, 入力, 期待する結果(ok|reject)
check() {
    description="$1"
    fn="$2"
    input="$3"
    expected="$4"

    if "${fn}" "${input}" >/dev/null 2>&1; then
        actual=ok
    else
        actual=reject
    fi

    if [ "${actual}" = "${expected}" ]; then
        echo "ok - ${description}"
    else
        echo "NG - ${description}(期待: ${expected}, 実際: ${actual}, 入力: '${input}')"
        failures=$((failures + 1))
    fi
}

# 引数: 説明, 関数名, 入力, 期待する出力文字列
#
# normalize_graph_iri_base / normalize_blob_prefix は ok/reject ではなく
# 正規化した文字列を返す関数なので、check() とは別の比較が要る。
check_eq() {
    description="$1"
    fn="$2"
    input="$3"
    expected="$4"

    actual="$("${fn}" "${input}")"

    if [ "${actual}" = "${expected}" ]; then
        echo "ok - ${description}"
    else
        echo "NG - ${description}(期待: '${expected}', 実際: '${actual}', 入力: '${input}')"
        failures=$((failures + 1))
    fi
}

# ---- validate_namespace ----
# 正常系。
check "小文字とハイフンの通常の名前空間名"     validate_namespace "retail-core" ok
check "数字始まりも許可"                      validate_namespace "9lives" ok
# 異常系(ここが通ってしまうと隔離境界が崩れる)。
check "1 文字は短すぎて拒否"                  validate_namespace "a" reject
check "予約名 ds は拒否"                      validate_namespace "ds" reject
check "大文字を含むと拒否"                    validate_namespace "Retail" reject
check "先頭がハイフンだと拒否"                validate_namespace "-core" reject
check "スラッシュを含むと拒否"                validate_namespace "a/b" reject
check "パストラバーサル(相対パス)は拒否"      validate_namespace "../evil" reject
check "空文字は拒否"                          validate_namespace "" reject
check "64 文字以上は長すぎて拒否" \
    validate_namespace "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
    reject

# ---- validate_version_file ----
# 正常系。
check "正常なバージョンファイル名"                    validate_version_file "1.0.0.ttl" ok
check "英字とプラスを含むバージョンも許可"            validate_version_file "1.0.0-beta+build.ttl" ok
# 1 文字のバージョンも許可(ontology_core.graphs._VERSION_PATTERN は
# [A-Za-z0-9][A-Za-z0-9.+-]{0,63} で 1 文字から許可している。過去に
# [A-Za-z0-9][A-Za-z0-9.+-]* という POSIX glob で書いたことで 2 文字以上を
# 要求してしまい、1 文字のバージョンを静かにスキップするバグがあった)。
check "数字 1 文字のバージョンも許可"                 validate_version_file "1.ttl" ok
check "英字 1 文字のバージョンも許可"                 validate_version_file "a.ttl" ok
# 異常系。修正1(Important)の本題: version_file 自体は Blob のリスト応答から
# そのまま来るため、ここで拒否できないと STAGING_DIR の外へ書き込める。
check "拡張子が .ttl でないと拒否"                    validate_version_file "1.0.0" reject
check "直下にスラッシュを含むと拒否"                  validate_version_file "a/b.ttl" reject
check "パストラバーサル(../../evil.ttl)は拒否" \
    validate_version_file "../../evil.ttl" reject
check "空のバージョンは拒否"                          validate_version_file ".ttl" reject
check "先頭がドットだけのバージョンは拒否"            validate_version_file "..ttl" reject
check "絶対パスは拒否"                                validate_version_file "/etc/passwd.ttl" reject

# ---- normalize_graph_iri_base ----
# I-4 追加分: Python 側(graphs.py の base.rstrip('/'))とシェル側を一致させる。
check_eq "末尾スラッシュ無しはそのまま" \
    normalize_graph_iri_base "urn:ontology:graph" "urn:ontology:graph"
check_eq "末尾スラッシュ 1 個を除去" \
    normalize_graph_iri_base "urn:ontology:graph/" "urn:ontology:graph"
check_eq "末尾スラッシュ複数も全部除去(rstrip('/') と同じ)" \
    normalize_graph_iri_base "urn:ontology:graph///" "urn:ontology:graph"

# ---- normalize_blob_prefix ----
# I-4 追加分: Python 側(blob.py の prefix.rstrip('/') + '/')とシェル側を一致させる。
# テスト値は ADR-0010 決定8での改名後の既定値(`versions/`)に合わせている。
check_eq "末尾スラッシュ無しには 1 個付与" \
    normalize_blob_prefix "versions" "versions/"
check_eq "末尾スラッシュ 1 個はそのまま" \
    normalize_blob_prefix "versions/" "versions/"
check_eq "末尾スラッシュ複数は 1 個に正規化" \
    normalize_blob_prefix "versions///" "versions/"

# 引数: 説明, 関数名, 入力1, 入力2, 期待する出力文字列
#
# manifest_status_for_version(json, version) のように 2 引数を取る関数用。
check_eq2() {
    description="$1"
    fn="$2"
    input1="$3"
    input2="$4"
    expected="$5"

    actual="$("${fn}" "${input1}" "${input2}")"

    if [ "${actual}" = "${expected}" ]; then
        echo "ok - ${description}"
    else
        echo "NG - ${description}(期待: '${expected}', 実際: '${actual}')"
        failures=$((failures + 1))
    fi
}

# jq が無いとここで実行できない。CI(ubuntu-latest)には標準で入っているが、
# ローカルで直接 `sh containers/fuseki/lib/validate.test.sh` する場合は
# jq が必要(fuseki コンテナの中や、jq をインストールした環境で実行すること)。
if ! command -v jq >/dev/null 2>&1; then
    echo "NG - jq が見つからないため manifest_* のテストを実行できません" >&2
    failures=$((failures + 1))
else
    manifest_ok='{"schema":1,"namespace":"retail-core","current":"2.0.0","versions":[{"version":"2.0.0","status":"approved"},{"version":"2.1.0","status":"in-review"},{"version":"1.0.0","status":"superseded"}],"generated_at":"2026-09-05T00:00:00Z"}'

    # ---- validate_manifest_json ----
    # 修正5の本題: マニフェストが無い・壊れている名前空間は
    # 「黙って全件承認済みとして扱う」のではなく、明示的に読み込みをスキップ
    # する。この判定に使う関数そのもの。
    check "正常なマニフェストは受理"                     validate_manifest_json "${manifest_ok}" ok
    check "空文字(取得失敗)は不正なマニフェストとして拒否" validate_manifest_json "" reject
    check "壊れた JSON は不正なマニフェストとして拒否"   validate_manifest_json "not json" reject
    check "schema が無いと拒否" \
        validate_manifest_json '{"namespace":"x","versions":[]}' reject
    check "versions が配列でないと拒否" \
        validate_manifest_json '{"schema":1,"namespace":"x","versions":"oops"}' reject

    # ---- manifest_current ----
    check_eq "current(承認済み現行版)を取り出す" manifest_current "${manifest_ok}" "2.0.0"
    check_eq "current が無い(null)場合は空文字" \
        manifest_current '{"schema":1,"namespace":"x","current":null,"versions":[]}' ""

    # ---- manifest_status_for_version ----
    check_eq2 "approved の版の status" manifest_status_for_version "${manifest_ok}" "2.0.0" "approved"
    check_eq2 "in-review の版の status" manifest_status_for_version "${manifest_ok}" "2.1.0" "in-review"
    check_eq2 "superseded の版の status" manifest_status_for_version "${manifest_ok}" "1.0.0" "superseded"
    check_eq2 "マニフェストに無い版(draft の可能性)は空文字" \
        manifest_status_for_version "${manifest_ok}" "9.9.9" ""
fi

if [ "${failures}" -gt 0 ]; then
    echo "失敗: ${failures} 件" >&2
    exit 1
fi
echo "すべて成功しました(検証関数のテスト)"
