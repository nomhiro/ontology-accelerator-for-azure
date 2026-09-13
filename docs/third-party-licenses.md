# 第三者コンポーネントのライセンス

本プロジェクトは [Apache License 2.0](../LICENSE) で公開されています。帰属表示は [NOTICE](../NOTICE) にも記載しています。

> **この表の範囲**: 設計判断を伴う主要コンポーネント(トリプルストア、推論器、RDF 処理系など)を対象としています。推移的依存を含む全依存関係の網羅的な棚卸しではありません。個々のバージョンは `uv.lock` と `pnpm-lock.yaml` が正本です。
>
> **ライセンスの自動スキャンは CI に入りました**(2026-09-13、`P4-03`、[ADR-0049](adr/0049-license-scanning-in-ci.md))。`scripts/check-licenses.py` が **Python(uv)と Node(pnpm)の全依存 288 件**を許諾リストと突き合わせ、**判定できないものがあれば CI を落とします**。
> **ただしこの表は引き続き手で維持します。** 自動スキャンが見るのはライセンスの識別子だけで、**「なぜそれを採ったか / 却下したか」は書けません**。範囲も違います(下記)。

## 採用するコンポーネント

| ライブラリ | ライセンス | 取得元 | 扱い |
|---|---|---|---|
| Apache Jena / Fuseki | Apache-2.0(確認済) | https://jena.apache.org/ | ✓ 既定のトリプルストア |
| Ontop | Apache-2.0(確認済) | https://ontop-vkg.org/ | ✓ **公式イメージに JDBC ドライバは同梱されていない**(確認済。下記 R6)。**2026-09-13 に 5.3.0 で再確認**(`P3-01`、[ADR-0046](adr/0046-virtual-knowledge-graph.md))。同梱物のライセンスはイメージ内の `/opt/ontop/copyright/` にあり、**GPL 単独のものは無い**(Apache-2.0 / MPL-2.0(H2)/ BSD(Protege)/ EPL-1.0+LGPL 二重(Logback)) |
| Oxigraph | MIT / Apache-2.0 | https://github.com/oxigraph/oxigraph | ✓ 代替ストア候補 |
| rdflib | BSD-3-Clause | https://github.com/RDFLib/rdflib | ✓ Python 側の RDF 処理 |
| pyshacl | Apache-2.0 | https://github.com/RDFLib/pySHACL | ✓ SHACL 検証(Phase 2) |
| ELK reasoner | Apache-2.0(確認済) | `io.github.liveontologies:elk-owlapi:0.6.0` / https://github.com/liveontologies/elk-reasoner | ✓ **既定の OWL 推論器。CI で使用中**(`containers/reasoner`、[ADR-0021](adr/0021-owl-reasoning-in-ci.md))。**結論は健全だが完全ではない**(データプロパティを扱えない)ため、検査は完全性を併せて報告する |
| OWL API | **Apache-2.0 / LGPL-3.0 のデュアル**(確認済) | `net.sourceforge.owlapi:owlapi-apibinding:5.1.20` / https://github.com/owlcs/owlapi | ✓ **Apache-2.0 の方を選択する。** ELK が推移的に引く(api / impl / parsers / rio / tools / oboformat)。**版は ELK に揃える** — `owlapi-distribution:5.5.1` を足すと同じクラスが二重に載って Turtle が読めなくなった(ADR-0021 決定4) |
| SLF4J (slf4j-api / slf4j-simple) | MIT(確認済) | https://github.com/qos-ch/slf4j | ✓ 推論器コンテナのログ。**2.0.13 の pom には `<licenses>` ブロックが無い**ので、jar 内の `META-INF/LICENSE.txt`(MIT の本文)と `Bundle-License` ヘッダで確認した。**Logback は使わない**(EPL-1.0 / LGPL-2.1 のデュアルで、選択の説明が要る割に得るものが無い) |
| HermiT | **LGPL-3.0** | https://www.hermit-reasoner.com/ | △ 同梱せず、任意有効化のコンテナビルド時取得。ACA Job の**別プロセス**として動かす構成が LGPL 上最も安全。NOTICE に明記 |
| PostgreSQL JDBC (pgjdbc) | BSD-2-Clause(確認済) | https://github.com/pgjdbc/pgjdbc | ✓ Ontop 用。**2026-09-13 に 42.7.7 を同梱した**(`P3-01`)。jar 内の `META-INF/LICENSE` と POM の両方で確認。SHA-256 を `containers/ontop/Dockerfile` に固定 |
| Microsoft JDBC Driver for SQL Server | MIT(確認済) | https://github.com/microsoft/mssql-jdbc | ✓ Ontop 用。**2026-09-13 に 12.10.0.jre11 を同梱した**(`P3-01`)。POM で確認(jar 内に LICENSE は無い)。SHA-256 を `containers/ontop/Dockerfile` に固定 |
| MySQL Connector/J | **GPL-2.0 with FOSS exception**(確認済) | https://github.com/mysql/mysql-connector-j | ✗ **同梱しない。** 利用者が実行時に `jdbc/` へ置く(下記 R6) |
| Oracle JDBC (ojdbc) | **Oracle 独自条項**(OSS ではない) | https://www.oracle.com/database/technologies/appdev/jdbc.html | ✗ **同梱しない。** 再配布が許諾されていない。利用者が実行時に置く |
| FastAPI | MIT | https://github.com/fastapi/fastapi | ✓ 利用中 |
| Fluent UI | MIT | https://github.com/microsoft/fluentui | ✓ 利用中 |
| MCP Python SDK | MIT | https://github.com/modelcontextprotocol/python-sdk | ✓ 利用中 |
| @azure/msal-browser | MIT | https://github.com/AzureAD/microsoft-authentication-library-for-js | ○ Web の認証(認可コードフロー + PKCE)。**2026-09-13 に追加**(`P2A-20`、[ADR-0045](adr/0045-web-auth.md))。**認証を設定したときだけ動的に読み込む**(約 256 kB) |
| Cytoscape.js | MIT | https://github.com/cytoscape/cytoscape.js | ○ グラフ可視化。**2026-09-13 に依存関係へ追加した**(`P2A-04`、[ADR-0044](adr/0044-review-ui.md) 決定5)。`apps/web` の依存で、**グラフのタブを開いたときだけ動的に読み込む**(約 445 kB) |
| SQLAlchemy | MIT | https://github.com/sqlalchemy/sqlalchemy | ✓ 正本(PostgreSQL)へのアクセス |
| asyncpg | Apache-2.0 | https://github.com/MagicStack/asyncpg | ✓ 正本(PostgreSQL)への async アクセス。psycopg3 は LGPL-3.0 のため採用しない |
| Alembic | MIT | https://github.com/sqlalchemy/alembic | ✓ PostgreSQL のスキーママイグレーション |
| azure-storage-blob | MIT | https://github.com/Azure/azure-sdk-for-python | ✓ 正本 TTL の読み書き |
| azure-core | MIT | https://github.com/Azure/azure-sdk-for-python | ✓ Entra ID の非同期トークン取得(`azure.identity.aio`)に必要な `[aio]` extra を明示するための直接依存 |

