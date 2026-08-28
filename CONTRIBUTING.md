# コントリビューションガイド

Ontology Accelerator for Azure への貢献に興味を持っていただきありがとうございます。

> **現在のステータス**: 本プロジェクトは **Phase 0(スキャフォールドのみ / 未稼働)** です。動作するアプリケーションはまだ存在しません。そのため現時点で最も価値のある貢献は、**設計そのものへのレビューと議論**です。詳しくは [README](README.md) をご覧ください。

## 行動規範

本プロジェクトへの参加者は [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)(Contributor Covenant 2.1)に従うことが求められます。

## ライセンスと貢献の取り扱い

本プロジェクトは [Apache License 2.0](LICENSE) で公開されています。Apache-2.0 §5 の定めにより、**あなたが意図的に投稿した貢献は、特段の申し出がない限り Apache-2.0 の条件の下で提供されたものとみなされます**。CLA(Contributor License Agreement)への署名は求めていません。

他のプロジェクトからコード・ドキュメント・データを持ち込む場合は、必ず Pull Request の説明に**出典とそのライセンス**を明記してください。Apache-2.0 と非互換なライセンス(GPL / LGPL 等)の成果物は、本体に取り込むことができません。判断に迷う場合は、実装前に Issue で相談してください。

## 貢献の種類ごとの進め方

### 設計へのフィードバック(Phase 0 で最も歓迎する貢献)

[`docs/architecture.md`](docs/architecture.md) と [`docs/adr/`](docs/adr/) を読み、疑問・反論・見落としを Issue として提起してください。特に以下は積極的に議論したい論点です。

- グラフ永続化の設計(トリプルストアを再構築可能な射影として扱う判断 / [ADR-0002](docs/adr/0002-triple-store-as-rebuildable-projection.md))
- トリプルストアの選定(Fuseki を既定とする判断 / [ADR-0001](docs/adr/0001-rdf-store-selection.md))
- コスト試算の妥当性([`docs/cost-estimate.md`](docs/cost-estimate.md))

### バグ報告

Phase 0 の時点では「動かない」ことは仕様です。ドキュメントの誤り、リンク切れ、IaC の構文エラー、CI の不具合などは Issue で報告してください。

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

> Phase 0 では以下のコマンドは動作しません。Phase 1 以降の想定手順です。

### 前提ツール

Azure CLI / Azure Developer CLI (`azd`) / Docker / uv / pnpm / Node.js 22 / Python 3.12 / `just`

Windows 環境の場合、リポジトリに同梱の [Dev Container](.devcontainer/) の利用を推奨します。

### セットアップ

タスクは `just` にまとめてあります。`just` だけを実行すると一覧が出ます。

```bash
just setup      # 依存関係を入れる (uv sync --all-packages + pnpm install)
just up         # Fuseki + PostgreSQL をローカル起動 (docker compose up -d --build)
just dev-api    # Core API を起動
just dev-mcp    # MCP サーバーを起動 (別ターミナル)
just dev-web    # Web を起動 (別ターミナル)
```

### 検証

Pull Request を出す前に、以下がすべて通ることを確認してください。CI(`.github/workflows/ci.yml`)でも同じ内容を検証します。

```bash
just check          # lint + typecheck + test (ruff / mypy strict / pytest)
just typecheck-web  # Web の型検査
just lint-infra     # Bicep のビルド検証
```

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
- コミットメッセージは変更の理由がわかる形で書いてください
- Pull Request の説明には、対応する Issue、どの Phase の作業か、検証方法を記載してください
- ドキュメントのみの変更でも歓迎します

## 質問

不明な点は Issue で気軽に質問してください。設計の意図がドキュメントから読み取れなかった場合、それはドキュメント側の不足です。指摘していただけると助かります。
