# ontology-api

Core API。FastAPI / Pydantic のスキーマファーストで、`openapi.json` から Web 用の
TypeScript 型を生成する(`docs/adr/0004-api-contract-strategy.md`)。

## Phase 0 の実装状況

| 領域 | 状態 |
| --- | --- |
| ヘルスチェック | 実装済み |
| 名前空間の CRUD | ルート定義とスキーマのみ。永続化はメモリ上のスタブ(Phase 1 で PostgreSQL に置換) |
| SPARQL の仲介 | 読み取り専用の経路を実装。ガード(更新拒否・`SERVICE` 拒否)を適用 |
| Entra ID 認証 | 依存関係として配線済み。名前空間ごとの認可強制は Phase 2 |
| Scan / Model | 未実装(Phase 2) |

## ローカルでの起動

```bash
just dev-api      # uvicorn --reload
```

`AUTH_MODE=disabled` を設定するとトークン検証を省略できる。**ローカル専用**。

## 起動時の環境変数

`packages/core/src/ontology_core/config.py` が正本。
