#!/bin/sh
# Fuseki の起動ラッパー。
#
# 管理 API ($/datasets) を保護するための shiro.ini を環境変数から生成してから
# サーバーを起動する。shiro.ini は環境変数の展開を行わないため、ここで書き出す。
set -eu

: "${FUSEKI_BASE:=/fuseki}"
: "${FUSEKI_HOME:=/opt/fuseki}"
: "${FUSEKI_ADMIN_USER:=admin}"

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

# JAVA_OPTIONS は "-Xmx1g -Xms512m" のように複数のフラグを含みうるため、
# 単語分割させる必要がある。ここは意図的に引用しない。
# shellcheck disable=SC2086
exec java ${JAVA_OPTIONS:-} \
    -jar "${FUSEKI_HOME}/fuseki-server.jar" \
    --config=/etc/fuseki/config.ttl \
    "$@"