## 採用しないコンポーネント

| ライブラリ | ライセンス | 取得元 | 理由 |
|---|---|---|---|
| **owlready2** | **LGPL-3.0** | https://owlready2.readthedocs.io/ | ✗ **採用しない**。改変版 HermiT を同梱する Python ライブラリで、Apache-2.0 の本体にライブラリとして取り込むとコンテナイメージ配布時に LGPL の順守義務が絡む。rdflib + pyshacl + 別プロセスの Java 推論器で代替する |
| **ROBOT** | BSD-3-Clause(**ただし配布物が LGPL-3.0 を同梱**) | https://github.com/ontodev/robot | ✗ **採用しない**。ROBOT 自身は許諾的だが、**配布 jar (82 MB) の中身を列挙したところ `org/semanticweb/HermiT/...` と `jfact` が入っていた**(実測)。ADR-0005 決定4(HermiT を同梱しない)に反する。依存を自分で選べる形(`elk-owlapi` に直接依存)で代替する |
| Logback | EPL-1.0 / LGPL-2.1 のデュアル | https://github.com/qos-ch/logback | ✗ 採用しない。推論器のログは slf4j-simple(MIT)で足りる。デュアルライセンスの選択を説明する負担を負う理由が無い |
| Virtuoso Open Source | GPL | https://github.com/openlink/virtuoso-opensource | ✗ 検討対象外 |

---

## 自動スキャンが見るもの・見ないもの(`P4-03`、[ADR-0049](adr/0049-license-scanning-in-ci.md))

