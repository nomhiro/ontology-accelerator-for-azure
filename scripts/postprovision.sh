#!/bin/sh
# azd provision 後にサンプルオントロジーを投入する。
#
# azd の postprovision フックは azure.yaml のあるリポジトリルートを cwd として
# 実行される(サービス単位のフックとは異なる)ため、相対パスはここを起点にする。
set -eu
: "${AZURE_STORAGE_ACCOUNT_NAME:?}"
echo "postprovision: サンプルオントロジーを Blob へ配置します"
az storage blob upload \
  --account-name "${AZURE_STORAGE_ACCOUNT_NAME}" \
  --container-name "${ONTOLOGY_BLOB_CONTAINER:-ontologies}" \
  --name "approved/retail-core/1.0.0.ttl" \
  --file "samples/retail-core.ttl" \
  --auth-mode login --overwrite --only-show-errors
echo "postprovision: 完了しました"
