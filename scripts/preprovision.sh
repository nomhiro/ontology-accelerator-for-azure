#!/bin/sh
# `azd provision` の前に、デプロイを実行する運用者の Entra 表示名（UPN または
# サービスプリンシパルの表示名）を解決し、`AZURE_PRINCIPAL_NAME` として azd
# 環境に設定する。
#
# **なぜ必要か（ADR-0011 決定1）**
#
# infra/modules/postgres.bicep の Entra 管理者登録（`administrators` リソース）
# は `principalName` を要求する。`AZURE_PRINCIPAL_ID`（オブジェクトID）は azd が
# provision 時に自動的に解決する既定の環境変数だが、名前（UPN/表示名）側の既定
# 環境変数は無い。そのため自前でこのフックを用意する。
#
# ゲストユーザーの UPN は `<name>_<domain>#EXT#@<tenant>.onmicrosoft.com` の
# ような形式になる（この環境では実際にそう）。`pgaadauth_create_principal` に
# 渡す名前はこれと一致していなければならないため、加工せずそのまま使う。
#
# **取得に失敗したら失敗させる。** 名前が解決できないまま Entra 管理者登録が
# 空文字列で作られると、後続の postdeploy（bootstrap-db.py・マイグレーション）が
# 分かりにくい形で失敗する。
#
# **失敗の理由を捨てないこと（P1-14）。** `az ad signed-in-user show` は
# 「サービスプリンシパルでログインしている」以外の理由でも失敗する（`az login`
# の期限切れ、トークンキャッシュが読めない等）。以前は失敗の理由を
# `2>/dev/null` で捨てて「サービスプリンシパルとして解決します」と表示して
# いたため、**認証の問題を型の問題として誤って報告していた**（POSIX 経路の
# 検証中に、実際にこの誤報を踏んだ）。両方の経路の理由を保持して、
# どちらも失敗したときにまとめて出す。
set -eu

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# 通常は運用者本人が `az login` している（User）。
principal_name="$(az ad signed-in-user show --query userPrincipalName -o tsv 2>"${work}/user.err" || true)"
principal_type="User"

if [ -z "${principal_name}" ]; then
    # サービスプリンシパルでログインしている場合（CI からの無人デプロイ等）。
    # `az ad signed-in-user show` はユーザーログインでないと失敗するため、
    # サインイン中のアプリID から表示名を解決する。
    echo "preprovision: signed-in user として解決できませんでした。サービスプリンシパルとして解決します"
    app_id="$(az account show --query user.name -o tsv 2>>"${work}/sp.err" || true)"
    if [ -n "${app_id}" ]; then
        principal_name="$(az ad sp show --id "${app_id}" --query displayName -o tsv 2>>"${work}/sp.err" || true)"
    fi
    principal_type="ServicePrincipal"
fi

if [ -z "${principal_name}" ]; then
    {
        echo "preprovision: デプロイ実行者の Entra 表示名を解決できません"
        echo "preprovision: **両方の経路が失敗しました。** 型（User / ServicePrincipal）の"
        echo "              問題ではなく認証の問題である可能性が高いので、まず"
        echo "              'az login' が有効か（トークンが期限切れでないか）を確認してください。"
        echo "--- az ad signed-in-user show の出力 ---"
        cat "${work}/user.err" 2>/dev/null || true
        echo "--- サービスプリンシパルとしての解決の出力 ---"
        cat "${work}/sp.err" 2>/dev/null || true
    } >&2
    exit 1
fi

echo "preprovision: AZURE_PRINCIPAL_NAME=${principal_name} を設定します"
azd env set AZURE_PRINCIPAL_NAME "${principal_name}"

# principalType も解決する。PostgreSQL の Entra 管理者リソースは principalType を
# 要求し、**実際の型と一致していなければ認証が成立しない**。既定を User に
# 固定していると、CI がサービスプリンシパルでデプロイしたときに誤った型で
# 登録される(実装者が指摘した既存不備。ADR-0011 の変更で影響が広がった)。
#
# 判定は「サインイン中のユーザーとして解決できたか」で行う。
# az ad signed-in-user show はサービスプリンシパルでは失敗するため、
# 上で principal_name をどちらの経路で得たかがそのまま型になる。
echo "preprovision: AZURE_PRINCIPAL_TYPE=${principal_type} を設定します"
azd env set AZURE_PRINCIPAL_TYPE "${principal_type}"
