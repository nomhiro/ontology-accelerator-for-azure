#!/bin/sh
# MCP → Core API のトークン転送を、**実際の Entra トークン**で確認する(ADR-0012)。
#
# **なぜ pytest ではなく手動スクリプトなのか**
#
# 検証には `az login` 済みの環境とアプリ登録が必要で、pytest からは満たせない。
# `packages/api/tests/conftest.py` は「接続できなければ失敗させる。静かに
# スキップしない」方針なので、az ログインに依存するテストを混ぜると、
# ログインしていない環境で常に失敗するか、方針を曲げてスキップさせることに
# なる。どちらも避ける。単体の検証(ヘッダ転送の論理)は
# `packages/mcp-server/tests/test_token_forwarding.py` にある。
#
# **費用は発生しない。** API と MCP をローカルで起動し、トークンだけを実際の
# Entra から取る。Azure 側で使うのはアプリ登録(既存)と自分のログインだけ。
#
# 前提:
#   - `az login` 済み
#   - アプリ登録の appId を ENTRA_API_AUDIENCE に渡す
#     (`requestedAccessTokenVersion: 2` と、Azure CLI の preAuthorizedApplications
#      が設定済みであること。ADR-0011 / P1-09 の構成)
#   - `just up` でローカルの PostgreSQL / Fuseki / Azurite が起動している
#
# 使い方:
#   ENTRA_TENANT_ID=<tenant> ENTRA_API_AUDIENCE=<appId> sh scripts/verify-mcp-auth.sh
set -eu

: "${ENTRA_TENANT_ID:?ENTRA_TENANT_ID が必要です}"
: "${ENTRA_API_AUDIENCE:?ENTRA_API_AUDIENCE が必要です(アプリ登録の appId)}"

API_PORT="${API_PORT:-8010}"
MCP_PORT="${MCP_PORT:-8011}"
API_URL="http://localhost:${API_PORT}"
MCP_URL="http://localhost:${MCP_PORT}"

work="$(mktemp -d)"
api_pid=""
mcp_pid=""

cleanup() {
    [ -n "${api_pid}" ] && kill "${api_pid}" 2>/dev/null || true
    [ -n "${mcp_pid}" ] && kill "${mcp_pid}" 2>/dev/null || true
    echo "--- API のログ (末尾) ---"
    tail -20 "${work}/api.log" 2>/dev/null || true
    echo "--- MCP のログ (末尾) ---"
    tail -20 "${work}/mcp.log" 2>/dev/null || true
    rm -rf "${work}"
}
trap cleanup EXIT

failures=0
check() {
    if [ "$2" = "$3" ]; then
        echo "ok - $1"
    else
        echo "NG - $1(期待: $3, 実際: $2)"
        failures=$((failures + 1))
    fi
}

# ---- API と MCP を AUTH_MODE=entra で起動する ----
echo "== API を起動します (AUTH_MODE=entra) =="
AUTH_MODE=entra \
ENTRA_TENANT_ID="${ENTRA_TENANT_ID}" \
ENTRA_API_AUDIENCE="${ENTRA_API_AUDIENCE}" \
    uv run --directory packages/api uvicorn ontology_api.main:app \
    --host 127.0.0.1 --port "${API_PORT}" > "${work}/api.log" 2>&1 &
api_pid=$!

echo "== MCP を起動します (AUTH_MODE=entra) =="
AUTH_MODE=entra \
ENTRA_TENANT_ID="${ENTRA_TENANT_ID}" \
ENTRA_API_AUDIENCE="${ENTRA_API_AUDIENCE}" \
CORE_API_URL="${API_URL}" \
MCP_ALLOWED_HOSTS="localhost:${MCP_PORT},127.0.0.1:${MCP_PORT},localhost,127.0.0.1" \
    uv run --directory packages/mcp-server uvicorn ontology_mcp.server:build_app --factory \
    --host 127.0.0.1 --port "${MCP_PORT}" > "${work}/mcp.log" 2>&1 &
mcp_pid=$!

# 起動を待つ(/healthz は無認証)。
i=0
while [ "${i}" -lt 30 ]; do
    api_ok="$(curl -s -o "${work}/health.out" -w '%{http_code}' "${API_URL}/healthz" || echo 000)"
    mcp_ok="$(curl -s -o "${work}/health.out" -w '%{http_code}' "${MCP_URL}/healthz" || echo 000)"
    if [ "${api_ok}" = "200" ] && [ "${mcp_ok}" = "200" ]; then break; fi
    i=$((i + 1))
    sleep 1
done
check "API と MCP の /healthz が無認証で応答する" "${api_ok}/${mcp_ok}" "200/200"

# ---- 実トークンを取る ----
echo "== アクセストークンを取得します =="
token="$(az account get-access-token \
    --scope "api://${ENTRA_API_AUDIENCE}/.default" \
    --query accessToken -o tsv)"
[ -n "${token}" ] || { echo "NG - トークンを取得できません" >&2; exit 1; }

# MCP のツールを呼ぶ(Streamable HTTP の JSON-RPC)。
call_tool() {
    auth="$1"
    body='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_namespaces","arguments":{}}}'
    if [ -n "${auth}" ]; then
        curl -s -o "${work}/out.json" -w '%{http_code}' --max-time 60 \
            -X POST "${MCP_URL}/mcp" \
            -H "Content-Type: application/json" \
            -H "Accept: application/json, text/event-stream" \
            -H "Authorization: ${auth}" \
            --data "${body}" || echo 000
    else
        curl -s -o "${work}/out.json" -w '%{http_code}' --max-time 60 \
            -X POST "${MCP_URL}/mcp" \
            -H "Content-Type: application/json" \
            -H "Accept: application/json, text/event-stream" \
            --data "${body}" || echo 000
    fi
}

# ---- 1. トークン無し: ツールエラーになる ----
echo "== トークン無しで list_namespaces を呼びます =="
call_tool "" > /dev/null
if grep -q "Bearer" "${work}/out.json" 2>/dev/null; then
    echo "ok - トークン無しは Bearer が必要である旨のエラーになる"
else
    echo "NG - トークン無しのエラー内容が期待と違う"
    head -c 500 "${work}/out.json"; echo
    failures=$((failures + 1))
fi

# ---- 2. 壊れたトークン: 検証で落ちる ----
echo "== 壊れたトークンで呼びます =="
call_tool "Bearer not.a.real.token" > /dev/null
if grep -q "検証に失敗" "${work}/out.json" 2>/dev/null; then
    echo "ok - 壊れたトークンは MCP 側の検証で落ちる"
else
    echo "NG - 壊れたトークンが検証で落ちていない"
    head -c 500 "${work}/out.json"; echo
    failures=$((failures + 1))
fi

# ---- 3. 実トークン: Core API まで通る ----
echo "== 実トークンで呼びます =="
call_tool "Bearer ${token}" > /dev/null
if grep -q '"result"' "${work}/out.json" 2>/dev/null; then
    echo "ok - 実トークンで list_namespaces が Core API まで通る"
else
    echo "NG - 実トークンでも通っていない(ここが P1-12 の本題)"
    head -c 800 "${work}/out.json"; echo
    failures=$((failures + 1))
fi

if [ "${failures}" -gt 0 ]; then
    echo "失敗: ${failures} 件" >&2
    exit 1
fi
echo "すべて成功しました(MCP → Core API のトークン転送)"
