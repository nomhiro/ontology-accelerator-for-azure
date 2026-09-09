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

# ---- platform-admin アプリロールの確認（P2A-09、ADR-0014 決定2・3）----
#
# 設計の意図は scripts/preprovision.sh の該当箇所に書いてある（要点のみ）。
#
#   - provision の前で見るのは、postdeploy の 403 まで進むと約 11 分の
#     プロビジョニングと課金を使い切っているため
#   - 判定はトークンの roles クレームで行う（Graph の appRoleAssignments は
#     グループ経由の割り当てを見落とし、偽のブロッカーになる）
#   - **確認できなかったときは通す**（確認の仕組み自体を新しいブロッカーに
#     しない）。割り当てが無いと確定したときだけ止める
$authMode = if ($env:AUTH_MODE) { $env:AUTH_MODE } else { "entra" }
if ($authMode -eq "disabled") {
    Write-Host "preprovision: AUTH_MODE=disabled のため platform-admin の確認を飛ばします"
} elseif (-not $env:ENTRA_API_AUDIENCE) {
    Write-Host "preprovision: ENTRA_API_AUDIENCE が空のため platform-admin の確認を飛ばします"
    Write-Host "              (認証必須の経路は 401 になります。README のアプリ登録の手順を参照)"
} else {
    Write-Host "preprovision: platform-admin アプリロールを確認します"
    $tokenOutput = az account get-access-token --scope "api://$($env:ENTRA_API_AUDIENCE)/.default" --query accessToken -o tsv 2>&1
    if ($LASTEXITCODE -eq 0 -and $tokenOutput) {
        $token = ($tokenOutput | Out-String).Trim()
        # **トークンは標準入力で渡す。** 引数にすると履歴やプロセス一覧に残る。
        $token | uv run python scripts/check-platform-admin.py
        $rc = $LASTEXITCODE
        if ($rc -eq 2) {
            Write-Host "preprovision: **provision を中止します。**" -ForegroundColor Red
            Write-Host "              このまま進めても postdeploy が名前空間の作成で 403 になり、" -ForegroundColor Red
            Write-Host "              約 11 分のプロビジョニングと課金が無駄になります。" -ForegroundColor Red
            Write-Host "              scripts/setup-app-role.py で割り当ててから再実行してください。" -ForegroundColor Red
            throw "platform-admin アプリロールが割り当てられていません"
        } elseif ($rc -ne 0) {
            Write-Host "preprovision: platform-admin を確認できませんでした(続行します)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "preprovision: アクセストークンを取得できませんでした(続行します)" -ForegroundColor Yellow
        Write-Host ($tokenOutput | Out-String)
    }
}
