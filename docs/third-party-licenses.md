# 第三者コンポーネントのライセンス

本プロジェクトは [Apache License 2.0](../LICENSE) で公開されています。帰属表示は [NOTICE](../NOTICE) にも記載しています。

> **この表の範囲**: 設計判断を伴う主要コンポーネント(トリプルストア、推論器、RDF 処理系など)を対象としています。推移的依存を含む全依存関係の網羅的な棚卸しではありません。個々のバージョンは `uv.lock` と `pnpm-lock.yaml` が正本です。
>
> **ライセンス自動スキャンの CI 化は Phase 4** で対応します。それまでは本ドキュメントを手動で維持するため、主要な依存関係を追加する際はこの表への追記を Pull Request に含めてください。

## 採用するコンポーネント

| ライブラリ | ライセンス | 取得元 | 扱い |
|---|---|---|---|
| Apache Jena / Fuseki | Apache-2.0(確認済) | https://jena.apache.org/ | ✓ 既定のトリプルストア |
| Ontop | Apache-2.0(確認済) | https://ontop-vkg.org/ | ✓ ただし配布イメージ同梱の JDBC ドライバは別ライセンスの可能性 → 自前 Dockerfile で必要分のみ追加(R6) |
| Oxigraph | MIT / Apache-2.0 | https://github.com/oxigraph/oxigraph | ✓ 代替ストア候補 |
| rdflib | BSD-3-Clause | https://github.com/RDFLib/rdflib | ✓ Python 側の RDF 処理 |
| pyshacl | Apache-2.0 | https://github.com/RDFLib/pySHACL | ✓ SHACL 検証(Phase 2) |
| ELK reasoner | Apache-2.0 | https://github.com/liveontologies/elk-reasoner | ✓ **既定の OWL 推論器**にする |
| HermiT | **LGPL-3.0** | https://www.hermit-reasoner.com/ | △ 同梱せず、任意有効化のコンテナビルド時取得。ACA Job の**別プロセス**として動かす構成が LGPL 上最も安全。NOTICE に明記 |
| FastAPI | MIT | https://github.com/fastapi/fastapi | ✓ 利用中 |
| Fluent UI | MIT | https://github.com/microsoft/fluentui | ✓ 利用中 |
| MCP Python SDK | MIT | https://github.com/modelcontextprotocol/python-sdk | ✓ 利用中 |
| Cytoscape.js | MIT | https://github.com/cytoscape/cytoscape.js | ○ グラフ可視化の候補。**まだ依存関係に追加していない**(Phase 2) |
| SQLAlchemy | MIT | https://github.com/sqlalchemy/sqlalchemy | ✓ 正本(PostgreSQL)へのアクセス |
| asyncpg | Apache-2.0 | https://github.com/MagicStack/asyncpg | ✓ 正本(PostgreSQL)への async アクセス。psycopg3 は LGPL-3.0 のため採用しない |
| Alembic | MIT | https://github.com/sqlalchemy/alembic | ✓ PostgreSQL のスキーママイグレーション |
| azure-storage-blob | MIT | https://github.com/Azure/azure-sdk-for-python | ✓ 正本 TTL の読み書き |
| azure-core | MIT | https://github.com/Azure/azure-sdk-for-python | ✓ Entra ID の非同期トークン取得(`azure.identity.aio`)に必要な `[aio]` extra を明示するための直接依存 |

## 採用しないコンポーネント

| ライブラリ | ライセンス | 取得元 | 理由 |
|---|---|---|---|
| **owlready2** | **LGPL-3.0** | https://owlready2.readthedocs.io/ | ✗ **採用しない**。改変版 HermiT を同梱する Python ライブラリで、Apache-2.0 の本体にライブラリとして取り込むとコンテナイメージ配布時に LGPL の順守義務が絡む。rdflib + pyshacl + 別プロセスの Java 推論器で代替する |
| Virtuoso Open Source | GPL | https://github.com/openlink/virtuoso-opensource | ✗ 検討対象外 |

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

代替として **rdflib + pyshacl + 別プロセスの Java 推論器(ELK)** の組み合わせを用います。**SHACL 検証は pyshacl(純 Python)で完結するため、Java 依存は Phase 4 まで発生しません。**

---

## Ontop 配布イメージの JDBC ドライバ(R6)

Ontop 本体は Apache-2.0 ですが、**公式配布イメージに同梱される JDBC ドライバは別ライセンスである可能性があります**(例: 商用 DB のドライバは再配布が制限されることがあります)。

対応: 公式イメージをそのまま再配布せず、**自前の Dockerfile で必要なドライバのみを追加**します。追加したドライバのライセンスは本ドキュメントに追記します。**Phase 1 の必須スパイクの 1 つとして、配布物のライセンスを確認します。**

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