```bash
just check-licenses          # Python + Node
just check-licenses --list   # 判定した 288 件を全部並べる
```

**見るもの**: Python(uv)と Node(pnpm)の**全依存**(推移的依存を含む。
2026-09-13 時点で Python 83 件 + Node 205 件)。

**見ないもの**(意図的。決定6):

| 見ていないもの | 今どう扱っているか |
|---|---|
| `containers/reasoner` の shade jar(67 依存) | `reasoner-check.test.sh` が **HermiT / JFact の不在**を検査。依存全体のライセンスは上の手作業 |
| Ontop の基盤イメージ(27 の同梱ライセンス) | 2026-09-13 に目視で確認(**GPL 単独は無い**)。[ADR-0046](adr/0046-virtual-knowledge-graph.md) に記録 |
| JDBC ドライバ | `containers/ontop/Dockerfile` が SHA-256 で固定し、全文を `containers/ontop/jdbc-licenses/` に置く |
| Fuseki / postgres の基盤イメージ | **未検査** |

**「CI が通ったからライセンスは全部確認済み」ではありません。** この表の
右側が、今どこまで確かめているかです。

### 判定できないものは通しません

ライセンスの綴りは揺れます(実測で同じ Apache-2.0 が 4 通り。
**`pyshacl` は `License` 欄に全文 11,491 文字**を書いていました)。
綴りを寄せる表に無いものは **`unknown` として CI を落とします**。

**「知らない綴りだから通す」を 1 度でも許すと、この検査は
「緑なのに何も検証していない」状態になります。** 落ちたときは
`scripts/check-licenses.py` の `_ALIASES` に**実測した綴りを**足すか、
`ALLOWED` を広げて**理由をこの文書に書いてください**。

---

## LGPL コンポーネントの取り扱い

`HermiT` と `owlready2` はいずれも **LGPL-3.0** です。本プロジェクトは Apache-2.0 の配布物であるため、以下の方針を採ります。判断の記録は [ADR-0005](adr/0005-reasoner-boundary.md) にあります。

### HermiT — 同梱せず、別プロセスで任意利用

- 本プロジェクトの配布物(リポジトリ、コンテナイメージ)には**同梱しません**
- 利用したい場合の**任意有効化オプション**として、コンテナビルド時に利用者側が取得する方式を採ります
- 実行は **Azure Container Apps Job の別プロセス**として行います。同一プロセス内でリンクしないため、LGPL の観点で最も安全な構成です
- この方針は [NOTICE](../NOTICE) にも明記しています

### owlready2 — 採用しない

改変版 HermiT を**ライブラリとして同梱する** Python パッケージです。これを Apache-2.0 の本体に依存関係として取り込むと、コンテナイメージを配布する時点で LGPL の順守義務(利用者による差し替えの保証など)が絡みます。回避策を運用で維持するより、依存しない設計を選びます。

代替として **rdflib + pyshacl + 別プロセスの Java 推論器(ELK)** の組み合わせを用います。SHACL 検証は pyshacl(純 Python)で完結します。

> **Java 依存は Phase 2 で発生しました。** ADR-0005 の補記(2026-09-01)が OWL 推論器の導入を Phase 4 から Phase 2 へ前倒ししたためです。`containers/reasoner` が ELK + OWL API を含む JRE イメージ(513 MB)をビルドします。**HermiT と JFact が入っていないことは、ビルドした jar のエントリを列挙して確認しています**([ADR-0021](adr/0021-owl-reasoning-in-ci.md))。

### 配布物に HermiT が入っていないことの確認 — CI が検査します

**`containers/reasoner/reasoner-check.test.sh` が毎回検査します。** イメージから jar を取り出し、`hermit` / `jfact` に一致するエントリが 0 件であることと、`META-INF/NOTICE` が集約されていることを確認します。

**目視の確認では守れません。** 依存を 1 つ足すと推移的に入りうるためです(実際に `owlapi-distribution` を足していた時期は除外指定が必要でした)。この検査を意図的に壊すと落ちることも確認しています。

手作業で確かめる場合は次の通りです。

```bash
docker create --name reasoner-check-tmp ontology-reasoner:local
docker cp reasoner-check-tmp:/opt/reasoner/reasoner-check.jar ./reasoner-check.jar
docker rm reasoner-check-tmp
unzip -l reasoner-check.jar | grep -iE 'hermit|jfact' || echo "同梱なし"
```

