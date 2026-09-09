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

テストは 45 件（Phase 0）→ **161 件**（Phase 1 の欠陥修正を含む）。

### 完了条件の達成状況

**完了条件はすべて満たした**（**2026-09-06**。当初 2026-09-05 に満たしたと記録したが、下記のとおり不正確だった）。

「エージェントが MCP 経由で参照できる」のうち**発見経路**を、`P1-09`（Entra アプリ登録と認証経路の実証）と `P1-10`（postprovision を Core API 経由にする）で閉じた。

```
トークン無し  /namespaces      -> 401
トークン有り  /namespaces      -> 200   retail-core が 1 件
SPARQL        owl:Class        -> Customer / Order / Product / Store
reconcile                      -> 孤児ゼロ
```

`postprovision` は `deploy` の前に走るため API がまだ起動していない。`postdeploy` へ移して API 経由にした。API が Blob・PostgreSQL・マニフェスト・Fuseki を正しい順序で書くため、**書き込み順序の 2 段目（PostgreSQL）が飛ぶ問題が構造的に解消した**。

**ただし上の確認は運用者のトークンで Core API を直接叩いたものであり、MCP 経路そのものは通っていなかった。** `P1-12` に着手して判明した（2026-09-06）: MCP は Core API を**認証ヘッダなしで**呼んでいたため、`AUTH_MODE=entra` のデプロイ環境ではツール呼び出しがすべて 401 になる。`tools/list` は MCP サーバ自身が返すので成功し、外からは動いているように見えていた。[ADR-0012](adr/0012-mcp-to-core-api-authentication.md) で呼び出し元のトークンを検証して転送する設計を決め、**実トークンで E2E を通して閉じた**（`scripts/verify-mcp-auth.sh`）。

**この食い違いから得た教訓**: 「エージェントが参照できる」を、エージェントが実際に通る経路ではなく**代理の経路**（運用者のトークンで API を直接叩く）で確認していた。完了条件の確認は、その条件を使う主体と同じ経路で行う。

残っているのは実機デプロイが必要なスパイクと、品質の底上げである。

### 残りのタスク

[`backlog.md`](backlog.md) の `P1-*` を参照。**Critical は残っていない**（`P1-C1` / `P1-C2` は完了）。

残るのは次の 3 件で、いずれも**費用の出る実機デプロイ窓**か設計判断が必要:

**Phase 1 の個別タスクはすべて完了した**（2026-09-09）。最後に残っていた `P1-25`（reconcile が欠落グラフを自動復旧するか）を [ADR-0013](adr/0013-reconcile-repairs-observed-divergence.md) で決めて実装し、`P1-26`（冪等な再 publish が 201 を返す）も直した。

`P1-25` の要点は **`projected_at` の意味の確定**である。「書き込み経路が完了した」と「ストアが今それを保持している」を混同していたため「非 NULL の版を再射影してよいか」という答えの出ない問いになっていた。分離すると、ストアの状態に答えられるのはストア自身だけ（ストアは再構築可能な派生物。ADR-0002）と決まり、**観測を根拠に修復する**という形に落ち着いた。あわせて、**既定グラフの欠落が `list_graphs` では見えない**（エージェントが読むのはそこなのに）という穴も塞いだ。

**費用の出る作業は完了した。** `P1-S2`（idle/active 課金）・`P1-14`（POSIX 経路）・MCP 認証の実機再確認を 2026-09-09 の 1 つのデプロイ窓（約 55 分）で片付けた。残る `P1-25` はローカルで完結する。

撤収時に **`azd down --purge` のプロセスが外部要因（実行環境のメモリ不足）で中断された**が、[`CLAUDE.md`](../CLAUDE.md) に記録してある想定どおりの結果になった: リソースグループの削除は ARM 側で完了し、**Key Vault の purge だけが残った**。`az keyvault purge` で手動完了させ、RG・論理削除された Key Vault・論理削除された Cognitive Services のすべてが無いことを確認した。**中断されうるからこそ、終了コードではなく副作用で確認する必要がある。**

