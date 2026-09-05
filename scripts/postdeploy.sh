#!/bin/sh
# azd deploy 後にサンプルオントロジーを Core API 経由で投入する。
#
# **なぜ postprovision ではなく postdeploy なのか**
#
# azd のフックは preprovision → provision → postprovision → predeploy → deploy →
# postdeploy の順で走る。postprovision の時点ではコンテナアプリのイメージが
# まだプレースホルダで、**Core API が起動していない**。API 経由で投入するには
# deploy の後、つまり postdeploy でなければならない。
#
# **なぜ API 経由なのか**（P1-10）
#
# 以前は Blob へ直接書いていたため、PostgreSQL に行が入らなかった。その結果
# `GET /namespaces` と MCP の `list_namespaces` が空配列を返し、**デプロイした
# サンプルがエージェント経路から発見できなかった**。書き込み順序
# Blob → PostgreSQL → Fuseki（不変条件2）の 2 段目が飛んでいた状態である。
#
# API 経由にすると、API が Blob・PostgreSQL・マニフェスト・Fuseki のすべてを
# 正しい順序で書くため、このスクリプトが個別に面倒を見る必要がなくなる。
#
# **認証**
#
# クライアントシークレットを持たない。Azure CLI がアプリ登録の
# preAuthorizedApplications に登録されているため、`az account get-access-token`
# で運用者自身の資格情報からトークンを取れる（ADR-0010 決定2の「外部の承認
# システムも API を叩く」と同じ考え方で、ここでは運用者が叩いている）。
set -eu

: "${SERVICE_API_URI:?SERVICE_API_URI が必要です（azd の出力）}"
: "${ENTRA_API_AUDIENCE:?ENTRA_API_AUDIENCE が必要です（アプリ登録の appId）}"

NS="retail-core"
VERSION="1.0.0"
SAMPLE="samples/retail-core.ttl"
API="${SERVICE_API_URI%/}"

[ -f "${SAMPLE}" ] || { echo "postdeploy: ${SAMPLE} が見つかりません" >&2; exit 1; }

# ---- API が応答するまで待つ ----
# deploy 直後はリビジョンが起動中で、マイグレーションも走っている。
echo "postdeploy: API の起動を待ちます (${API})"
ready="false"
i=0
while [ "${i}" -lt 40 ]; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${API}/healthz" || echo 000)"
    if [ "${code}" = "200" ]; then ready="true"; break; fi
    i=$((i + 1))
    echo "postdeploy:   [${i}] /healthz -> ${code}"
    sleep 15
done
[ "${ready}" = "true" ] || { echo "postdeploy: API が応答しません" >&2; exit 1; }
echo "postdeploy: API が応答しました"

# ---- トークンを取得 ----
echo "postdeploy: アクセストークンを取得します"
token="$(az account get-access-token \
    --scope "api://${ENTRA_API_AUDIENCE}/.default" \
    --query accessToken -o tsv)"
[ -n "${token}" ] || { echo "postdeploy: トークンを取得できません" >&2; exit 1; }

# 呼び出しの共通処理。HTTP コードと本文を取り出す。
# 期待するコード（空白区切り）を渡し、一致しなければ失敗させる。
call() {
    method="$1"; path="$2"; expected="$3"; body="${4:-}"
    tmp="$(mktemp)"
    if [ -n "${body}" ]; then
        code="$(curl -s -o "${tmp}" -w '%{http_code}' --max-time 120 \
            -X "${method}" "${API}${path}" \
            -H "Authorization: Bearer ${token}" \
            -H "Content-Type: application/json" \
            --data "${body}" || echo 000)"
    else
        code="$(curl -s -o "${tmp}" -w '%{http_code}' --max-time 120 \
            -X "${method}" "${API}${path}" \
            -H "Authorization: Bearer ${token}" || echo 000)"
    fi
    for ok in ${expected}; do
        if [ "${code}" = "${ok}" ]; then
            echo "postdeploy:   ${method} ${path} -> ${code}"
            rm -f "${tmp}"
            return 0
        fi
    done
    echo "postdeploy: ${method} ${path} -> ${code}（期待: ${expected}）" >&2
    head -c 600 "${tmp}" >&2; echo >&2
    rm -f "${tmp}"
    return 1
}

# ---- 名前空間 ----
# 409 は既に存在する場合。azd up を繰り返しても失敗しないようにする。
echo "postdeploy: 名前空間 ${NS} を作成します"
call POST "/namespaces" "201 409" \
    '{"name":"'"${NS}"'","display_name":"小売ドメイン","description":"同梱サンプル。Scan → Model のフロー(Phase 2)で置き換えられる想定","base_iri":"https://example.com/ontology/retail#"}'

# ---- 公開 → 提出 → 承認 ----
# publish は draft を作るだけで射影しない。approve で初めて既定グラフに載る
# （ADR-0010 決定1・5）。
echo "postdeploy: サンプルを公開します"
payload="$(python -c "
import json, sys
ttl = open('${SAMPLE}', encoding='utf-8').read()
sys.stdout.write(json.dumps({'turtle': ttl, 'version': '${VERSION}'}))
")"
# 409 は同じ版が既にある場合（繰り返し実行しても失敗させない）。
call POST "/namespaces/${NS}/versions" "201 409" "${payload}"
call POST "/namespaces/${NS}/versions/${VERSION}/submit"  "200 409"
call POST "/namespaces/${NS}/versions/${VERSION}/approve" "200 409"

# ---- 発見できることを確認する ----
# これが P1-10 の完了条件そのものである。PostgreSQL に行が入っていなければ
# 空配列が返る。
echo "postdeploy: 名前空間が一覧に現れることを確認します"
listed="$(curl -s --max-time 30 "${API}/namespaces" -H "Authorization: Bearer ${token}")"
case "${listed}" in
    *"\"${NS}\""*) echo "postdeploy: 確認しました（${NS} が一覧に含まれます）" ;;
    *)
        echo "postdeploy: 名前空間が一覧に現れません。PostgreSQL に行が入っていない可能性があります" >&2
        echo "${listed}" | head -c 600 >&2; echo >&2
        exit 1
        ;;
esac

echo "postdeploy: 完了しました"
