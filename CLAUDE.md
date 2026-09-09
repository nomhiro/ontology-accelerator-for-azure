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
10. **`projected_at` は書き込み経路の知識であり、ストアの現在の状態ではない。** ストアは再構築可能な派生物なので、PostgreSQL の列から「ストアが今それを保持している」ことは主張できない。それに答えられるのはストア自身だけである（[ADR-0013](docs/adr/0013-reconcile-repairs-observed-divergence.md)）
11. **権限の既定は「拒否」。暗黙のフォールバックを作らない。** ロール付与が 1 件も無い名前空間は「誰も権限を持たない」として扱う。「付与が無ければ全員に許可」は**「強制していない」を「強制している」と誤認させる**ため、この製品では最も避けたい形である。既存デプロイの移行はマイグレーションで明示的に行う（[ADR-0014](docs/adr/0014-namespace-rbac.md)）
12. **`platform-admin` は名前空間の権限を飛び越えるが、四眼原則は飛び越えない。** 管理者が自分の提案を自分で承認できてしまうと、四眼原則が「管理者以外への制約」に成り下がり、規制対応の文脈で意味を失う（ADR-0014 決定5）

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
- **`just clean` は PostgreSQL のボリュームごと消す。** 消した後は `just migrate` をやり直す必要がある。さらに `alembic` を素で叩くときは `.env` を読まないので、`POSTGRES_*` を環境変数で明示する（`just migrate` は `--env-file` を使っている）。読み込まれないと既定値で接続を試み、`InvalidPasswordError` になる
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
uv run pytest                                  # 243 件(件数は増える。減っていたら何かを壊している)
uv run ruff check . && uv run ruff format --check .
uv run mypy packages
sh containers/fuseki/lib/validate.test.sh      # シェル側の検証関数
sh containers/fuseki/load-snapshot.test.sh     # ローダの制御フロー
sh scripts/lint-shell.sh                       # シェルの移植性(素の python 等)
# shellcheck は CI と同じバージョンを使う(apt 版 0.9.0 と指摘が違うため固定)
docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:v0.11.0 \
  scripts/*.sh containers/fuseki/*.sh containers/fuseki/lib/*.sh
az bicep build --file infra/main.bicep --stdout > /dev/null
```

**Azure へのデプロイは費用が発生する。** `azd up` は約 11 分、`azd down --purge` は約 24 分。実施前に確認を取り、**検証後は必ず `azd down --purge`** する。

**`azd down --purge` を中断してはいけない。** Key Vault の purge は**リソースグループの削除が完了した後**に実行されるため、途中で止めると論理削除が残る。論理削除が残ると同名で再デプロイできない。中断してしまった場合の復旧:

```bash
az keyvault list-deleted --query "[?contains(name,'<suffix>')].name" -o tsv
az keyvault purge --name <name> --location japaneast
```

リソースグループの削除自体は ARM のサーバ側で継続するので、コマンドを止めても完了する。止まるのは purge だけである。

**中断は自分の操作だけで起きるとは限らない。** 実行環境のメモリ不足で背景プロセスが殺されて実際に中断した（2026-09-09）。そのため **`azd down --purge` の後は必ず副作用で確認する**こと。終了コードを見るだけでは足りない。

```bash
az group exists -g <rg>                    # false であること
az keyvault list-deleted --query "[].name" -o tsv   # 対象が消えていること
```

**長時間かかるコマンドを待つのに、スリープを入れない密ループを書いてはいけない。**`until cond; do :; done` は CPU とメモリを食い潰し、**待っている当の背景プロセスごと殺される**（実際に `azd down --purge` を巻き込んで止めた）。`sleep 45` などを必ず挟む。

**MCP SDK は `ToolError` 以外の例外のメッセージを隠す。** ツールの中で
`ValueError` を投げると、エージェントに届くのは `Error executing tool <name>`
だけで理由が失われる。意図的な拒否は必ず
`mcp.server.mcpserver.exceptions.ToolError` で投げること。

**シェル側のテストは `jq` を要求する。** `containers/fuseki/` の 2 本は load-snapshot.sh 自身がマニフェストの解析に jq を使うため、jq が無い環境では実行できない。Windows には既定で無いので docker 経由で回す:

```bash
docker run --rm -v "$(pwd):/w" -w /w alpine:3.20 sh -c \
  'apk add --no-cache jq >/dev/null && sh containers/fuseki/load-snapshot.test.sh'
```

**ルータのハンドラ名が、同じモジュールで import している関数を上書きすることがある。** エンドポイント関数を `validate_version` と命名したところ、入口検証に使っている `ontology_core.graphs.validate_version` を隠してしまい、**他のハンドラの検証が黙って効かなくなった**。ハンドラ名は `validate_version_shacl` のように用途を付けて衝突を避ける（回帰テストあり）。

**SQLAlchemy の `session.execute()` の戻り値に `rowcount` は無い（mypy strict）。** `rowcount` は `CursorResult` にしか無く、`execute()` の宣言型はそれより広い `Result[Any]` である。DELETE の件数が欲しいときは `cast` で型を潰すのではなく、**存在確認してから削除する**（1 クエリ増えるが意図が読める。`RoleRepository.revoke` がこの形）。

**PostgreSQL の `now()` はトランザクション開始時刻を返す。** `server_default=now()` の列は、同一トランザクション内で挿入した複数行が**同じ値になる**。時系列で並べたいときは主キーを第二キーに加える（`audit_events` の決定記録の並び順で実際に必要になった）。

**シェルスクリプトで素の `python` を呼んではいけない。** 多くの現代的な Linux には `python` が無く `python3` しかない（Python 3 が既定になった時点で各ディストリが無印の提供をやめた）。実測で Azure Linux 3.0 には無い。**`uv run python` を使う**（このリポジトリのスクリプトは既に uv に依存しているため、前提を増やさない）。`scripts/lint-shell.sh` が機械的に検査する。

**コメント行を静的解析ツールの名前だけで始めてはいけない。** `#` の直後にツール名が来ると、ツール自身がディレクティブ指定として解釈して SC1072 / SC1073 で失敗する。説明したいときは「静的解析ツール」と書くか、行頭に別の語を置く（2 回踏んだ）。

**Windows の Azure CLI のトークンキャッシュは Linux から使えない。** `~/.azure/msal_token_cache.bin` は DPAPI 暗号化（先頭が `01 00 00 00 D0 8C 9D DF`）で、Windows ユーザーに紐づく。`~/.azure` をコンテナへ複製すると `az account show`（ローカルのメタデータだけ）は通るのに、**トークンを要求するコマンドはすべて失敗する**ので「az は動いている」と誤解しやすい。Linux 側で az を使うには、そちら側で `az login`（対話的）が別途必要。

**`azd up` は docker が動いていないと即座に失敗する。** Docker Desktop が落ちていると `error checking for external tool Docker` で終わる（**課金は始まらない**）。azd 自身が `remoteBuild: true` を提案してくる（ACR 側でビルドする。ローカル docker が不要になる）。

**`azd up` を `> log 2>&1` で包んで終了コードを見るときは、azd 自身の `$?` を取ること。** `azd up ... > log; echo $?` のように後続コマンドを挟むと、報告される終了コードは複合コマンド全体のものになり、**azd の失敗が成功に見える**（実際に一度誤読した）。

**`azd provision` は Entra 管理者の登録について冪等でない。** 既存環境に再 provision すると `AadAuthPrincipalCreationFailed: role "..." already exists` で失敗する。同じ環境に作り直すのではなく `azd env new` で別環境を使うか、管理者登録を先に削除する。

**ARM の output 名は camelCase で返る。** `SERVICE_API_URI` は `servicE_API_URI` として返ってくる。復元するときは先頭 1 文字だけ大文字化するのではなく、キー全体を `upper()` する（`ServicE_API_URI` のような中途半端な名前を作らないため）。

**Windows の環境変数は大文字小文字を区別しない。** `.azure/<env>/.env` に大文字小文字だけ違う残骸（空の値）があると、正しい変数を上書きして**フックから見えなくなる**。フックが「必要な環境変数が無い」と言い出したら、`.env` の重複を疑う。

**`az postgres flexible-server firewall-rule` の引数は紛らわしい。** サーバは `--server-name` / `-s`、**規則名は `--name` / `-n`**。`--rule-name` は存在しない（`--name` にサーバ名を渡すと「`--server-name` が必要」と言われ、`--rule-name` を渡すと「認識されない引数」になる）。

**新しい azd 環境を作ると `AZURE_SUBSCRIPTION_ID` は引き継がれない。** 環境ごとに独立しているため、`azd env new` の後に `azd env set AZURE_SUBSCRIPTION_ID <id>` が必要（`azd up` が `prompt required` で止まる）。

## 新しいテストを書くときの規律

**修正前のコードでそのテストが落ちることを確認してから直す。** 通るだけのテストを書かないため。このリポジトリでは実際にこの手順で複数の見せかけの修正を防いでいる。
