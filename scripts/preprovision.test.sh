#!/bin/sh
# preprovision.sh の platform-admin ゲートに対するテスト(P2A-09)。
#
# **なぜテストするのか**
#
# このゲートは「デプロイを止める」判断をする。**間違った方向に間違うと痛い**:
#
#   - 止めるべきときに通すと、postdeploy が 403 になり約 11 分と課金を捨てる
#   - 通すべきときに止めると、**確認の仕組み自体が新しいブロッカーになる**
#     （Graph の権限が無い運用者、az login が切れている運用者が deploy できない）
#
# 後者のほうが害が大きい。そのため「確認できなかった」は通すと決めた
# (ADR-0014 の周辺実装。scripts/check-platform-admin.py の終了コード 1 と 2 の
# 使い分けがこれを表している)。その分岐を固定する。
#
# **やり方**: `az` と `azd` をスタブに差し替えてスクリプトを丸ごと実行する
# (load-snapshot.test.sh と同じ方針)。スクリプト自身のロジックは書き換えない。
# `uv run python scripts/check-platform-admin.py` は**本物を実行する** —
# トークンの解釈はテストしたい対象そのものだからである。
#
# 実行方法: sh scripts/preprovision.test.sh
#   uv が必要(check-platform-admin.py を実行するため)。
set -eu

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
target="${repo_root}/scripts/preprovision.sh"

failures=0
fail() { echo "NG - $1"; failures=$((failures + 1)); }
pass() { echo "ok - $1"; }

work=""
cleanup() {
    if [ -n "${work}" ]; then
        rm -rf "${work}"
    fi
}
trap cleanup EXIT

# base64url でパディングを削る(JWT と同じ形にする)。
b64url() {
    # openssl が無い環境でも動くよう python を使う。**素の python は使わない**
    # (P1-14。多くの Linux には python3 しか無い)。
    uv run --directory "${repo_root}" python -c "
import base64, sys
data = sys.stdin.buffer.read()
sys.stdout.write(base64.urlsafe_b64encode(data).decode().rstrip('='))
"
}

# roles クレームを持つ(あるいは持たない)偽のトークンを作る。
# **署名は検証しないので中身はでたらめでよい**(check-platform-admin.py は
# ontology_core.auth.unverified を使い、署名を見ない)。
make_token() {
    header="$(printf '%s' '{"alg":"RS256","typ":"JWT"}' | b64url)"
    payload="$(printf '%s' "$1" | b64url)"
    printf '%s.%s.sig' "${header}" "${payload}"
}

# スタブを用意して preprovision.sh を実行する。
#   $1 = az account get-access-token が返すトークン(空ならトークン取得を失敗させる)
run_preprovision() {
    token="$1"
    work="$(mktemp -d)"
    mkdir -p "${work}/bin"

    printf '%s\n' "${token}" > "${work}/token.txt"

    # az のスタブ。preprovision.sh が呼ぶのは 2 つの経路だけ。
    cat > "${work}/bin/az" <<'STUB'
#!/bin/sh
case "$1 $2 $3" in
    "ad signed-in-user show")
        # UPN を返す経路(User として解決できる)。
        echo "operator@example.onmicrosoft.com"
        exit 0
        ;;
    "account get-access-token"*)
        token="$(cat "${STUB_TOKEN_FILE}")"
        if [ -z "${token}" ]; then
            echo "AADSTS500011: リソースが見つかりません" >&2
            exit 1
        fi
        echo "${token}"
        exit 0
        ;;
esac
echo "az スタブ: 想定外の呼び出し: $*" >&2
exit 1
STUB

    # azd のスタブ。env set は記録するだけ。
    cat > "${work}/bin/azd" <<'STUB'
#!/bin/sh
echo "$*" >> "${STUB_AZD_LOG}"
exit 0
STUB

    chmod +x "${work}/bin/az" "${work}/bin/azd"

    STUB_TOKEN_FILE="${work}/token.txt" \
    STUB_AZD_LOG="${work}/azd.log" \
    PATH="${work}/bin:${PATH}" \
    AUTH_MODE="${TEST_AUTH_MODE:-entra}" \
    ENTRA_API_AUDIENCE="${TEST_AUDIENCE-11111111-2222-3333-4444-555555555555}" \
        sh "${target}" > "${work}/out.log" 2>&1
    return $?
}