`P1-C1` は「既定グラフに全バージョンが載り、新旧の定義が同時に返る」問題で、[ADR-0009](adr/0009-ontology-operations.md) の決定 2 に対応する Phase 1 側の欠陥修正である（保持ポリシーそのものの実装は Phase 2 の `P2B-02`）。

`P1-C1` / `P1-15`（未承認の版が射影される）/ `P1-16`（最小の承認 API）は**同一ラウンドで実装する**。射影先を状態で分ける設計を [ADR-0010](adr/0010-approval-and-projection.md) で確定させたため、3 件は分離できない。

### 必須スパイク

| # | 内容 | 状態 |
|---|---|---|
| ① | 起動時再構築の所要時間実測と射影ループの検証 | **完了**（4.6 秒。名前空間1件・60トリプル） |
| ② | ACA の idle/active 課金比率の実測 | **完了**（2026-09-09。Fuseki は待機時に idle の条件を満たし、CPU の条件に約 4 倍の余裕がある。「8 倍に上振れして月 $105」は起きない。[実測](cost-estimate.md#aca-の-idleactive-判定--実測して解消しました2026-09-09p1-s2)） |
| ③ | Ontop 配布物のライセンス確認 | **完了**（公式イメージに JDBC ドライバは同梱されておらず、懸念していたリスクは存在しなかった。[結論](third-party-licenses.md#ontop-配布イメージの-jdbc-ドライバr6--調査済み結論)） |

---

## Phase 2 — Scan/Model + 運用

**2 本柱**である。片方だけでは Phase 2 を完了としない。

### 柱 A: AI がオントロジーを作れる

**完了条件**: 顧客 DB に接続してスキーマを自動発見し、LLM がオントロジー候補（OWL/SHACL）を生成し、Web でレビュー・承認して公開できる。

- 顧客 DB 接続とスキーマ自動発見（scan-job）
- LLM によるオントロジー候補生成
- Web でのレビュー・承認フロー（グラフ可視化を含む）
- pyshacl による SHACL 検証 — **完了**（2026-09-09。`P2A-05`）
- 名前空間 RBAC の強制 — **完了**（2026-09-09。`P2A-06`、[ADR-0014](adr/0014-namespace-rbac.md)。四眼原則を含む）
- 監査証跡の PROV-O 表現（[ADR-0006](adr/0006-ontology-versioning-and-audit.md)）

### 柱 B: 運用し続けられる

**完了条件**: オントロジーが増え続けても、人の理解が追従できる。矛盾が機械的に検出され、廃止の経路があり、健全性が測れる。

根拠は [ADR-0009](adr/0009-ontology-operations.md)。同 ADR の決定 1〜8 に対応する。

- 決定可能なものを機械が判定する層（構文検証 → SHACL → **OWL 推論器を CI へ**）〔決定 1〕
- **保持ポリシーの実装**（ストアに載せる版の制御。既定グラフは常に単一の版を指す）〔決定 2〕
- 廃止のライフサイクル（`owl:deprecated` + 後継への参照。IRI は削除も再利用もしない）〔決定 3〕
- 名前空間と用語の責任者〔決定 4〕 — **完了**（2026-09-10。`P2B-04`、[ADR-0015](adr/0015-term-owners.md)。名前空間単位は `owner` ロール、用語単位は `term_owners`）
- 健全性指標（未参照の用語、責任者未設定、再承認が古い、SHACL 違反、未射影）〔決定 5〕
- 想定質問（Competency Questions）を SPARQL テストとして CI で実行〔決定 6〕 — **完了**（2026-09-09。`P2B-07`）
- 「なぜ」を参照時に返す（`reason` / `diff` を書き、読み出す）〔決定 7〕 — **`reason` は完了**（2026-09-09。`P2B-08`）。**監査を読み出す口も完了**（2026-09-10。`P2B-11`）。`diff` は `P2B-09`
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
- **VNet 内で実行されるマイグレーション経路**（[ADR-0011](adr/0011-database-privilege-separation.md) 決定6）。`publicNetworkAccess: Disabled` では運用者のマシンから届かないため、Phase 1 の`postdeploy` 経路が成立しない。Container Apps Job かパイプラインの実行環境を選ぶ
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
