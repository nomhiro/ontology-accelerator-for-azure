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
#
# **PostgreSQL のブートストラップ・マイグレーション・ファイアウォール規則の
# 開閉もこのフックが行う（ADR-0011）。** アプリのロール（UAMI）にはテーブルの
# 所有権も DDL 権限も無いため、マイグレーションは運用者（Entra 管理者）が
# ここで実行するしかない。運用者のマシンからは既定でファイアウォールが
# 届かない（AllowAllAzureServicesAndResources は Azure 内のみ）ため、
# 一時的な規則をここで作って必ず削除する。
set -eu

: "${SERVICE_API_URI:?SERVICE_API_URI が必要です（azd の出力）}"
: "${ENTRA_API_AUDIENCE:?ENTRA_API_AUDIENCE が必要です（アプリ登録の appId）}"
: "${AZURE_RESOURCE_GROUP:?AZURE_RESOURCE_GROUP が必要です（azd の出力）}"
: "${POSTGRES_SERVER_NAME:?POSTGRES_SERVER_NAME が必要です（azd の出力）}"
: "${POSTGRES_HOST:?POSTGRES_HOST が必要です（azd の出力）}"
: "${POSTGRES_DATABASE:?POSTGRES_DATABASE が必要です（azd の出力）}"
: "${POSTGRES_USER:?POSTGRES_USER が必要です（azd の出力。UAMI の名前）}"
: "${AZURE_PRINCIPAL_NAME:?AZURE_PRINCIPAL_NAME が必要です（preprovision フックが設定）}"

NS="retail-core"
VERSION="1.0.0"
SAMPLE="samples/retail-core.ttl"
API="${SERVICE_API_URI%/}"

[ -f "${SAMPLE}" ] || { echo "postdeploy: ${SAMPLE} が見つかりません" >&2; exit 1; }

# ---- ファイアウォール規則（運用者のIPを一時的に許可する。ADR-0011 決定4） ----
#
# trap で EXIT 時に必ず削除を試みる。ステップ5で明示的に削除した後は
# firewall_created を false に戻すため、trap は二重削除の no-op になる。
FIREWALL_RULE_NAME="postdeploy-operator"
firewall_created="false"

delete_firewall_rule() {
    [ "${firewall_created}" = "true" ] || return 0
    echo "postdeploy: ファイアウォール規則 ${FIREWALL_RULE_NAME} を削除します"
    if az postgres flexible-server firewall-rule delete \
        --resource-group "${AZURE_RESOURCE_GROUP}" \
        --server-name "${POSTGRES_SERVER_NAME}" \
        --name "${FIREWALL_RULE_NAME}" --yes >/dev/null 2>&1; then
        firewall_created="false"
    else
        echo "postdeploy: ファイアウォール規則の削除に失敗しました。手動で確認してください（az postgres flexible-server firewall-rule delete --resource-group ${AZURE_RESOURCE_GROUP} --server-name ${POSTGRES_SERVER_NAME} --name ${FIREWALL_RULE_NAME}）" >&2
    fi
}
trap delete_firewall_rule EXIT

echo "postdeploy: 運用者のIPを取得します"
# 0.0.0.0/0 のような代替は行わない。取得できなければ失敗させる（ADR-0011 決定4）。
operator_ip="$(curl -s --max-time 10 https://api.ipify.org || true)"
case "${operator_ip}" in
    '' | *[!0-9.]*)
        echo "postdeploy: 運用者のIPを取得できません（ipify応答: '${operator_ip}'）" >&2
        exit 1
        ;;
esac
echo "postdeploy: 運用者のIP = ${operator_ip}"

echo "postdeploy: ファイアウォール規則 ${FIREWALL_RULE_NAME} を作成します"
az postgres flexible-server firewall-rule create \
    --resource-group "${AZURE_RESOURCE_GROUP}" \
    --server-name "${POSTGRES_SERVER_NAME}" \
    --name "${FIREWALL_RULE_NAME}" \
    --start-ip-address "${operator_ip}" \
    --end-ip-address "${operator_ip}" >/dev/null
firewall_created="true"

