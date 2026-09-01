# ロードマップ

各 Phase は「**完了時に何ができるようになるか**」で区切る。実装の分量ではなく、獲得する能力で区切ることで、途中で止めても意味のある単位になる。

現在の作業状況とタスクの一覧は [`docs/backlog.md`](backlog.md) にある。**このファイルは方針、backlog は状態**を持つ。

---

## 全体像

| Phase | 名称 | 獲得する能力 | 状態 |
|---|---|---|---|
| **Phase 0** | スキャフォールド | リポジトリが `azd` テンプレートとして成立し、CI が通る | **完了** |
| **Phase 1** | MVP「器が動く」 | `azd up` 一発でデプロイでき、サンプルを SPARQL で引ける | **主要部分は完了。残りあり** |
| **Phase 2** | Scan/Model + 運用 | AI がオントロジーを作れる **かつ** 運用し続けられる | 未着手 |
| **Phase 3** | Serve フル | エージェントが連邦クエリとベクトル検索をフル活用できる | 未着手 |
| **Phase 4** | ハードニング | 本番品質・OSS 公開 | 未着手 |

Phase 2 が**2 本柱**である点が当初のロードマップからの変更である。理由は [ADR-0009](adr/0009-ontology-operations.md) に記録した。

---

## Phase 0 — スキャフォールド（完了）

**完了条件**: すべて達成済み。

- リポジトリが Azure Developer CLI テンプレートとして成立する（`azure.yaml`、`infra/`、`azd-service-name` タグ）
- uv workspace（Python 3.12）+ pnpm workspace（Node 22）が解決する
- `docker compose up` で Fuseki + PostgreSQL が起動する
- CI が 3 ジョブ（Python / Web / Bicep）ともグリーン
- ADR-0001〜0008、アーキテクチャ、費用試算、第三者ライセンスを文書化

---

## Phase 1 — MVP「器が動く」

**完了条件**: `azd up` 一発で ACA + Fuseki + Core API + MCP + PostgreSQL がデプロイされ、同梱サンプルオントロジーを SPARQL で検索でき、AI エージェントが MCP（`sparql_query` / `list_namespaces`）経由で参照できる。名前空間 CRUD、Entra JWT 検証、SPARQL 攻撃面対策を含む。AI 機能はまだ無い。

### 達成済み（Azure 実機で検証、検証後に `azd down --purge`）

| 項目 | 実測 |
|---|---|
| `azd up` 一発成功 | provision 9分5秒 + deploy 2分1秒 |
| マイグレーション | Entra パスワードレス + `pg_advisory_lock` で成功 |
| 名前空間ごとの TDB2 構築 | `urn:ontology:graph/retail-core/1.0.0` へ 60 トリプル |
| SPARQL 読み戻し | `retail-core`=60件、予約 `ds`=0件 |
| SSRF 遮断 | `SERVICE` で IMDS へ → HTTP 422 |
| 認証境界 | 401 でフェイルクローズ（`ENTRA_API_AUDIENCE` が空でも） |
| Fuseki の隔離 | `external=false`（internal ingress のみ） |
| 完全削除 | `azd down --purge` 23分51秒、Key Vault も purge |
| 起動時の再構築 | **4.6 秒**（Blob一覧 0.7s → tdbloader 0.06s → JVM 2.5s） |
| CI | GitHub Actions で全 8 ジョブ green |

テストは 45 件（Phase 0）→ **105 件**。

### 未達成 — 完了条件に届いていない点

**エージェントがサンプルを発見できない。** `postprovision` が PostgreSQL に行を入れないため `GET /namespaces` と MCP の `list_namespaces` が空配列を返す。書き込み順序 Blob → PostgreSQL → Fuseki の 2 段目が飛んでいる。本筋の対策は Entra アプリ登録が前提（[`backlog.md`](backlog.md) の `P1-09` / `P1-10`）。

### 残りのタスク

[`backlog.md`](backlog.md) の `P1-*` を参照。**Critical 1 件（`P1-C1`）を含む。**

`P1-C1` は「既定グラフに全バージョンが載り、新旧の定義が同時に返る」問題で、[ADR-0009](adr/0009-ontology-operations.md) の決定 2 に対応する Phase 1 側の欠陥修正である（保持ポリシーそのものの実装は Phase 2 の `P2B-02`）。

### 必須スパイク

