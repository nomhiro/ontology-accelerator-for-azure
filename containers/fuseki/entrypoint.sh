#!/bin/sh
# Fuseki の起動ラッパー。次の順に行う。
#
#   1. 管理 API ($/datasets) を保護する shiro.ini を環境変数から生成する
#      (shiro.ini は環境変数を展開しないため、ここで書き出す)
#   2. 正本(Blob)から TDB2 を再構築する
#   3. Fuseki を起動する
#
# 2 を init コンテナではなく**この entrypoint で行う**理由:
# azd は provision → deploy の順に実行し、deploy が差し替えるのはメインコンテナの
# イメージだけである。init コンテナのイメージは Bicep 経由でしか更新されないため、
# init を使うと `azd up` 一回ではタグが揃わず、利用者が `azd provision` を
# もう一度実行しなければならなかった。同一イメージ内の entrypoint で行えば
# コードとタグは構造的に常に一致する。
# 詳細は docs/adr/0002-triple-store-as-rebuildable-projection.md を参照。
set -eu

: "${FUSEKI_BASE:=/fuseki}"
: "${FUSEKI_HOME:=/opt/fuseki}"
: "${FUSEKI_ADMIN_USER:=admin}"
: "${SNAPSHOT_LOADER:=/opt/fuseki-init/load-snapshot.sh}"

if [ -z "${FUSEKI_ADMIN_PASSWORD:-}" ]; then
    echo "entrypoint: FUSEKI_ADMIN_PASSWORD が未設定です。管理 API を無効にして起動します" >&2
fi

mkdir -p "${FUSEKI_BASE}/databases" "${FUSEKI_BASE}/staging"

# 管理 API は認証必須、データの読み書きはネットワーク境界(internal ingress)で守る。
# Fuseki を外部 ingress に出してはならない。
cat > "${FUSEKI_BASE}/shiro.ini" <<EOF
[main]
plainMatcher = org.apache.shiro.authc.credential.SimpleCredentialsMatcher
iniRealm.credentialsMatcher = \$plainMatcher

[users]
${FUSEKI_ADMIN_USER} = ${FUSEKI_ADMIN_PASSWORD:-}

[urls]
/\$/status  = anon
/\$/ping    = anon
/\$/**      = authcBasic,user[${FUSEKI_ADMIN_USER}]
/**         = anon
EOF
chmod 600 "${FUSEKI_BASE}/shiro.ini"

# 正本から TDB2 を作り直す。失敗したら起動しない(空のグラフを黙って配らない)。
# 読み込むものが無い場合はローダ側が空の TDB2 を用意して正常終了する。
if [ -x "${SNAPSHOT_LOADER}" ]; then
    echo "entrypoint: 正本から TDB2 を再構築します" >&2
    "${SNAPSHOT_LOADER}"
else
    echo "entrypoint: ${SNAPSHOT_LOADER} が無いため再構築をスキップします" >&2
fi

# JAVA_OPTIONS は "-Xmx1g -Xms512m" のように複数のフラグを含みうるため、
# 単語分割させる必要がある。ここは意図的に引用しない。
# shellcheck disable=SC2086
exec java ${JAVA_OPTIONS:-} \
    -jar "${FUSEKI_HOME}/fuseki-server.jar" \
    --config=/etc/fuseki/config.ttl \
    "$@"
