# azd deploy 後にサンプルオントロジーを Core API 経由で投入する。
#
# 設計の意図は scripts/postdeploy.sh の冒頭コメントに書いてある（同じ内容を
# 二重に持たないため、ここでは要点だけ）。
#
#   - postprovision では API がまだ起動していないため postdeploy でなければならない
#   - Blob へ直接書くと PostgreSQL に行が入らず、MCP の list_namespaces が
#     空配列を返してサンプルが発見できない（P1-10）
#   - クライアントシークレットを持たない（Azure CLI が事前承認済み）
#
# **PostgreSQL のブートストラップ・マイグレーション・ファイアウォール規則の
# 開閉もこのフックが行う（ADR-0011）。** アプリのロール（UAMI）にはテーブルの
# 所有権も DDL 権限も無いため、マイグレーションは運用者（Entra 管理者）が
# ここで実行するしかない。運用者のマシンからは既定でファイアウォールが
# 届かない（AllowAllAzureServicesAndResources は Azure 内のみ）ため、
# 一時的な規則をここで作って必ず削除する。
#
# **注意**: `$ErrorActionPreference = "Stop"` は PowerShell のコマンドレット
# エラーには効くが、`az` / `uv` のようなネイティブコマンドの失敗（非ゼロ終了）には
# 効かない。そのため、ネイティブコマンドの直後は必ず `$LASTEXITCODE` を確認して
# `throw` する（確認を忘れると、マイグレーション失敗後にサンプル投入へ進んで
# しまい、失敗が見えなくなる）。

$ErrorActionPreference = "Stop"

if (-not $env:SERVICE_API_URI)     { throw "SERVICE_API_URI が必要です（azd の出力）" }
if (-not $env:ENTRA_API_AUDIENCE)  { throw "ENTRA_API_AUDIENCE が必要です（アプリ登録の appId）" }
if (-not $env:AZURE_RESOURCE_GROUP) { throw "AZURE_RESOURCE_GROUP が必要です（azd の出力）" }
if (-not $env:POSTGRES_SERVER_NAME) { throw "POSTGRES_SERVER_NAME が必要です（azd の出力）" }
if (-not $env:POSTGRES_HOST)        { throw "POSTGRES_HOST が必要です（azd の出力）" }
if (-not $env:POSTGRES_DATABASE)    { throw "POSTGRES_DATABASE が必要です（azd の出力）" }
if (-not $env:POSTGRES_USER)        { throw "POSTGRES_USER が必要です（azd の出力。UAMI の名前）" }
if (-not $env:AZURE_PRINCIPAL_NAME) { throw "AZURE_PRINCIPAL_NAME が必要です（preprovision フックが設定）" }

$ns      = "retail-core"
$version = "1.0.0"
$sample  = "samples/retail-core.ttl"
$api     = $env:SERVICE_API_URI.TrimEnd("/")

if (-not (Test-Path $sample)) { throw "$sample が見つかりません" }

# ---- ファイアウォール規則（運用者のIPを一時的に許可する。ADR-0011 決定4） ----
#
# try/finally で必ず削除を試みる（途中で throw しても実行される）。
$firewallRuleName = "postdeploy-operator"
$firewallCreated = $false

