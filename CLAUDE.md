# このリポジトリで作業するときに最初に読むこと

企業のビジネスオントロジーを W3C 標準のナレッジグラフとして管理し、MCP 経由で AI エージェントに提供する Azure ネイティブな OSS。Apache-2.0。

## 作業を始める前に

1. **[`docs/backlog.md`](docs/backlog.md) を読む。** 何が残っていて、次に何をすべきかの単一の正本。「今すぐ着手すべきもの」節から見る
2. **[`docs/roadmap.md`](docs/roadmap.md) を読む。** Phase の区切りと、各 Phase の完了条件
3. 触る領域に関係する **[`docs/adr/`](docs/adr/)** を読む。ADR は「却下した代替案とその理由」を残している。**同じ議論を繰り返さないため**にある

## 作業を終えるときに必ずやること

**バックログの状態を、コードと同じコミットで更新する。** これが守られないとセッションをまたいだ引き継ぎが壊れる。

- タスクの状態を変えたら `docs/backlog.md` を更新する
- タスク ID（`P1-C1` など）をコミットメッセージに含める
- 新しく見つけた問題は、直さない場合でも**バックログに追記する**。「出典」に何で見つけたかを書く
- 設計の方針を変えたら ADR を書く。既存 ADR に影響するなら、その ADR に補記を追加する

**ID は変えない。** ADR やコミットから参照されている。

## 壊してはいけない設計の不変条件

変更がこれらに触るなら、ADR を読んでから進めること。

1. **トリプルストアは再構築可能な射影であり正本ではない。** 正本は PostgreSQL（メタデータ・監査）と Blob（バージョン付き TTL）（[ADR-0002](docs/adr/0002-triple-store-as-rebuildable-projection.md)）
2. **書き込み順序は Blob → PostgreSQL → Fuseki で不可逆。** 耐久化の順序も含む。`put_graph` の前に commit する
3. **射影の失敗は正本への書き込みを失敗させない。** `put_graph` が失敗しても呼び出し元には成功を返し、`projected_at IS NULL` として `reconcile` が回収する。**ここを例外伝播に変えてはいけない**（過去に一度変えて差し戻した）
4. **抽象の契約**: `SparqlStore` の失敗は必ず `SparqlStoreError`、`OntologyBlobStore` の失敗は必ず `BlobStoreError` として表面化する。呼び出し側がトランザクション境界の判断に使う
5. **名前空間名はセキュリティ境界。** Fuseki のデータセット名・Blob パス・グラフ IRI に使う。外部入力は必ず `validate_namespace_name` を通す
6. **DSN にパスワードを埋め込まない。** Entra トークンは期限切れするため `connect_args["password"]` に callable を渡して接続ごとに評価させる
7. **オントロジーは不変リビジョン。** 公開済みの版を書き換えない。削除もしない（[ADR-0006](docs/adr/0006-ontology-versioning-and-audit.md)）
8. **オントロジーは縮められなければならない。** 廃止を追加と同格に扱う。IRI を削除も再利用もしない（[ADR-0009](docs/adr/0009-ontology-operations.md)）
9. `AUTH_MODE=disabled` はローカル開発専用。デプロイ環境で使ってはならない

## 開発環境

```bash
cp .env.example .env     # ローカル開発用の設定
just up                  # Fuseki + PostgreSQL + Azurite + Blob コンテナ作成
just migrate             # テーブル作成
just dev-api             # Core API 起動
```

### 既知の罠

- **ポート 3030 が別プロジェクトと衝突する場合がある。** `FUSEKI_PORT=3131` を環境変数で指定する。テストも同じ変数を読む（`POSTGRES_PORT` / `AZURITE_PORT` も同様）
- **`just up` は Azurite に Blob コンテナを作る。** これを飛ばすと publish と削除が `ContainerNotFound` で失敗する。名前空間の作成と SPARQL 参照は Blob を触らないため動いてしまい、原因が分かりにくい
- **`git commit` はインデックス全体をコミットする。** `git add <パス>` で絞っても、他に staged なものがあれば混ざる。**コミット前に `git diff --cached --name-only` で確認する**
- **PowerShell の `>` は UTF-16LE で書き出す。** ファイル出力はシェルに任せず、生成側の言語で `encoding='utf-8'` を明示する
- **docker のボリュームを `/lib` にマウントしてはいけない。** Alpine の `/lib` は musl libc 等の
  システム共有ライブラリの場所で、そこを自分のディレクトリで覆うと `/bin/sh` 自身が動かなくなり
  `exec /bin/sh: no such file or directory` で全滅する。`/work` などに置くこと。
  `jq` が必要なシェルテストを docker で回すときに踏む
- **Git Bash は `/` で始まる引数を Windows パスに変換する。** `az` に ARM のリソース ID を渡すと壊れる。`export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'` を先に置くか、リソース ID ではなく名前を渡す

## 検証

変更をコミットする前に全部通すこと。

```bash
uv run pytest                                  # 105 件
uv run ruff check . && uv run ruff format --check .
uv run mypy packages
sh containers/fuseki/lib/validate.test.sh      # シェル側の検証関数
az bicep build --file infra/main.bicep --stdout > /dev/null
```

**Azure へのデプロイは費用が発生する。** `azd up` は約 11 分、`azd down --purge` は約 24 分。実施前に確認を取り、**検証後は必ず `azd down --purge`** する（Key Vault の purge まで確認する。論理削除が残ると同名で再デプロイできない）。

## 新しいテストを書くときの規律

**修正前のコードでそのテストが落ちることを確認してから直す。** 通るだけのテストを書かないため。このリポジトリでは実際にこの手順で複数の見せかけの修正を防いでいる。