# ---- PostgreSQL のブートストラップ（マイグレーション前。ADR-0011 決定2） ----
#
# ontology_owner の作成・UAMI の pgaadauth 登録・将来のテーブルへの既定権限。
# ALTER DEFAULT PRIVILEGES は CREATE TABLE より前でなければ効かないため、
# マイグレーションより先に実行する（順序が重要）。
echo "postdeploy: PostgreSQL をブートストラップします（マイグレーション前）"
POSTGRES_ADMIN_USER="${AZURE_PRINCIPAL_NAME}" POSTGRES_APP_ROLE="${POSTGRES_USER}" \
    uv run python scripts/bootstrap-db.py pre

# ---- マイグレーション（運用者が ontology_owner として実行。ADR-0011 決定3） ----
#
# アプリのロール（POSTGRES_USER = UAMI）は DDL 権限を持たないため、ここでは
# POSTGRES_USER を運用者自身に上書きして接続する。ontology_api.migrate を
# 使う（docker-entrypoint.sh から外した分、ここが唯一の実行場所になる。
# アドバイザリロックは複数運用者の同時実行に備えて残っている）。
echo "postdeploy: マイグレーションを ontology_owner で実行します"
POSTGRES_USER="${AZURE_PRINCIPAL_NAME}" MIGRATION_ROLE="ontology_owner" \
    uv run --directory packages/api python -m ontology_api.migrate

# ---- PostgreSQL のブートストラップ（マイグレーション後） ----
#
# 既存テーブルへの GRANT と、audit_events の DELETE 剥奪（追記専用にする）。
echo "postdeploy: PostgreSQL をブートストラップします（マイグレーション後）"
POSTGRES_ADMIN_USER="${AZURE_PRINCIPAL_NAME}" POSTGRES_APP_ROLE="${POSTGRES_USER}" \
    uv run python scripts/bootstrap-db.py post

# ---- ファイアウォール規則を削除する（開けたままにしない） ----
delete_firewall_rule

# ---- scan-job のイメージを差し替える（ADR-0042 決定5） ----
#
# **azd はジョブのイメージを更新しない。** provision の時点では
# SERVICE_API_IMAGE_NAME がまだ空なので、Bicep はプレースホルダを置いている。
# azd がイメージを差し替えるのは azd-service-name タグの付いたコンテナアプリ
# だけで、Microsoft.App/jobs は対象外である（azd 1.28.0 が
# host: containerapp-job を扱えるかは確認できなかった。`azd show` は不正な
# host 値も黙って受け付けるため、受け付けたことは対応の証拠にならない）。
#
# **終了コードを見るだけでは足りない**ので、読み戻して一致を確認する。
#
# **ここで止めない。** ジョブは既定で Manual トリガ（ADR-0042 決定1）なので、
# 古いイメージのままでも自動では何も起こらない。サンプルの投入まで終わった
# デプロイを、この 1 点で失敗にするのは不釣り合いである。**代わりに大きく
# 警告する** — 差し替わっていないジョブを起動すると、プレースホルダは
# 終了しないプロセスなので実行はタイムアウトで Failed になる（静かに成功した
# ことにはならない）。
if [ -n "${SERVICE_SCAN_JOB_NAME:-}" ]; then
    if [ -n "${SERVICE_API_IMAGE_NAME:-}" ]; then
        echo "postdeploy: scan-job のイメージを差し替えます (${SERVICE_SCAN_JOB_NAME})"
        if az containerapp job update \
            --name "${SERVICE_SCAN_JOB_NAME}" \
            --resource-group "${AZURE_RESOURCE_GROUP}" \
            --image "${SERVICE_API_IMAGE_NAME}" >/dev/null; then
            # **副作用で確認する。** 終了コードだけでは差し替わった保証にならない。
            applied="$(az containerapp job show \
                --name "${SERVICE_SCAN_JOB_NAME}" \
                --resource-group "${AZURE_RESOURCE_GROUP}" \
                --query "properties.template.containers[0].image" -o tsv 2>/dev/null || echo '')"
            if [ "${applied}" = "${SERVICE_API_IMAGE_NAME}" ]; then
                echo "postdeploy: scan-job のイメージを確認しました (${applied})"
            else
                echo "postdeploy: 警告 — scan-job のイメージが一致しません" >&2
                echo "postdeploy:   期待: ${SERVICE_API_IMAGE_NAME}" >&2
                echo "postdeploy:   実際: ${applied:-（読み取れません）}" >&2
            fi
        else
            echo "postdeploy: 警告 — scan-job のイメージを差し替えられませんでした" >&2
            echo "postdeploy:   定期実行（P2A-19）は動きません。手で実行してください:" >&2
            echo "postdeploy:   az containerapp job update --name ${SERVICE_SCAN_JOB_NAME} --resource-group ${AZURE_RESOURCE_GROUP} --image ${SERVICE_API_IMAGE_NAME}" >&2
        fi
    else
        echo "postdeploy: 警告 — SERVICE_API_IMAGE_NAME が無いため scan-job のイメージを差し替えられません" >&2
        echo "postdeploy:   ジョブはプレースホルダのイメージを指したままです（既定は Manual トリガなので自動では走りません）" >&2
    fi
