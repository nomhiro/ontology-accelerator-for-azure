# OWL 推論と OWL 2 プロファイル適合の検査 (P2B-01、ADR-0021)。
#
# **CI とローカルで同じイメージを使う。** 推論器は Java であり、ホストに JDK を
# 要求すると「ローカルでは検査していない」状態になる(shellcheck をローカルと CI で
# 同じ docker イメージに固定したのと同じ理由)。
#
# **ELK と、ELK が引く OWL API だけを入れる。** HermiT (LGPL-3.0 単独) は
# 入れない(ADR-0005 決定4 / ADR-0021 決定4)。ROBOT を使わないのも、その配布
# jar が HermiT と JFact を同梱しているためである(jar の中身を実際に確認した)。
# 同梱されていないことの確認方法は docs/third-party-licenses.md にある。

# ---- ビルド段: 依存を解決して shade jar を作る ----
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build

# **pom.xml だけを先に入れて依存を落とす。** ソースを変えたときに依存の
# ダウンロードをやり直さないため(レイヤキャッシュを効かせる)。
COPY pom.xml ./
RUN mvn -B -q dependency:go-offline

COPY src ./src
RUN mvn -B -q package

# ---- 実行段: JRE だけ ----
FROM eclipse-temurin:21-jre
WORKDIR /work

# **jar 自身が同梱物の NOTICE を持っている。** shade の
# ApacheNoticeResourceTransformer が集約する(pom.xml 参照)。Apache-2.0 §4d の
# 「NOTICE の内容を継承する」義務はそこで満たしている。
COPY --from=build /build/target/reasoner-check.jar /opt/reasoner/reasoner-check.jar

# JVM のヒープは入力の規模に応じて外から調整する。既定は控えめにしておく
# (CI のランナーは 7GB 程度しかない)。
ENV JAVA_TOOL_OPTIONS="-Xmx1g"

# 検査対象は引数で渡す。**既定の引数は置かない** — 何を検査したかが
# コマンドラインに現れないと、CI のログから追えない。
ENTRYPOINT ["java", "-jar", "/opt/reasoner/reasoner-check.jar"]
