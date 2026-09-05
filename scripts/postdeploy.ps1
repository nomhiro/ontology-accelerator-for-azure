# azd deploy 後にサンプルオントロジーを Core API 経由で投入する。
#
# 設計の意図は scripts/postdeploy.sh の冒頭コメントに書いてある（同じ内容を
# 二重に持たないため、ここでは要点だけ）。
#
#   - postprovision では API がまだ起動していないため postdeploy でなければならない
#   - Blob へ直接書くと PostgreSQL に行が入らず、MCP の list_namespaces が
#     空配列を返してサンプルが発見できない（P1-10）
#   - クライアントシークレットを持たない（Azure CLI が事前承認済み）

$ErrorActionPreference = "Stop"

if (-not $env:SERVICE_API_URI)     { throw "SERVICE_API_URI が必要です（azd の出力）" }
if (-not $env:ENTRA_API_AUDIENCE)  { throw "ENTRA_API_AUDIENCE が必要です（アプリ登録の appId）" }

$ns      = "retail-core"
$version = "1.0.0"
$sample  = "samples/retail-core.ttl"
$api     = $env:SERVICE_API_URI.TrimEnd("/")

if (-not (Test-Path $sample)) { throw "$sample が見つかりません" }

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
if (-not $token) { throw "トークンを取得できません" }

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
Invoke-Api -Method POST -Path "/namespaces/$ns/versions" -Expected 201, 409 -Body $pubBody
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