assert_log() {
    if grep -q "$1" "${work}/out.log"; then
        pass "$2"
    else
        fail "$2（ログに '$1' が無い）"
        sed 's/^/      /' "${work}/out.log"
    fi
}

# ---------------------------------------------------------------------------
echo "--- 1) platform-admin があれば成功する ---"
rc=0
run_preprovision "$(make_token '{"oid":"abc","roles":["platform-admin"]}')" || rc=$?
if [ "${rc}" -eq 0 ]; then
    pass "終了コード 0"
else
    fail "終了コード 0 を期待したが ${rc}"
    sed 's/^/      /' "${work}/out.log"
fi
assert_log "platform-admin' があります" "ロールがあることをログに出す"
assert_log "AZURE_PRINCIPAL_NAME" "既存の責務（表示名の設定）も動いている"
cleanup; work=""

# ---------------------------------------------------------------------------
echo "--- 2) platform-admin が無ければ provision を止める ---"
rc=0
run_preprovision "$(make_token '{"oid":"abc","roles":["other-role"]}')" || rc=$?
if [ "${rc}" -ne 0 ]; then
    pass "終了コードが非ゼロ（provision に進ませない）"
else
    fail "止まらなかった。**これは 11 分と課金を捨てる方向の誤り**"
    sed 's/^/      /' "${work}/out.log"
fi
assert_log "provision を中止します" "止めた理由をログに出す"
assert_log "setup-app-role" "対処の手段を示す"
cleanup; work=""

# ---------------------------------------------------------------------------
echo "--- 3) roles クレームが無いトークンも「無い」として止める ---"
# Entra はロールが 1 件も無いとクレーム自体を落とす。これを
# 「読めなかった」と誤判定すると、止めるべきときに通してしまう。
rc=0
run_preprovision "$(make_token '{"oid":"abc"}')" || rc=$?
if [ "${rc}" -ne 0 ]; then
    pass "終了コードが非ゼロ"
else
    fail "roles クレームが無いトークンで止まらなかった"
    sed 's/^/      /' "${work}/out.log"
fi
cleanup; work=""

# ---------------------------------------------------------------------------
echo "--- 4) トークンが取れないときは続行する ---"
# **ここが「確認の仕組みを新しいブロッカーにしない」の要**。
# az login が切れている・スコープが違う等で確認できないことは起こりうる。
rc=0
run_preprovision "" || rc=$?
if [ "${rc}" -eq 0 ]; then
    pass "終了コード 0（確認できなくてもデプロイは止めない）"
else
    fail "確認できないだけで止めてしまった（偽のブロッカー）。終了コード ${rc}"
    sed 's/^/      /' "${work}/out.log"
fi
assert_log "続行します" "続行したことをログに出す"
cleanup; work=""

# ---------------------------------------------------------------------------
echo "--- 5) トークンが JWT でないときも続行する ---"
rc=0
run_preprovision "これはトークンではありません" || rc=$?
if [ "${rc}" -eq 0 ]; then
    pass "終了コード 0（読めない＝判定していない）"
else
    fail "読めないトークンで止めてしまった。終了コード ${rc}"
    sed 's/^/      /' "${work}/out.log"
fi
assert_log "確認できませんでした" "確認できなかったとログに出す"
cleanup; work=""

# ---------------------------------------------------------------------------
echo "--- 6) AUTH_MODE=disabled では確認しない ---"
rc=0
TEST_AUTH_MODE=disabled run_preprovision "" || rc=$?
if [ "${rc}" -eq 0 ]; then
    pass "終了コード 0"
else
    fail "終了コード 0 を期待したが ${rc}"
fi
assert_log "AUTH_MODE=disabled のため" "飛ばした理由をログに出す"
cleanup; work=""

# ---------------------------------------------------------------------------
echo "--- 7) ENTRA_API_AUDIENCE が空なら確認しない ---"
rc=0
TEST_AUDIENCE='' run_preprovision "" || rc=$?
if [ "${rc}" -eq 0 ]; then
    pass "終了コード 0"
else
    fail "終了コード 0 を期待したが ${rc}"
fi
assert_log "ENTRA_API_AUDIENCE が空のため" "飛ばした理由をログに出す"
cleanup; work=""

# ---------------------------------------------------------------------------
if [ "${failures}" -eq 0 ]; then
    echo "すべて成功しました(preprovision.sh の platform-admin ゲート)"
    exit 0
fi
echo "失敗: ${failures} 件" >&2
exit 1