### 推論器コンテナに入る依存の棚卸し(2026-09-11 時点)

`containers/reasoner` の shade jar (18.4 MiB) には **67 個の第三者依存**が入ります。ライセンスを機械的に集計した結果、**コピーレフトのコンポーネントは 1 つもありません**。

| ライセンス | 主なコンポーネント |
|---|---|
| Apache-2.0 | ELK、Jackson、Guava、Apache HttpComponents、Commons Codec / IO / RDF API、Caffeine、hppcrt、jcl-over-slf4j、puli / owlapi-proof |
| **Apache-2.0 / LGPL-3.0 のデュアル** | OWL API (api / apibinding / impl / parsers / rio / tools)。**Apache-2.0 を選択します** |
| BSD-3-Clause | JSONLD Java、OWLAPI :: OBO Format |
| Eclipse Distribution License v1.0 (= BSD-3-Clause) | Eclipse RDF4J 3.7.4 (model / rio / util) |
| MIT | SLF4J、Checker Qual |
| Public Domain | XZ for Java |

再集計するコマンド(**ビルド時だけのプラグインで、成果物には入りません**)。

```bash
docker run --rm -v "$PWD/containers/reasoner:/build:ro" -w /tmp/proj   maven:3.9-eclipse-temurin-21 sh -c   'mkdir -p /tmp/proj && cp /build/pom.xml /tmp/proj/ && cp -r /build/src /tmp/proj/    && cd /tmp/proj    && mvn -B -q org.codehaus.mojo:license-maven-plugin:2.4.0:add-third-party    && cat target/generated-sources/license/THIRD-PARTY.txt'
```

> **Java 側の棚卸しは引き続き手作業です**(`P4-02` 以降)。CI が機械的に守っているのは「HermiT / JFact が入っていないこと」だけで、**`containers/reasoner` の 67 依存のライセンス全体は見ていません**([ADR-0049](adr/0049-license-scanning-in-ci.md) 決定6)。依存を追加する Pull Request では上のコマンドを回してください。
>
> **Python と Node 側は CI が見ます**(`P4-03`)。`uv run python scripts/check-licenses.py` / `just check-licenses`。

---

## Ontop 配布イメージの JDBC ドライバ(R6) — 調査済み・結論

### 2026-09-13: 実装して確かめたこと(`P3-01`、[ADR-0046](adr/0046-virtual-knowledge-graph.md))

`containers/ontop/Dockerfile` を書いたときに `ontop/ontop:5.3.0` を実測しました。

| 確かめたこと | 結果 |
|---|---|
| `/opt/ontop/jdbc` の有無 | **ディレクトリ自体が無い**(ドライバも当然無い) |
| クラスパス | 起動スクリプトが `-cp "$ONTOP_HOME/lib/*:$ONTOP_HOME/jdbc/*"` を渡す。**利用者が置く前提の設計である** |
| 同梱物のライセンス | `/opt/ontop/copyright/` に 27 件。**GPL 単独は無い** |

**同梱したのは pgjdbc(BSD-2-Clause)と mssql-jdbc(MIT)の 2 つだけです。**
MySQL Connector/J(GPL-2.0 with FOSS exception)と Oracle JDBC(独自条項)は
同梱しません。**`/opt/ontop/jdbc` はマウント可能なまま残してあるので、
同梱しないドライバも利用者が実行時に置けます** — 「私たちが再配布しないこと」と
「利用者が使えないこと」は別です。

足した 2 つのライセンス全文は、イメージの `/opt/ontop/copyright/` に
`PGJDBC-LICENSE.txt` / `MSSQL-JDBC-LICENSE.txt` として置いてあります
(リポジトリでは `containers/ontop/jdbc-licenses/`)。

**ドライバは TLS を検証できる段で落としています。** `ontop/ontop:5.3.0` には
**CA 証書が 1 枚も入っていない**ため(実測。`/etc/ssl/certs` が空)、イメージの
中からは TLS の検証ができません。`--no-check-certificate` は使わず、
別のビルド段(`alpine`)で落として SHA-256 を検証し、成果物だけを持ち込みます。



