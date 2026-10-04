# コントリビューションガイド

Ontology Accelerator for Azure への貢献に興味を持っていただきありがとうございます。

> **現在のステータス**: Phase 0〜2の実装は完了し、Phase 3と本番ハードニングを進めています。完了タスクにも実機・実ブラウザの残検証があります。現在の作業状態は [GitHub Issues](https://github.com/nomhiro/ontology-accelerator-for-azure/issues)、ID・出典・判断履歴は [バックログ索引](docs/backlog.md)、方針は [ロードマップ](docs/roadmap.md) を確認してください。

## Issueから開発する

1. 対応Issueの受け入れ条件・出典・依存と関連ADRを読む。`scope:required` は該当Phaseの必須、`scope:optional` は任意・条件付きでリリース阻害要因ではありません。`kind:decision` は実装前に採否や方式を決める課題です。
2. 着手するIssueへ方針をコメントし、`status:in-progress` を付ける。担当可能なら自分を割り当てる。Copilotへの割り当て・起動は、環境の準備と利用者の承認を確認してから行います。
3. 前提が満たせない場合は `status:blocked` と具体的な依存Issue・外部条件・必要な入力を記録する。関連するだけのIssueを依存扱いしない。再開時は理由をコメントしてblockedを外します。
4. 修正前に落ちる回帰テストを確認してから直し、該当領域の検査を行う。IssueとPRへ実行コマンド・結果・未実施理由・手動確認の範囲を残す。
5. 完全対応したPRに `Closes #番号`、部分対応は `Refs #番号` を記載する。未達条件があれば閉じず、マージによる完了後は進行中/ブロックのラベルを外す。

新しい作業は [開発タスクテンプレート](.github/ISSUE_TEMPLATE/development_task.md) で
起票し、`docs/backlog.md` で採番を確認してください。ID・出典・Issueリンク・判断履歴
が変わるときは、コードと同じコミットで索引を更新します。**状態だけの変更を文書へ
二重記録しません。** 設計判断で採用した実装が残るなら独立Issueへ分割し、決定と
完了を混同しないでください。見送りは根拠/ADRを記録し `wontfix` で閉じます。
重複は既存Issueを示して `duplicate` で閉じます。

詳細は [ADR-0052](docs/adr/0052-issue-based-development.md) を参照。
GitHubを更新できない場合は結果と未更新項目を報告し、更新済みと書かないでください。

## コーディングエージェント

- [AGENTS.md](AGENTS.md): Claude/Copilotに共通の詳細規則・17不変条件・既知の罠・検証規律
- [CLAUDE.md](CLAUDE.md): Claude Codeのimport入口
- [.github/copilot-instructions.md](.github/copilot-instructions.md): Copilot向けの重要規則の要約
- [.github/instructions/](.github/instructions/): Python/API、Web、インフラ/シェルのパス別指示

Copilot CLI・クラウドエージェントはCLAUDE.mdにも対応しますが、Chat/IDE/レビューは
指示ファイルの対応範囲が異なります。詳細文書のリンクは自動読込を保証しません。
実際に使うクライアントで適用を確認してください。

指示ファイルの整備と、クラウド環境で依存・DB・型生成・検査が動くことは別です。
クラウドsetupは [`P4-11`](https://github.com/nomhiro/ontology-accelerator-for-azure/issues/45)、
Dev Container再現性は [`P4-12`](https://github.com/nomhiro/ontology-accelerator-for-azure/issues/43) で追跡します。
今回クラウドエージェントの実動確認はしていません。エージェントに
Azure資格情報を渡したり、検証として無断でprovisionを実行したりしないでください。

## 行動規範

本プロジェクトへの参加者は [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)(Contributor Covenant 2.1)に従うことが求められます。

## ライセンスと貢献の取り扱い

本プロジェクトは [Apache License 2.0](LICENSE) で公開されています。Apache-2.0 §5 の定めにより、**あなたが意図的に投稿した貢献は、特段の申し出がない限り Apache-2.0 の条件の下で提供されたものとみなされます**。CLA(Contributor License Agreement)への署名は求めていません。

他のプロジェクトからコード・ドキュメント・データを持ち込む場合は、必ず Pull Request の説明に**出典とそのライセンス**を明記してください。Apache-2.0 と非互換なライセンス(GPL / LGPL 等)の成果物は、本体に取り込むことができません。判断に迷う場合は、実装前に Issue で相談してください。

## 貢献の種類ごとの進め方

### 設計へのフィードバック

[`docs/architecture.md`](docs/architecture.md) と [`docs/adr/`](docs/adr/) を読み、疑問・反論・見落としを Issue として提起してください。特に以下は積極的に議論したい論点です。

- グラフ永続化の設計(トリプルストアを再構築可能な射影として扱う判断 / [ADR-0002](docs/adr/0002-triple-store-as-rebuildable-projection.md))
- トリプルストアの選定(Fuseki を既定とする判断 / [ADR-0001](docs/adr/0001-rdf-store-selection.md))
- コスト試算の妥当性([`docs/cost-estimate.md`](docs/cost-estimate.md))

### バグ報告

観測した挙動・期待・再現手順・環境・ログをバグ報告テンプレートで記録してください。
未検証と既知の不具合、ADRで受け入れた制約を区別してください。

### 機能追加

**実装を始める前に Issue を立てて合意を取ってください。** 本プロジェクトは YAGNI を設計原則としており、ロードマップの Phase を先取りする実装や、現時点で必要性が確認できていない抽象化は、たとえ動作しても取り込まないことがあります。どの Phase に属する機能なのかを Issue に明記してください。

### アーキテクチャ上の決定

設計方針を変更する提案は、**ADR(アーキテクチャ決定記録)として提出**してください。既存の ADR を覆す場合は、その ADR のステータスを更新する形の変更も含めてください。書式は既存の ADR([`docs/adr/0001-rdf-store-selection.md`](docs/adr/0001-rdf-store-selection.md) など)に揃えてください。

```
# ADR-000X: <タイトル>

- ステータス: 提案中 | 承認済み | 却下 | 非推奨(ADR-000Y により置換)
- 日付: YYYY-MM-DD

## コンテキスト
## 決定
## 根拠
## 検討した代替案
## 結果(トレードオフ・影響)
```

「検討した代替案」と「結果(トレードオフ・影響)」は必須です。却下した選択肢とその理由が書かれていない ADR は、後から読む人にとって価値がありません。

## 開発環境

同じ `just` recipe を PowerShell、Git Bash、Linux/Dev Container で使います。

### 前提ツール

Azure CLI / Azure Developer CLI (`azd`) / Docker / uv / pnpm / Node.js 22 / Python 3.12 / `just`

pnpmはCIと同じ9系を使います。シェル検査には `sh` / `jq` が必要です。
Windows固有の注意とDocker経由の検査は [AGENTS.md](AGENTS.md) を読んでください。
同梱 [Dev Container](.devcontainer/) も Node.js 22 / pnpm 9 に固定しています。

### セットアップ

タスクは `just` にまとめてあります。`just` だけを実行すると一覧が出ます。

```bash
cp .env.example .env # PowerShellでは Copy-Item .env.example .env
just setup      # 依存関係を入れる (uv sync --all-packages + pnpm install)
just up         # Fuseki + PostgreSQL + Azurite、Blob container/vector/pg_trgm初期化
just migrate    # .envを明示的に読み、DBテーブルを作る
just up-vkg     # ローカルの vkg schema を題材データで作り直し、Ontopを起動
just gen-api    # Webの生成型を準備
just dev-api    # Core API を起動
just dev-mcp    # MCP サーバーを起動 (別ターミナル)
just dev-web    # Web を起動 (別ターミナル)
```

最初の `.env` の作成だけがOSで異なります。PowerShellは
`Copy-Item .env.example .env`、Git Bash/Linuxは `cp .env.example .env` を
使います。それ以降の `just setup` / `up` / `migrate` / `up-vkg` / `check-all`
は共通です。Git Bashでも `MSYS_NO_PATHCONV` をシェル全体へ export しないで
ください。recipe がDockerへ渡すパスを管理します。

### 検証

Pull Requestでは該当領域の検査を実行し、コマンドと結果を記録してください。
`just check` は高速な Python 検査、`just check-all` は Azure provisioning・
資格情報操作・破壊的 cleanup を除くローカル検査の統合入口です。CI は同じ検査を
ジョブごとに並列実行します。完全なコマンドとOSごとの注意は
[AGENTS.md](AGENTS.md#検証)、実際のジョブは [CI](.github/workflows/ci.yml) を
参照してください。

```bash
just check          # Python lint + strict型検査 + 単体テスト
just check-all      # 上記 + 型生成/Web/integration/全個別検査
just gen-api        # Webの型検査/ビルドの前に生成
pnpm --filter @ontology-accelerator/web build # Webの型検査・テスト・ビルド
just test-shell     # shellcheck・移植性・Fuseki・preprovision
just test-reasoner  # 推論器の実コンテナ検査
just test-vkg       # Ontop/PostgreSQLの実コンテナ検査
just check-licenses # Python + Node の依存ライセンス検査
just lint-infra     # Bicep の構文検査
```

`just check-all` は `just up` 済みのローカルサービス、Docker、jq、curl、uv、
pnpm、Azure CLI(Bicep buildのみ)を必要とします。Azure provisioning、実テナントの
認証・権限変更、`just clean` / `azd down --purge` は含みません。
pytestは同じDBに対して2プロセス同時に走らせないでください。
テストのfixtureがスキーマを再作成します。Dockerと公開ポートを事前に確認し、
サービス未起動をコードの失敗と混同しないでください。

| ローカル入口 | 含む検査 | 対応するCI job |
|---|---|---|
| `just check` | Ruff、format、strict mypy、非integration pytest | `python` |
| `just gen-api` + `just build-web` | API型生成、Web型検査、89 tests、Vite build | `web` |
| `just test-integration` | PostgreSQL/Azurite/Fusekiを使うpytest | `python` |
| `just test-shell` | shellcheck 0.11.0、移植性、preprovision、Fuseki loader | `shell` |
| `just test-reasoner` | ELKの実コンテナ検査 | `reasoner` |
| `just test-vkg` | Ontop/PostgreSQLの実コンテナ検査 | `vkg` |
| `just check-licenses` | Python/Node依存ライセンス | `python` |
| `just lint-infra` | Bicep build | `infra` |
| `just check-all` | 上記すべてを表の順序で実行 | 上記jobの集合 |

CIの `containers` job は配布イメージのbuild検査であり、ローカルの各実機検査が
必要なイメージをbuildするため `check-all` へ別重複では追加しません。

Azure実機確認は課金の事前承認が必要です。検証後は `azd down --purge` を完了させ、
RG・論理削除されたKey Vault等が消えたことを副作用で確認します。

コードを整形する場合は `just fmt` を使ってください。

API のスキーマを変更した場合は、`just gen-api` で `openapi.json` と Web 用の TypeScript 型を再生成してください(詳細は [ADR-0004](docs/adr/0004-api-contract-strategy.md))。

## コーディング規約

- **Python**: 3.12、`ruff` でフォーマットと lint、`mypy --strict` を通すこと。型注釈は必須です
- **TypeScript**: Web 用の型は `openapi.json` から `openapi-typescript` で生成します(詳細は [ADR-0004](docs/adr/0004-api-contract-strategy.md))。**生成物を手で編集しないでください**
- **改行コード**: リポジトリは LF に統一しています(`.gitattributes` で `* text=auto eol=lf`)。Windows で開発する場合もコミットは LF になります
- **シェル依存を避ける**: タスクは `just` と Python で記述してください。Windows と Linux CI の双方で動く必要があります
- **W3C 標準から逸脱しない**: RDF / OWL / SPARQL / SHACL / R2RML の標準的な使い方を優先します。独自のグラフ表現やクエリ言語を導入する提案は、ADR での合意が必要です
- **ストア実装に依存しない**: アプリコードは SPARQL 1.1 Protocol を境界として書いてください。Fuseki 固有の機能に依存するコードは `packages/core` の SPARQL クライアント層に閉じ込めます

## セキュリティ

脆弱性を発見した場合は、**公開 Issue や Pull Request を作成せず**、[SECURITY.md](SECURITY.md) の手順に従って非公開で報告してください。

特に SPARQL エンドポイントの取り扱いには注意が必要です。`SERVICE` 句による SSRF、クエリ DoS、名前空間の越境といった攻撃面については SECURITY.md に整理しています。この領域に触れる変更は、対策が回帰していないことを確認してください。

## コミットと Pull Request

- 1 つの Pull Request は 1 つの関心事に絞ってください
- コミットメッセージは変更の理由がわかる形にし、タスクID（例: `P3-11`）を含めてください
- Pull Request の説明には、対応する Issue、どの Phase の作業か、検証方法を記載してください
- ドキュメントのみの変更でも歓迎します
- PRテンプレートの受け入れ条件と検証の証拠欄を埋め、完全対応だけ `Closes` を使ってください

## 質問

不明な点は Issue で気軽に質問してください。設計の意図がドキュメントから読み取れなかった場合、それはドキュメント側の不足です。指摘していただけると助かります。
