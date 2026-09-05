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
  --name "versions/retail-core/1.0.0.ttl" \
  --file "samples/retail-core.ttl" \
  --auth-mode login --overwrite --only-show-errors

# サンプルは Core API を経由せず Blob へ直接書くため、ローダ(load-snapshot.sh)が
# 承認状態を判断するためのマニフェスト(versions/<namespace>/_state.json、
# ADR-0010 決定7)も自分で書く必要がある。書かないとマニフェストが存在しない
# 名前空間として扱われ、ローダがサンプルの読み込みをスキップしてしまう
# (containers/fuseki/load-snapshot.sh 修正5)。同梱サンプルは常に「承認済みの
# 現行版」として宣言する。
echo "postprovision: マニフェストを配置します"
manifest_file="$(mktemp)"
trap 'rm -f "${manifest_file}"' EXIT
generated_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "${manifest_file}" <<EOF
{
  "schema": 1,
  "namespace": "retail-core",
  "current": "1.0.0",
  "versions": [
    {"version": "1.0.0", "status": "approved"}
  ],
  "generated_at": "${generated_at}"
}
EOF
az storage blob upload \
  --account-name "${AZURE_STORAGE_ACCOUNT_NAME}" \
  --container-name "${ONTOLOGY_BLOB_CONTAINER:-ontologies}" \
  --name "versions/retail-core/_state.json" \
  --file "${manifest_file}" \
  --auth-mode login --overwrite --only-show-errors
echo "postprovision: 完了しました"