**当初懸念していたリスクは存在しませんでした。** 公式の Ontop イメージ(`ontop/ontop`)には **JDBC ドライバが一切同梱されていません**。公式チュートリアルは利用者側で `jdbc/` ディレクトリを用意してドライバを入れ、`-v $PWD/jdbc:/opt/ontop/jdbc` でマウントすることを求めています(公式ドキュメントの記述: 「Make sure to have the `jdbc/` directory and the JDBC driver inside.」。例として挙げられている H2 のドライバも利用者が自分で取得します)。Ontop 本体は Apache-2.0 です。

したがって R6 の論点は「公式イメージに何が入っているか」ではなく、**「自前イメージに何を入れるか」**に移ります。

### 決めたこと

1. **同梱してよいドライバ**(許諾的で再配布に制約が無いもの)
   - PostgreSQL JDBC (pgjdbc): **BSD-2-Clause**
   - Microsoft JDBC Driver for SQL Server: **MIT**

   この 2 つは Azure 上の主要な関係データベース(Azure Database for PostgreSQL / Azure SQL)に対応し、Apache-2.0 の配布物に同梱しても問題がありません。

2. **同梱しないドライバ**(利用者が実行時に `jdbc/` へ置く)
   - **MySQL Connector/J: GPL-2.0 with FOSS exception。** FOSS exception により Apache-2.0 のソフトウェアと組み合わせられる可能性は高いものの、**例外条項の適用範囲の解釈に依存する形でコンテナイメージを再配布するリスクを取る必要がない**ため同梱しません。利用者が自分の環境で置くぶんには何の制約もかかりません
   - **Oracle JDBC (ojdbc): Oracle 独自条項で、そもそも再配布が許諾されていません**

3. **仕組みとして、`/opt/ontop/jdbc` をマウント可能なまま保ちます。** 同梱しないドライバは利用者が実行時に置けます。**私たちが再配布しないことと、利用者が使えないことは別**です。この分離を維持することが R6 への構造的な答えになります。

4. 自前イメージに新しいドライバを追加するときは、**このドキュメントの表に行を追加してから**追加します(NOTICE への記載が必要かどうかも同時に判断します)。

### 実装への反映

`containers/ontop/` は Phase 3 です。上記 1・2 を Dockerfile のコメントに書き、`P2A-01`(顧客 DB 接続)以降で対応する DB を増やすときにこの表を更新します。

---

## AWS 版 Context Ontology Accelerator との関係

`aws/context-ontology-accelerator` は **Apache-2.0** です(著作権表示: Amazon.com, Inc.、GitHub API で確認済)。商用利用・改変・再配布・派生物の作成すべてが許諾されており、**フォークすることすら法的には問題ありません**。

本プロジェクトが「参考にした新規実装」を選んだのは**ライセンス上の制約ではなく技術的判断**です(CDK / Smithy / Neptune への深い結合を持ち込まないため)。詳細な論点は [ADR-0008](adr/0008-independent-implementation.md) に記録しています。

- **著作権**: アーキテクチャや概念(Scan→Model→Serve、名前空間 RBAC の考え方など)は著作権の保護対象ではないため、コード・設定・ドキュメント本文・サンプルオントロジー・プロンプト文を一切コピーしない限り、Apache-2.0 の義務(§4: ライセンス全文の同梱、著作権表示の保持、変更点の明示、NOTICE の内容の継承)は発生しません。**逆に一部でもコピーした場合は義務が発生します。** 将来コピーする場合は対象ファイルを台帳管理し、NOTICE に追記する運用にします
- **特許**: Apache-2.0 §3 は「その成果物の利用者」に対する明示的な特許ライセンスを与えます。独立実装を選ぶとこの特許許諾を受けられないため、理論上はフォークより不利になります
- **商標**: Apache-2.0 §6 は商標権を許諾しません。"AWS" / "Amazon" をプロジェクト名・ブランディングに使わず、README に **Amazon Web Services および Microsoft と無関係(非提携・非承認・非スポンサー)である旨**を明記します

---

## ライセンス全文の入手先

| ライセンス | 全文 |
|---|---|
| Apache License 2.0 | https://www.apache.org/licenses/LICENSE-2.0 |
| MIT License | https://opensource.org/license/mit |
| BSD 3-Clause License | https://opensource.org/license/bsd-3-clause |
| LGPL-3.0 | https://www.gnu.org/licenses/lgpl-3.0.html |
