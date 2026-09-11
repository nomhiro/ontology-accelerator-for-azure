#!/bin/sh
# シェルスクリプトに対する、shellcheck では拾えない移植性の検査（P1-14）。
#
# **なぜ必要か**: `scripts/postdeploy.sh` が素の `python -c` を呼んでいて、
# `python` を PATH に置かない Linux（Azure Linux 3.0 / Ubuntu 20.04+ など、
# Python 3 が既定になった時点で無印の `python` の提供をやめたディストリ）では
# `command not found` になっていた。実測で確認して修正したが、
# **shellcheck はこれを検出しない**（構文としては正しいため）。
# 同じ間違いが戻ってこないよう、機械的な検査として残す。
#
# 実行方法: sh scripts/lint-shell.sh
set -eu

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "${repo_root}"

failures=0

report() {
    echo "NG - $1" >&2
    failures=$((failures + 1))
}

# 検査対象。CI の検査と同じ範囲に揃える。
#
# **このスクリプト自身は除く。** 探索するパターン（`python` や bash 専用構文）を
# 文字列リテラルとして含んでいるため、自分自身を検査すると必ず誤検出する。
targets=""
for candidate in scripts/*.sh containers/fuseki/*.sh containers/fuseki/lib/*.sh; do
    [ -f "${candidate}" ] || continue
    case "${candidate}" in
        scripts/lint-shell.sh) continue ;;
    esac
    targets="${targets} ${candidate}"
done

# ---- 1. 素の `python` を呼んでいないこと ----
#
# `uv run python` / `uv run --directory X python` は可（uv がインタプリタを
# 用意するため、uv が動く環境なら必ず動く）。`python3` も可。
# 弾くのは、行の中で `uv run` を経由しない `python` の直接起動。
for f in ${targets}; do
    hits="$(grep -n '[^-a-zA-Z_]python[^0-9a-zA-Z_]' "${f}" 2>/dev/null |
        grep -v 'uv run' |
        grep -v '^[0-9]*: *#' || true)"
    if [ -n "${hits}" ]; then
        report "${f}: 素の 'python' を呼んでいます。'uv run python' か 'python3' を使ってください"
        echo "${hits}" | sed 's/^/       /' >&2
    fi
done

# ---- 2. `#!/bin/sh` を宣言しているのに bash 専用の構文を使っていないこと ----
#
# 静的解析ツール側でも指摘されるが、CI で scripts/ が検査対象から漏れていた
# 期間があるため、ここでも見る（多重でも害はない）。
#
# 注意: コメント行を静的解析ツールの名前だけで始めてはいけない。`#` の直後に
# その名前が来ると、ツール自身がディレクティブ指定として解釈し
# SC1072 / SC1073 で失敗する（実測で 2 回踏んだ）。説明したいときは
# 「静的解析ツール」と書くか、行頭に別の語を置く。
for f in ${targets}; do
    if head -1 "${f}" | grep -q '^#!/bin/sh'; then
        hits="$(grep -n '\[\[\|(( \|declare -A\|local -n\|<<<' "${f}" 2>/dev/null || true)"
        if [ -n "${hits}" ]; then
            report "${f}: #!/bin/sh なのに bash 専用の構文があります"
            echo "${hits}" | sed 's/^/       /' >&2
        fi
    fi
done

# ---- 3. `scripts/*.py` が運用者向けの出力に print を使っていないこと ----
#
# **なぜ必要か**: Windows の Python の標準出力は cp932 である。`print` に日本語を
# 渡すと cp932 のバイト列が出る一方、周りのシェルスクリプトの `echo` はソースの
# UTF-8 をそのまま出すため、**azd のフックのログに 2 つのエンコーディングが
# 混ざって読めなくなる**（実測）。cp932 に無い文字（絵文字・ダッシュ）があると
# `UnicodeEncodeError` で**スクリプトごと落ちる**。
#
# `ontology_core.console` の `say` / `warn` は UTF-8 を明示して書き出す。
# 一度直しても戻ってくるので機械的に検査する（`P2A-10`）。
for f in scripts/*.py; do
    [ -f "${f}" ] || continue
    hits="$(grep -n '^[[:space:]]*print(' "${f}" 2>/dev/null || true)"
    if [ -n "${hits}" ]; then
        report "${f}: print を使っています。ontology_core.console の say / warn を使ってください"
        echo "${hits}" | sed 's/^/       /' >&2
    fi
done

if [ "${failures}" -gt 0 ]; then
    echo "失敗: ${failures} 件" >&2
    exit 1
fi
echo "すべて成功しました（シェルスクリプトの移植性）"
