# azd provision 後にサンプルオントロジーを投入する。
#
# azd の postprovision フックは azure.yaml のあるリポジトリルートを cwd として
# 実行される(サービス単位のフックとは異なる)ため、相対パスはここを起点にする。
# scripts/postprovision.sh (POSIX 版) と同じ処理を PowerShell で行う。
$ErrorActionPreference = "Stop"

if (-not $env:AZURE_STORAGE_ACCOUNT_NAME) {
    throw "AZURE_STORAGE_ACCOUNT_NAME が設定されていません"
}
$container = if ($env:ONTOLOGY_BLOB_CONTAINER) { $env:ONTOLOGY_BLOB_CONTAINER } else { "ontologies" }

Write-Host "postprovision: サンプルオントロジーを Blob へ配置します"
az storage blob upload `
    --account-name $env:AZURE_STORAGE_ACCOUNT_NAME `
    --container-name $container `
    --name "versions/retail-core/1.0.0.ttl" `
    --file "samples/retail-core.ttl" `
    --auth-mode login --overwrite --only-show-errors
if ($LASTEXITCODE -ne 0) {
    throw "az storage blob upload が失敗しました (exit code: $LASTEXITCODE)"
}

# サンプルは Core API を経由せず Blob へ直接書くため、ローダ(load-snapshot.sh)が
# 承認状態を判断するためのマニフェスト(versions/<namespace>/_state.json、
# ADR-0010 決定7)も自分で書く必要がある。書かないとマニフェストが存在しない
# 名前空間として扱われ、ローダがサンプルの読み込みをスキップしてしまう
# (containers/fuseki/load-snapshot.sh 修正5)。同梱サンプルは常に「承認済みの
# 現行版」として宣言する。
#
# PowerShell の `>` リダイレクトは既定で UTF-16LE で書き出すため
# (CLAUDE.md の既知の罠)、生成側で明示的に UTF-8(BOM 無し)を指定する。
Write-Host "postprovision: マニフェストを配置します"
$manifestFile = [System.IO.Path]::GetTempFileName()
try {
    $generatedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $manifest = @"
{
  "schema": 1,
  "namespace": "retail-core",
  "current": "1.0.0",
  "versions": [
    {"version": "1.0.0", "status": "approved"}
  ],
  "generated_at": "$generatedAt"
}
"@
    [System.IO.File]::WriteAllText($manifestFile, $manifest, [System.Text.UTF8Encoding]::new($false))

    az storage blob upload `
        --account-name $env:AZURE_STORAGE_ACCOUNT_NAME `
        --container-name $container `
        --name "versions/retail-core/_state.json" `
        --file $manifestFile `
        --auth-mode login --overwrite --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "az storage blob upload (マニフェスト) が失敗しました (exit code: $LASTEXITCODE)"
    }
} finally {
    Remove-Item -Path $manifestFile -ErrorAction SilentlyContinue
}
Write-Host "postprovision: 完了しました"
