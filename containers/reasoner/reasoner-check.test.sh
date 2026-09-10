#!/bin/sh
# OWL 推論器 (ELK) の検査の振る舞いを固定する (P2B-01、ADR-0021)。
#
# 前提: docker が動いていること、uv が入っていること。
# 使い方: sh containers/reasoner/reasoner-check.test.sh
#
# **JSON の判定は `uv run python` で行う。** jq を要求すると Windows では
# 追加の docker が必要になり、この検査自体が docker を回すので入れ子になる。
# 素の `python` を呼ばないのは scripts/lint-shell.sh の規律に従うため。
set -eu

# Git Bash は `/` で始まる引数を Windows のパスに変換する(CLAUDE.md に記録済み)。
# 抑止しないと `-w /work` が `W:/` になって docker が拒否する。Linux では無害。
MSYS_NO_PATHCONV=1
MSYS2_ARG_CONV_EXCL='*'
export MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

IMAGE=ontology-reasoner:test
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
TESTDATA=containers/reasoner/testdata

# docker に渡すホスト側のパスは、Windows では `C:/...` 形式にする。
# ビルドコンテキストも同じ変換が必要である(素の `/c/...` は docker が
# 「パスが見つからない」と言って止まる。実測)。
if command -v cygpath >/dev/null 2>&1; then
    HOST_REPO=$(cygpath -m "$REPO")
    HOST_HERE=$(cygpath -m "$HERE")
else
    HOST_REPO=$REPO
    HOST_HERE=$HERE
fi

fail=0
note() { printf '%s\n' "$*"; }
bad() { printf 'NG: %s\n' "$*"; fail=1; }

note "推論器イメージをビルドします ($IMAGE)"
docker build -q -t "$IMAGE" "$HOST_HERE" >/dev/null

# 検査を実行し、1 行目に終了コード、2 行目に JSON を置いて返す。
#
# **リポジトリ全体をマウントする。** JSON の `file` にリポジトリ相対のパスが
# 載るので、CI のログからそのまま辿れる。
run_check() {
    _out=$(mktemp)
    _rc=0
    docker run --rm -v "$HOST_REPO:/work" -w /work "$IMAGE" "$@" \
        >"$_out" 2>/dev/null || _rc=$?
    printf '%s\n' "$_rc"
    cat "$_out"
    rm -f "$_out"
}

# 第 3 引数は Python の式。`r` に 1 件目の結果、`rc` に終了コード、
# `doc` に全体が入る。
assert_check() {
    _label=$1
    _target=$2
    _expr=$3
    _raw=$(run_check "$_target")
    _rc=$(printf '%s\n' "$_raw" | head -n 1)
    _json=$(printf '%s\n' "$_raw" | tail -n +2)
    if printf '%s' "$_json" | RC="$_rc" EXPR="$_expr" \
        uv run python -c '
import json, os, sys

from ontology_core.console import say

# **標準入力を bytes で読んで自分で UTF-8 として解釈する。** Windows の
# Python は標準入出力を cp932 で扱うため、json.load(sys.stdin) だと
# 日本語ラベルを含む JSON が壊れる(実測。CLAUDE.md の cp932 の罠の入力側)。
# 出力側に say を使うのも同じ理由である。
#
# なお、この埋め込み Python のコメントにバッククォートを書いてはいけない。
# シェルの単一引用符の中にあると SC2016 で検査が落ちる(実測)。
expr = os.environ["EXPR"]
rc = int(os.environ["RC"])
doc = json.loads(sys.stdin.buffer.read().decode("utf-8"))
scope = {"r": doc["results"][0], "rc": rc, "doc": doc}
# 括弧で包むのは、複数行に折り返した式を eval が受け付けるようにするため。
if not eval("(" + expr + ")", {}, scope):
    say("  条件が成立しませんでした: " + " ".join(expr.split()))
    say("  rc=%d" % rc)
    say("  " + json.dumps(scope["r"], ensure_ascii=False)[:1500])
    sys.exit(1)
'; then
        note "OK: $_label"
    else
        bad "$_label"
    fi
}

note ""
note "--- EL の中で整合しているオントロジー"
assert_check "整合していれば通す(偽陽性が無い)" \
    "$TESTDATA/consistent-el.ttl" \
    'rc == 0 and r["loaded"] and not r["blocking"]
     and not r["inconsistency_found"] and r["consistency_conclusive"]
     and r["unsatisfiable_classes"] == [] and r["unsatisfiable_classes_conclusive"]'

note ""
note "--- 充足不能クラス"
assert_check "充足不能クラスを検出して止める" \
    "$TESTDATA/unsatisfiable-el.ttl" \
    'rc == 1 and r["blocking"]
     and r["unsatisfiable_classes"] == ["https://example.com/t#Ghost"]
     and not r["inconsistency_found"]'

note ""
note "--- 矛盾したオントロジー"
# **`unsatisfiable_classes` が `null` であること**を確認する。矛盾時は全クラスが
# 充足不能になるので列挙しない。`[]` にすると「充足不能クラスは無かった」と
# 読めてしまう(ADR-0021 決定1)。
assert_check "矛盾を検出して止める" \
    "$TESTDATA/inconsistent-el.ttl" \
    'rc == 1 and r["blocking"] and r["inconsistency_found"]
     and r["consistency_conclusive"]
     and r["unsatisfiable_classes"] is None
     and r["unsatisfiable_class_count"] is None'