function Remove-OperatorFirewallRule {
    if (-not $script:firewallCreated) { return }
    Write-Host "postdeploy: ファイアウォール規則 $firewallRuleName を削除します"
    az postgres flexible-server firewall-rule delete `
        --resource-group $env:AZURE_RESOURCE_GROUP `
        --server-name $env:POSTGRES_SERVER_NAME `
        --name $firewallRuleName --yes | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $script:firewallCreated = $false
    } else {
        Write-Host "postdeploy: ファイアウォール規則の削除に失敗しました。手動で確認してください（az postgres flexible-server firewall-rule delete --resource-group $env:AZURE_RESOURCE_GROUP --server-name $env:POSTGRES_SERVER_NAME --name $firewallRuleName）" -ForegroundColor Red
    }
}

try {
    Write-Host "postdeploy: 運用者のIPを取得します"
    # 0.0.0.0/0 のような代替は行わない。取得できなければ失敗させる（ADR-0011 決定4）。
    $operatorIp = (Invoke-WebRequest -Uri "https://api.ipify.org" -TimeoutSec 10 -SkipHttpErrorCheck).Content.Trim()
    if ($operatorIp -notmatch '^[0-9.]+$') {
        throw "運用者のIPを取得できません（ipify応答: '$operatorIp'）"
    }
    Write-Host "postdeploy: 運用者のIP = $operatorIp"

    Write-Host "postdeploy: ファイアウォール規則 $firewallRuleName を作成します"
    az postgres flexible-server firewall-rule create `
        --resource-group $env:AZURE_RESOURCE_GROUP `
        --server-name $env:POSTGRES_SERVER_NAME `
        --name $firewallRuleName `
        --start-ip-address $operatorIp `
        --end-ip-address $operatorIp | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ファイアウォール規則の作成に失敗しました" }
    $firewallCreated = $true

    # ---- PostgreSQL のブートストラップ（マイグレーション前。ADR-0011 決定2） ----
    #
    # ontology_owner の作成・UAMI の pgaadauth 登録・将来のテーブルへの既定権限。
    # ALTER DEFAULT PRIVILEGES は CREATE TABLE より前でなければ効かないため、
    # マイグレーションより先に実行する（順序が重要）。
    Write-Host "postdeploy: PostgreSQL をブートストラップします（マイグレーション前）"
    $env:POSTGRES_ADMIN_USER = $env:AZURE_PRINCIPAL_NAME
    $env:POSTGRES_APP_ROLE   = $env:POSTGRES_USER
    uv run python scripts/bootstrap-db.py pre
    if ($LASTEXITCODE -ne 0) { throw "bootstrap-db.py pre が失敗しました" }

    # ---- マイグレーション（運用者が ontology_owner として実行。ADR-0011 決定3） ----
    #
    # アプリのロール（POSTGRES_USER = UAMI）は DDL 権限を持たないため、ここでは
    # POSTGRES_USER を運用者自身に一時的に上書きして接続する。ontology_api.migrate
    # を使う（docker-entrypoint.sh から外した分、ここが唯一の実行場所になる。
    # アドバイザリロックは複数運用者の同時実行に備えて残っている）。
    Write-Host "postdeploy: マイグレーションを ontology_owner で実行します"
    $savedPostgresUser = $env:POSTGRES_USER
    $env:POSTGRES_USER = $env:AZURE_PRINCIPAL_NAME
    $env:MIGRATION_ROLE = "ontology_owner"
    uv run --directory packages/api python -m ontology_api.migrate
    $migrateExitCode = $LASTEXITCODE
    $env:POSTGRES_USER = $savedPostgresUser
    Remove-Item Env:\MIGRATION_ROLE -ErrorAction SilentlyContinue
    if ($migrateExitCode -ne 0) { throw "マイグレーションが失敗しました" }

    # ---- PostgreSQL のブートストラップ（マイグレーション後） ----
    #
    # 既存テーブルへの GRANT と、audit_events の DELETE 剥奪（追記専用にする）。
    Write-Host "postdeploy: PostgreSQL をブートストラップします（マイグレーション後）"
    uv run python scripts/bootstrap-db.py post
    if ($LASTEXITCODE -ne 0) { throw "bootstrap-db.py post が失敗しました" }
} finally {
    # ---- ファイアウォール規則を削除する（開けたままにしない） ----
    Remove-OperatorFirewallRule
}

# ---- API が応答するまで待つ ----
Write-Host "postdeploy: API の起動を待ちます ($api)"
$ready = $false
for ($i = 1; $i -le 40; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "$api/healthz" -TimeoutSec 15 -SkipHttpErrorCheck
        if ($r.StatusCode -eq 200) { $ready = $true; break }
        Write-Host "postdeploy:   [$i] /healthz -> $($r.StatusCode)"
    } catch {
        Write-Host "postdeploy:   [$i] /healthz -> 応答なし"
    }
    Start-Sleep -Seconds 15
}
if (-not $ready) { throw "API が応答しません" }
Write-Host "postdeploy: API が応答しました"