fi

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
    if [ "${code}" = "403" ]; then
        # **原因が設定側にあることを言い切る**(P2A-06、ADR-0014)。
        # トークンは取れているのに 403 なので「認証の失敗」と読み違えやすい。
        echo "postdeploy: 403 は権限不足です。名前空間の作成には Entra の" >&2
        echo "postdeploy: 'platform-admin' アプリロールが必要です。README の" >&2
        echo "postdeploy: 「必要な Azure 権限と Entra ID の前提」を参照してください" >&2
        echo "postdeploy: (割り当て直後はトークンを取り直す必要があります)" >&2
    fi
    return 1
}

# ---- 名前空間 ----
# 409 は既に存在する場合。azd up を繰り返しても失敗しないようにする。
# **同梱サンプルの名前空間だけ四眼原則を無効にする**(P2A-06、ADR-0014 決定4)。
# 運用者 1 人のデプロイで publish → submit → approve を通すため。
# 「デモのために既定を緩める」のではなく「デモの名前空間だけを緩める」。
# 実運用の名前空間では既定(有効)のままにする。
echo "postdeploy: 名前空間 ${NS} を作成します(四眼原則は無効。同梱サンプルのため)"
call POST "/namespaces" "201 409" \
    '{"name":"'"${NS}"'","display_name":"小売ドメイン","description":"同梱サンプル。Scan → Model のフロー(Phase 2)で置き換えられる想定","base_iri":"https://example.com/ontology/retail#","require_two_person_approval":false}'

# ---- 公開 → 提出 → 承認 ----
# publish は draft を作るだけで射影しない。approve で初めて既定グラフに載る
# （ADR-0010 決定1・5）。
echo "postdeploy: サンプルを公開します"
# **`python` ではなく `uv run python` を使う（P1-14）。**
# 多くの現代的な Linux ディストリビューションは `python` を PATH に置かず
# `python3` しか無い（Python 3 が既定になった時点で、各ディストリが
# 無印の `python` の提供をやめた）。実測で Azure Linux 3.0
# (mcr.microsoft.com/azure-cli) には `python` が無く、素の `python -c` は
# `command not found` になる。
#
# `python3` に変えるのではなく `uv run python` にするのは、このスクリプトが
# 既に `uv run` に依存している（bootstrap-db.py・マイグレーション）ためで、
# **同じ前提で動く経路に揃える**方が壊れにくい。`uv` が動くなら必ず動く。
# `uv run` の進捗は stderr に出るので `$(...)` の取り込みは汚れない。
# `reason` を渡す(P2B-08、ADR-0009 決定7)。同梱サンプルでも
# 「誰が・いつ・なぜ」が記録され、GET .../decisions で読み出せることを
# 実演する。理由が空の監査は説明にならない。
payload="$(uv run python -c "
import json, sys
ttl = open('${SAMPLE}', encoding='utf-8').read()
sys.stdout.write(json.dumps({
    'turtle': ttl,
    'version': '${VERSION}',
    'reason': '同梱サンプルの初期投入(azd のデプロイフックによる自動実行)',
}))
")"
# 200 は同一内容の再投入（冪等。P1-26）。409 は同じ版番号が別内容で使われて
# いる場合。どちらも繰り返し実行で失敗させない。
call POST "/namespaces/${NS}/versions" "201 200 409" "${payload}"
call POST "/namespaces/${NS}/versions/${VERSION}/submit"  "200 409"     '{"reason":"サンプルのため人のレビューを経ずに提出する"}'
call POST "/namespaces/${NS}/versions/${VERSION}/approve" "200 409"     '{"reason":"同梱サンプルの初期投入。Phase 2 の Scan/Model フローで置き換えられる想定"}'

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
