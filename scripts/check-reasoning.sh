#!/bin/sh
# OWL 推論器 (ELK) でオントロジーの論理的整合性を検査する (P2B-01、ADR-0021)。
#
#   sh scripts/check-reasoning.sh                 # samples/ をすべて検査する
#   sh scripts/check-reasoning.sh samples/x.ttl   # ファイルを指定する
#   sh scripts/check-reasoning.sh dir1 file2.ttl  # ディレクトリとファイルの混在
#
# **パスはリポジトリルート相対で指定する。** コンテナにリポジトリを丸ごと
# マウントして同じパスを渡すため、JSON の `file` が CI のログからそのまま辿れる。
#
# 前提: docker が動いていること、uv が入っていること。
#
# **終了コード**: 0 = 止めるものは無い / 1 = 矛盾・充足不能クラス・読み込み失敗
# のいずれかがある / 2 = 使い方の誤り。
#
# **「矛盾は検出されなかった」は「矛盾がない」ではない。** ELK は OWL 2 EL の
# 推論器で、扱えない公理は無視するため見逃しがある(誤検出はしない)。
# 結論が完全だったかどうかを必ず併せて表示する。詳細は ADR-0021。
set -eu

# Git Bash は `/` で始まる引数を Windows のパスに変換する(CLAUDE.md に記録済み)。
# 抑止しないと `-w /work` が `W:/` になって docker が拒否する。Linux では無害。
MSYS_NO_PATHCONV=1
MSYS2_ARG_CONV_EXCL='*'
export MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

IMAGE=${REASONER_IMAGE:-ontology-reasoner:local}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)

# **リポジトリルートで動く。** `uv run --directory` に POSIX 形式のパスを
# 渡すと Windows の uv が「指定されたパスが見つかりません」で落ちる(実測)ため、
# パスを渡すのではなく作業ディレクトリを移す。
cd "$REPO"

# docker に渡すホスト側のパスは、Windows では `C:/...` 形式にする。
if command -v cygpath >/dev/null 2>&1; then
    HOST_REPO=$(cygpath -m "$REPO")
    HOST_CONTEXT=$(cygpath -m "$REPO/containers/reasoner")
else
    HOST_REPO=$REPO
    HOST_CONTEXT=$REPO/containers/reasoner
fi

if [ "$#" -eq 0 ]; then
    set -- samples
fi

# ディレクトリは配下の *.ttl に展開する。
list_targets() {
    for arg in "$@"; do
        if [ -d "$arg" ]; then
            find "$arg" -type f -name '*.ttl' | sort
        else
            printf '%s\n' "$arg"
        fi
    done
}

TARGETS=$(list_targets "$@")

# 展開結果を位置パラメータに積み直す。**パイプではなくヒアドキュメントを使う。**
# パイプだと while がサブシェルで走って `set --` の結果が捨てられる。
set --
while IFS= read -r target; do
    if [ -n "$target" ]; then
        set -- "$@" "$target"
    fi
done <<TARGET_LIST
$TARGETS
TARGET_LIST

if [ "$#" -eq 0 ]; then
    printf '検査対象の .ttl が見つかりませんでした\n' >&2
    exit 2
fi

printf '推論器イメージをビルドします (%s)\n' "$IMAGE" >&2
docker build -q -t "$IMAGE" "$HOST_CONTEXT" >/dev/null

printf '検査します: %s\n' "$*" >&2

# 検査対象はリポジトリ相対で渡す。JSON の `file` にそのまま載るので、
# CI のログからファイルを辿れる。
rc=0
docker run --rm -v "$HOST_REPO:/work" -w /work "$IMAGE" "$@" >.reasoner-report.json || rc=$?

# **人が読める要約を出す。** JSON だけだと CI のログで誰も読まない。
REPORT=.reasoner-report.json uv run python -c '
import json, os

from ontology_core.console import say, warn

with open(os.environ["REPORT"], encoding="utf-8") as handle:
    doc = json.load(handle)

say("推論器: %s / プロファイル: %s" % (doc["reasoner"], doc["profile"]))
for r in doc["results"]:
    if not r["loaded"]:
        # OWLAPI は「試した全パーサの一覧」を返すので、要約では頭だけ見せる。
        # 全文は標準エラーと .reasoner-report.json にある。
        detail = " ".join((r["error"] or "").split())[:200]
        warn("  %s: 読み込めませんでした - %s" % (r["file"], detail))
        warn("    全文は標準エラーと %s にあります" % os.environ["REPORT"])
        continue
    say("  %s (公理 %d 件)" % (r["file"], r["axiom_count"]))
    if r["inconsistency_found"]:
        warn("    矛盾が検出されました")
    else:
        # **ここが要点。** 「矛盾なし」と書かない。
        state = "完全に検査できました" if r["consistency_conclusive"] else "見逃しがありえます"
        say("    矛盾は検出されませんでした (%s)" % state)
    if r["unsatisfiable_classes"] is None:
        say("    充足不能クラス: 測っていません (矛盾時は全クラスが該当するため)")
    elif r["unsatisfiable_classes"]:
        warn("    充足不能クラス %d 件:" % len(r["unsatisfiable_classes"]))
        for iri in r["unsatisfiable_classes"]:
            warn("      " + iri)
    else:
        state = "完全" if r["unsatisfiable_classes_conclusive"] else "見逃しがありえます"
        say("    充足不能クラスはありませんでした (%s)" % state)
    for reason in r["incompleteness_reasons"]:
        say("    不完全さの理由: " + reason)
    if r["expressivity_violation_count"]:
        say("    OWL 2 EL の表現力の逸脱 %d 件:" % r["expressivity_violation_count"])
        for v in r["expressivity_violations"]:
            say("      " + v[:200])
    if r["declaration_violation_count"]:
        # 宣言漏れは報告だけ。SKOS や SHACL の語彙を宣言せずに使うと出る。
        say("    宣言されていない語彙の使用 %d 件 (詳細は %s)"
            % (r["declaration_violation_count"], os.environ["REPORT"]))
'

if [ "$rc" -ne 0 ]; then
    printf '止めるべき問題があります (終了コード %s)。詳細: .reasoner-report.json\n' "$rc" >&2
    exit "$rc"
fi
printf '止めるべき問題はありませんでした\n' >&2