# ---- トークンを取得 ----
Write-Host "postdeploy: アクセストークンを取得します"
$token = az account get-access-token --scope "api://$($env:ENTRA_API_AUDIENCE)/.default" --query accessToken -o tsv
if ($LASTEXITCODE -ne 0 -or -not $token) { throw "トークンを取得できません" }

# 呼び出しの共通処理。期待するコードの配列を渡し、一致しなければ失敗させる。
function Invoke-Api {
    param([string]$Method, [string]$Path, [int[]]$Expected, [string]$Body)
    $headers = @{ Authorization = "Bearer $token" }
    $params = @{
        Uri = "$api$Path"; Method = $Method; Headers = $headers
        TimeoutSec = 120; SkipHttpErrorCheck = $true
    }
    if ($Body) {
        $params.ContentType = "application/json"
        # 日本語を含む本文が化けないよう UTF-8 のバイト列で送る。
        $params.Body = [System.Text.Encoding]::UTF8.GetBytes($Body)
    }
    $res = Invoke-WebRequest @params
    if ($Expected -contains [int]$res.StatusCode) {
        Write-Host "postdeploy:   $Method $Path -> $($res.StatusCode)"
        return
    }
    Write-Host "postdeploy: $Method $Path -> $($res.StatusCode)（期待: $($Expected -join ' ')）" -ForegroundColor Red
    if ($res.Content) { Write-Host ($res.Content.Substring(0, [Math]::Min(600, $res.Content.Length))) }
    throw "$Method $Path が失敗しました"
}

# ---- 名前空間（409 は既存。azd up を繰り返しても失敗させない）----
Write-Host "postdeploy: 名前空間 $ns を作成します"
$nsBody = @{
    name         = $ns
    display_name = "小売ドメイン"
    description  = "同梱サンプル。Scan → Model のフロー(Phase 2)で置き換えられる想定"
    base_iri     = "https://example.com/ontology/retail#"
} | ConvertTo-Json -Compress
Invoke-Api -Method POST -Path "/namespaces" -Expected 201, 409 -Body $nsBody

# ---- 公開 → 提出 → 承認 ----
# publish は draft を作るだけで射影しない。approve で初めて既定グラフに載る
# （ADR-0010 決定1・5）。
Write-Host "postdeploy: サンプルを公開します"
$ttl = Get-Content -Path $sample -Raw -Encoding UTF8
$pubBody = @{ turtle = $ttl; version = $version } | ConvertTo-Json -Compress
# 200 は同一内容の再投入（冪等。P1-26）。409 は同じ版番号が別内容の場合。
Invoke-Api -Method POST -Path "/namespaces/$ns/versions" -Expected 201, 200, 409 -Body $pubBody
Invoke-Api -Method POST -Path "/namespaces/$ns/versions/$version/submit"  -Expected 200, 409
Invoke-Api -Method POST -Path "/namespaces/$ns/versions/$version/approve" -Expected 200, 409

# ---- 発見できることを確認する（P1-10 の完了条件そのもの）----
Write-Host "postdeploy: 名前空間が一覧に現れることを確認します"
$listed = Invoke-WebRequest -Uri "$api/namespaces" -Headers @{ Authorization = "Bearer $token" } `
    -TimeoutSec 30 -SkipHttpErrorCheck
if ($listed.Content -notmatch [regex]::Escape("`"$ns`"")) {
    Write-Host "postdeploy: 名前空間が一覧に現れません。PostgreSQL に行が入っていない可能性があります" -ForegroundColor Red
    Write-Host ($listed.Content.Substring(0, [Math]::Min(600, $listed.Content.Length)))
    throw "名前空間が発見できません"
}
Write-Host "postdeploy: 確認しました（$ns が一覧に含まれます）"
Write-Host "postdeploy: 完了しました"