note ""
note "--- 読み込めないファイル"
assert_check "読めなかったことを「矛盾なし」にしない" \
    "$TESTDATA/broken.ttl" \
    'rc == 1 and r["blocking"] and not r["loaded"] and r["error"]
     and r["unsatisfiable_classes"] is None'

note ""
note "--- プロファイル適合と推論の完全性は別物(両方向)"
# ELK が扱えない構文があると、結論は「矛盾は検出されなかった」に留まる。
# **OWL 2 EL の逸脱は 0 件**であることも併せて確認する。ここが >0 になると
# 「逸脱を見れば不完全性が分かる」という誤った近道が成立してしまう。
assert_check "逸脱 0 件でも結論は不完全になりうる(データプロパティ)" \
    "$TESTDATA/incomplete-datatype.ttl" \
    'rc == 0 and not r["blocking"] and not r["inconsistency_found"]
     and not r["consistency_conclusive"]
     and r["expressivity_violation_count"] == 0
     and any("DataProperty" in x for x in r["incompleteness_reasons"])'

assert_check "逸脱があっても結論は完全になりうる(subClassOf の左辺の和)" \
    "$TESTDATA/beyond-el.ttl" \
    'rc == 0 and not r["blocking"]
     and r["expressivity_violation_count"] >= 1
     and r["consistency_conclusive"] and r["incompleteness_reasons"] == []'

note ""
note "--- 同梱サンプル"
# **SHACL shape と SKOS を宣言せずに使っているので宣言漏れが大量に出る。**
# それを表現力の逸脱と混ぜないことを確認する(混ぜると意味のある逸脱が埋もれる)。
assert_check "同梱サンプルは通る" \
    "samples/retail-core.ttl" \
    'rc == 0 and not r["blocking"] and r["loaded"]
     and r["declaration_violation_count"] > 0
     and r["expressivity_violation_count"] == 0'

note ""
note "--- 複数ファイル"
_raw=$(run_check "$TESTDATA/consistent-el.ttl" "$TESTDATA/unsatisfiable-el.ttl")
_rc=$(printf '%s\n' "$_raw" | head -n 1)
_json=$(printf '%s\n' "$_raw" | tail -n +2)
if printf '%s' "$_json" | RC="$_rc" uv run python -c '
import json, os, sys

rc = int(os.environ["RC"])
doc = json.loads(sys.stdin.buffer.read().decode("utf-8"))
# 1 件でも止めるべきものがあれば全体を止める。個別の結果は残す。
assert rc == 1, "1 件でも blocking なら終了コードは 1 であるべき"
assert doc["blocking"] is True, doc["blocking"]
assert len(doc["results"]) == 2, len(doc["results"])
assert doc["results"][0]["blocking"] is False, "1 件目は通るべき"
assert doc["results"][1]["blocking"] is True, "2 件目は止めるべき"
assert doc["reasoner"] and doc["reasoner"].startswith("ELK "), doc["reasoner"]
'; then
    note "OK: 複数ファイルのうち 1 件でも止めるべきなら全体を止める"
else
    bad "複数ファイルのうち 1 件でも止めるべきなら全体を止める"
fi

note ""
note "--- 配布物に LGPL のコンポーネントが入っていないこと"
# ADR-0005 決定4(HermiT を配布物に同梱しない)を機械的に守る。
# **依存を 1 つ足すと推移的に入りうる**ので、目視の確認では守れない
# (owlapi-distribution を足していた時期には実際に除外指定が必要だった)。
_jar=$(mktemp)
_cid=$(docker create "$IMAGE")
docker cp "$_cid:/opt/reasoner/reasoner-check.jar" "$_jar" >/dev/null
docker rm "$_cid" >/dev/null
if JAR="$_jar" uv run python -c '
import os, sys, zipfile

from ontology_core.console import say

names = zipfile.ZipFile(os.environ["JAR"]).namelist()
banned = [n for n in names if "hermit" in n.lower() or "jfact" in n.lower()]
if banned:
    say("  同梱されてはいけないコンポーネントが %d 件:" % len(banned))
    for n in banned[:10]:
        say("    " + n)
    sys.exit(1)
# NOTICE の集約も確認する。Apache-2.0 の同梱物があるので必須である。
if "META-INF/NOTICE" not in names:
    say("  META-INF/NOTICE がありません (Apache-2.0 の NOTICE 継承が壊れている)")
    sys.exit(1)
'; then
    note "OK: HermiT / JFact は同梱されていない (NOTICE も集約されている)"
else
    bad "HermiT / JFact は同梱されていない (NOTICE も集約されている)"
fi
rm -f "$_jar"

note ""
note "--- 引数なし"
_rc=0
docker run --rm "$IMAGE" >/dev/null 2>&1 || _rc=$?
if [ "$_rc" -ne 2 ]; then
    bad "引数なしは終了コード 2 (使い方の誤り) であるべき: 実際は $_rc"
else
    note "OK: 引数なしは終了コード 2"
fi

note ""
if [ "$fail" -ne 0 ]; then
    note "失敗しました"
    exit 1
fi
note "すべて成功しました"
