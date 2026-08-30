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

if [ "${failures}" -gt 0 ]; then
    echo "失敗: ${failures} 件" >&2
    exit 1
fi
echo "すべて成功しました(検証関数のテスト)"
