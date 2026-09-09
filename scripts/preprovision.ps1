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
#   - **失敗の理由を捨てない**（P1-14）。`az ad signed-in-user show` は
#     「サービスプリンシパルでログインしている」以外の理由でも失敗するため、
#     理由を捨てると認証の問題を型の問題として誤報する

$ErrorActionPreference = "Stop"

# 通常は運用者本人が az login している（User）。
$userError = az ad signed-in-user show --query userPrincipalName -o tsv 2>&1
$principalName = if ($LASTEXITCODE -eq 0) { ($userError | Out-String).Trim() } else { "" }
$principalType = "User"
$spError = ""

if (-not $principalName) {
    # サービスプリンシパルでログインしている場合（CI からの無人デプロイ等）。
    Write-Host "preprovision: signed-in user として解決できませんでした。サービスプリンシパルとして解決します"
    $appIdOutput = az account show --query user.name -o tsv 2>&1
    if ($LASTEXITCODE -eq 0) {
        $appId = ($appIdOutput | Out-String).Trim()
        $spOutput = az ad sp show --id $appId --query displayName -o tsv 2>&1
        if ($LASTEXITCODE -eq 0) {
            $principalName = ($spOutput | Out-String).Trim()
        } else {
            $spError = ($spOutput | Out-String).Trim()
        }
    } else {
        $spError = ($appIdOutput | Out-String).Trim()
    }
    $principalType = "ServicePrincipal"
}

if (-not $principalName) {
    Write-Host "preprovision: デプロイ実行者の Entra 表示名を解決できません" -ForegroundColor Red
    Write-Host "preprovision: 両方の経路が失敗しました。型(User / ServicePrincipal)の問題ではなく" -ForegroundColor Red
    Write-Host "              認証の問題である可能性が高いので、まず 'az login' が有効か" -ForegroundColor Red
    Write-Host "              (トークンが期限切れでないか)を確認してください。" -ForegroundColor Red
    Write-Host "--- az ad signed-in-user show の出力 ---"
    Write-Host ($userError | Out-String)
    Write-Host "--- サービスプリンシパルとしての解決の出力 ---"
    Write-Host $spError
    throw "デプロイ実行者の Entra 表示名を解決できません"
}

Write-Host "preprovision: AZURE_PRINCIPAL_NAME=$principalName を設定します"
azd env set AZURE_PRINCIPAL_NAME $principalName
if ($LASTEXITCODE -ne 0) { throw "azd env set に失敗しました" }

# principalType も解決する。PostgreSQL の Entra 管理者リソースは principalType を
# 要求し、実際の型と一致していなければ認証が成立しない。既定を User に固定して
# いると、CI がサービスプリンシパルでデプロイしたときに誤った型で登録される。
Write-Host "preprovision: AZURE_PRINCIPAL_TYPE=$principalType を設定します"
azd env set AZURE_PRINCIPAL_TYPE $principalType
if ($LASTEXITCODE -ne 0) { throw "azd env set (AZURE_PRINCIPAL_TYPE) に失敗しました" }
