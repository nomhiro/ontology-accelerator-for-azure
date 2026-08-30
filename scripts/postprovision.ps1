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
    --name "approved/retail-core/1.0.0.ttl" `
    --file "samples/retail-core.ttl" `
    --auth-mode login --overwrite --only-show-errors
if ($LASTEXITCODE -ne 0) {
    throw "az storage blob upload が失敗しました (exit code: $LASTEXITCODE)"
}
Write-Host "postprovision: 完了しました"
