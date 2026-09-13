# Ontop VKG — リレーショナル DB を仮想グラフとして見せる (P3-01、ADR-0046)。
#
# **公式イメージに JDBC ドライバを足すだけのイメージである。** Ontop 本体は
# 再ビルドしない(Apache-2.0 の配布物をそのまま使う)。
#
# ## なぜ自前の Dockerfile が必要か
#
# **公式イメージには JDBC ドライバが 1 つも入っていない**(実測。5.3.0 には
# `/opt/ontop/jdbc` ディレクトリすら無い)。それでいて起動スクリプトは
# クラスパスに `/opt/ontop/jdbc/*` を含めている。つまり**ドライバは利用者が
# 置くもの**という設計であり、`P1-S3` が懸念した「配布イメージ同梱の
# ドライバのライセンス」というリスクは**存在しなかった**。
#
# 論点は「自前イメージに何を入れるか」に移る。
#
# ## 入れるドライバと、入れないドライバ
#
# | ドライバ | ライセンス | 扱い |
# |---|---|---|
# | pgjdbc 42.7.7 | BSD-2-Clause(jar 内の `META-INF/LICENSE` と POM で確認) | **入れる** |
# | mssql-jdbc 12.10.0.jre11 | MIT(POM で確認) | **入れる** |
# | MySQL Connector/J | GPL-2.0 with FOSS exception | **入れない** |
# | Oracle JDBC | 独自条項(再配布の許諾が無い) | **入れない** |
#
# **「私たちが再配布しないこと」と「利用者が使えないこと」は別である。**
# `/opt/ontop/jdbc` はマウント可能なまま残すので、入れないドライバも
# 利用者が実行時に置ける(`docs/third-party-licenses.md`)。

# ---- 取得段: TLS を検証できる場所でドライバを落とす ----
#
# **なぜ段を分けるのか。`ontop/ontop:5.3.0` には CA 証書が 1 枚も無い**
# (実測: `/etc/ssl/certs` が空で、`wget` が
# `Unable to locally verify the issuer's authority` で終了コード 5 になる)。
#
# **`--no-check-certificate` で逃げない。** SHA-256 で固定してあるので
# 内容の完全性は保てるが、「検証を切った」という設定がイメージに残ると、
# **次にここを触る人が別のダウンロードにもそれを流用する**。
# 検証できる段で落として、成果物だけを持ち込む。
FROM alpine:3.20 AS drivers

ARG PGJDBC_VERSION=42.7.7
ARG PGJDBC_SHA256=157963d60ae66d607e09466e8c0cdf8087e9cb20d0159899ffca96bca2528460
ARG MSSQL_JDBC_VERSION=12.10.0.jre11
ARG MSSQL_JDBC_SHA256=8b80e2a3d254c26f66d479bc51d2d235f054eeb6e8394260c129bbd7fc7394a7

# **タグ + チェックサムで固定する。** Maven Central が公開する SHA-1 で
# 本物であることを確かめ、そのバイト列から計算した SHA-256 をここに
# 書いてある。**ダウンロードが差し替わったらビルドが落ちる。**
RUN set -eu; \
    apk add --no-cache curl ca-certificates >/dev/null; \
    mkdir -p /drivers; \
    curl -fsSL -o /drivers/postgresql.jar \
      "https://repo1.maven.org/maven2/org/postgresql/postgresql/${PGJDBC_VERSION}/postgresql-${PGJDBC_VERSION}.jar"; \
    echo "${PGJDBC_SHA256}  /drivers/postgresql.jar" | sha256sum -c -; \
    curl -fsSL -o /drivers/mssql-jdbc.jar \
      "https://repo1.maven.org/maven2/com/microsoft/sqlserver/mssql-jdbc/${MSSQL_JDBC_VERSION}/mssql-jdbc-${MSSQL_JDBC_VERSION}.jar"; \
    echo "${MSSQL_JDBC_SHA256}  /drivers/mssql-jdbc.jar" | sha256sum -c -; \
    chmod 0444 /drivers/*.jar

# ---- 実行段: 公式イメージ + ドライバ ----
FROM ontop/ontop:5.3.0

# **root で足して、実行ユーザは戻す。** 公式イメージの
# entrypoint / healthcheck / 実行ユーザをそのまま使う。
USER root

# `/opt/ontop/jdbc` は起動スクリプトがクラスパスに含める場所である
# (`-cp "$ONTOP_HOME/lib/*:$ONTOP_HOME/jdbc/*"`)。**別の場所に置いても
# 読まれない。**
COPY --from=drivers /drivers/ /opt/ontop/jdbc/

# **同梱物のライセンスを一緒に置く。** 公式イメージは
# `/opt/ontop/copyright/` に同梱物のライセンスを持っている(Apache-2.0 §4d の
# 「NOTICE の内容を継承する」義務はそこで満たされている)。足した 2 つの
# ドライバについても同じ場所に置いておく。
COPY jdbc-licenses/ /opt/ontop/copyright/

USER ontop

# **ポータルページを既定で切る。** `/index.html` は同梱のクエリを並べる
# 画面で、内部 ingress にしか出さない前提でも**要らないものは出さない**。
# `ontology_core.vkg` はここへ問い合わせない(`/sparql` だけを使う)。
ENV ONTOP_DISABLE_PORTAL_PAGE=true

# **遅延初期化を既定にする。** 既定(即時)だと、マッピングが参照する関係が
# 無いときに**コンテナが起動しない**(実測)。起動しないと、運用者に届くのは
# 「Ontop が落ちている」だけで、原因(表名の誤り)はコンテナのログの中に
# 埋まる。遅延なら起動はして、最初のクエリで 500 を返す — **そのとき
# Core API が本文を捨ててログに出す**(ADR-0046 決定10)。
#
# **遅延は失敗を覚えない**(実測)。関係が後から現れれば次のクエリで通る。
ENV ONTOP_LAZY_INIT=true

# JVM のヒープ。既定の 512m から上げてある — 仮想グラフの書き換えは
# クエリごとに行われるが、DB のメタデータはすべてメモリに載る。
ENV ONTOP_JAVA_ARGS="-Xmx1g"