| # | 内容 | 状態 |
|---|---|---|
| ① | 起動時再構築の所要時間実測と射影ループの検証 | **完了**（4.6 秒。名前空間1件・60トリプル） |
| ② | ACA の idle/active 課金比率の実測 | 未実施。費用試算の最大の不確実性（8 倍差） |
| ③ | Ontop 配布物のライセンス確認 | 未実施 |

---

## Phase 2 — Scan/Model + 運用

**2 本柱**である。片方だけでは Phase 2 を完了としない。

### 柱 A: AI がオントロジーを作れる

**完了条件**: 顧客 DB に接続してスキーマを自動発見し、LLM がオントロジー候補（OWL/SHACL）を生成し、Web でレビュー・承認して公開できる。

- 顧客 DB 接続とスキーマ自動発見（scan-job）
- LLM によるオントロジー候補生成
- Web でのレビュー・承認フロー（グラフ可視化を含む）
- pyshacl による SHACL 検証
- 名前空間 RBAC の強制
- 監査証跡の PROV-O 表現（[ADR-0006](adr/0006-ontology-versioning-and-audit.md)）

### 柱 B: 運用し続けられる

**完了条件**: オントロジーが増え続けても、人の理解が追従できる。矛盾が機械的に検出され、廃止の経路があり、健全性が測れる。

根拠は [ADR-0009](adr/0009-ontology-operations.md)。同 ADR の決定 1〜8 に対応する。

- 決定可能なものを機械が判定する層（構文検証 → SHACL → **OWL 推論器を CI へ**）〔決定 1〕
- **保持ポリシーの実装**（ストアに載せる版の制御。既定グラフは常に単一の版を指す）〔決定 2〕
- 廃止のライフサイクル（`owl:deprecated` + 後継への参照。IRI は削除も再利用もしない）〔決定 3〕
- 名前空間と用語の責任者〔決定 4〕
- 健全性指標（未参照の用語、責任者未設定、再承認が古い、SHACL 違反、未射影）〔決定 5〕
- 想定質問（Competency Questions）を SPARQL テストとして CI で実行〔決定 6〕
- 「なぜ」を参照時に返す（`reason` / `diff` を書き、読み出す）〔決定 7〕
- 意味的差分の計算と差分レビュー〔決定 7〕
- 領域間マッピング（SKOS `closeMatch` 等）〔決定 8〕

**ADR-0005 からの変更**: OWL 推論器の導入を Phase 4 → Phase 2 に前倒しする。「最も安い正しさの担保」であるため（[ADR-0009](adr/0009-ontology-operations.md) の根拠を参照）。

---

## Phase 3 — Serve フル

**完了条件**: エージェントが、実データを実体化しない連邦クエリとベクトル検索を通じて、必要な文脈を自力で組み立てられる。

- Ontop VKG（R2RML 管理 + 実データを実体化しない連邦クエリ）
- Azure AI Search 統合（ベクトル/ハイブリッド検索、`search_context` ツール）
- Metric Service
- Context Manager のオーケストレーション
- （任意）Microsoft Purview コネクタ（[ADR-0007](adr/0007-no-purview-dependency.md) により依存はしない）
- 定義と実データの乖離検出（Ontop 経由が前提のためここに置く）

---

## Phase 4 — ハードニング

**完了条件**: 本番環境に置ける品質で、第三者が OSS として採用できる。

- production プロファイル（VNet / Private Endpoint / AKS 昇格ガイド）
- 可観測性・負荷試験
- ライセンス自動スキャンの CI 化
- GitHub Actions の依存更新（現在 Node.js 20 対象のアクションが強制的に 24 で動いている）
- awesome-azd 申請（アーキ図画像・タグが必須）
- 表示名の最終決定（`Ontology Accelerator for Azure` は暫定。商標の論点は [ADR-0008](adr/0008-independent-implementation.md)）
- v0.1.0 リリース

---

## 参照実装との関係

[ADR-0008](adr/0008-independent-implementation.md) のとおり、本プロジェクトは AWS Context Ontology Accelerator の**フォークではなく独立実装**である。

ロードマップの観点で重要な事実を [ADR-0009](adr/0009-ontology-operations.md) のコンテキストに記録した。**参照実装は初回構築に特化しており、受理後の運用フェーズを扱っていない。** したがって Phase 2 の柱 B は、参照できる先行実装が無い領域である。設計を自分で決める必要があり、同時にそれが差別化の余地でもある。
