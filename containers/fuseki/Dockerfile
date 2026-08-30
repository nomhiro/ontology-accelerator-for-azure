# Apache Jena Fuseki のコンテナイメージ。
#
# 公式配布物を検証つきで取得して組み立てる。Fuseki サーバー本体に加えて
# Jena のコマンドライン一式(tdb2.tdbloader)も入れる。init コンテナが起動時に
# Blob 上の正本 TTL から TDB2 を作り直すのに使うため。
#
#   docker build -t ontology-fuseki containers/fuseki

ARG JENA_VERSION=6.2.0

# ---------------------------------------------------------------------------
# 配布物の取得と検証
# ---------------------------------------------------------------------------
FROM eclipse-temurin:21-jdk AS dist

ARG JENA_VERSION
ARG APACHE_MIRROR=https://archive.apache.org/dist/jena/binaries
WORKDIR /dist

# Apache が公開している SHA-512 で改ざん検知を行う。
RUN set -eux; \
    for artifact in "apache-jena-fuseki-${JENA_VERSION}" "apache-jena-${JENA_VERSION}"; do \
        curl -fsSL -o "${artifact}.tar.gz" "${APACHE_MIRROR}/${artifact}.tar.gz"; \
        curl -fsSL -o "${artifact}.tar.gz.sha512" "${APACHE_MIRROR}/${artifact}.tar.gz.sha512"; \
        awk '{print $1}' "${artifact}.tar.gz.sha512" | tr -d '\n' > expected.sha512; \
        printf '  %s\n' "${artifact}.tar.gz" >> expected.sha512; \
        sha512sum -c expected.sha512; \
        rm expected.sha512; \
        tar -xzf "${artifact}.tar.gz"; \
    done; \
    mv "apache-jena-fuseki-${JENA_VERSION}" /dist/fuseki; \
    mv "apache-jena-${JENA_VERSION}" /dist/jena


# ---------------------------------------------------------------------------
# 実行イメージ
# ---------------------------------------------------------------------------
FROM eclipse-temurin:21-jre

LABEL org.opencontainers.image.title="Ontology Accelerator for Azure - Fuseki" \
      org.opencontainers.image.licenses="Apache-2.0"

# curl は init コンテナが Blob から正本を取得するために必要。
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    rm -rf /var/lib/apt/lists/*; \
    useradd --uid 10001 --home-dir /fuseki --create-home fuseki

COPY --from=dist /dist/fuseki /opt/fuseki
COPY --from=dist /dist/jena /opt/jena

COPY config.ttl /etc/fuseki/config.ttl
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY load-snapshot.sh /opt/fuseki-init/load-snapshot.sh
# admin API ($/datasets への dbName+dbType のフォーム POST)が使う雛形。
# config.ttl のデータセットレベル設定(arq:queryTimeout, tdb2:unionDefaultGraph)を
# 動的に作ったデータセットにも継承させるため、配布物の既定テンプレートを
# 上書きする。詳細は templates/config-tdb2 のコメントを参照。
COPY templates/config-tdb2 /fuseki/templates/config-tdb2

RUN chmod +x /usr/local/bin/entrypoint.sh /opt/fuseki-init/load-snapshot.sh \
    && mkdir -p /fuseki/databases /fuseki/staging \
    && chown -R fuseki:fuseki /fuseki /etc/fuseki

ENV FUSEKI_HOME=/opt/fuseki \
    FUSEKI_BASE=/fuseki \
    JENA_HOME=/opt/jena \
    PATH="/opt/jena/bin:${PATH}" \
    TDB_LOCATION=/fuseki/databases/ds \
    FUSEKI_DATASET=ds \
    FUSEKI_ADMIN_USER=admin \
    JAVA_OPTIONS="-Xmx1g"

USER fuseki
WORKDIR /fuseki
EXPOSE 3030

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
