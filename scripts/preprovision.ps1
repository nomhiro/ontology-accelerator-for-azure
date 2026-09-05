# `azd provision` の前に、デプロイを実行する運用者の Entra 表示名（UPN または
# サービスプリンシパルの表示名）を解決し、`AZURE_PRINCIPAL_NAME` として azd
# 環境に設定する。
#
# 設計の意図は scripts/preprovision.sh の冒頭コメントに書いてある（同じ内容を
# 二重に持たないため、ここでは要点だけ）。
#
#   - infra/modules/postgres.bicep の Entra 管理者登録には principalName
#     （UPN または表示名）が必要だが、azd はこれを自動的には環境変数にしない
#     （AZURE_PRINCIPAL_ID はオブジェクトIDで、azd が自動的に解決する）
#   - 取得に失敗したら失敗させる（空文字列で登録すると後続が分かりにくく壊れる）

$ErrorActionPreference = "Stop"

# 通常は運用者本人が az login している（User）。
$principalName = (az ad signed-in-user show --query userPrincipalName -o tsv 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $principalName) {
    # サービスプリンシパルでログインしている場合（CI からの無人デプロイ等）。
    Write-Host "preprovision: signed-in user が見つかりません。サービスプリンシパルとして解決します"
    $appId = az account show --query user.name -o tsv
    if ($LASTEXITCODE -ne 0 -or -not $appId) { throw "az account show に失敗しました" }
    $principalName = az ad sp show --id $appId --query displayName -o tsv
    if ($LASTEXITCODE -ne 0 -or -not $principalName) { throw "az ad sp show に失敗しました" }
}

if (-not $principalName) { throw "デプロイ実行者の Entra 表示名を解決できません" }

Write-Host "preprovision: AZURE_PRINCIPAL_NAME=$principalName を設定します"
azd env set AZURE_PRINCIPAL_NAME $principalName
if ($LASTEXITCODE -ne 0) { throw "azd env set に失敗しました" }
