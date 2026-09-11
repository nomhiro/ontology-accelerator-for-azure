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
8. **オントロジーは縮められなければならない。** 廃止を追加と同格に扱う。IRI を削除も再利用もしない（[ADR-0009](docs/adr/0009-ontology-operations.md)）。**現行版にあった用語を消した版は `approve` が 422 で拒否する**（[ADR-0017](docs/adr/0017-deprecation-lifecycle.md) 決定2）。縮めるときは `owl:deprecated true` + 後継（`dcterms:isReplacedBy`）か理由を書いて残す
9. `AUTH_MODE=disabled` はローカル開発専用。デプロイ環境で使ってはならない
10. **`projected_at` は書き込み経路の知識であり、ストアの現在の状態ではない。** ストアは再構築可能な派生物なので、PostgreSQL の列から「ストアが今それを保持している」ことは主張できない。それに答えられるのはストア自身だけである（[ADR-0013](docs/adr/0013-reconcile-repairs-observed-divergence.md)）
11. **権限の既定は「拒否」。暗黙のフォールバックを作らない。** ロール付与が 1 件も無い名前空間は「誰も権限を持たない」として扱う。「付与が無ければ全員に許可」は**「強制していない」を「強制している」と誤認させる**ため、この製品では最も避けたい形である。既存デプロイの移行はマイグレーションで明示的に行う（[ADR-0014](docs/adr/0014-namespace-rbac.md)）
12. **`platform-admin` は名前空間の権限を飛び越えるが、四眼原則は飛び越えない。** 管理者が自分の提案を自分で承認できてしまうと、四眼原則が「管理者以外への制約」に成り下がり、規制対応の文脈で意味を失う（ADR-0014 決定5）

13. **権限（何ができるか）と責任者（誰が説明責任を負うか）を混ぜない。** 名前空間単位の責任者は `namespace_roles` の `owner`、用語単位は `term_owners` である（[ADR-0015](docs/adr/0015-term-owners.md)）。**ルーティングにはフォールバックを作るが、権限には作らない** — 安全側の向きが逆である（権限は「無いなら拒否」、ルーティングは「無いなら上位に回す」。誰にも届かない問い合わせは放置され、放置されたことも分からない）。ただしフォールバックしたことは `source` で必ず見せる
14. **受け入れ基準を、その基準で審査される側が書き換えられてはならない。** 想定質問の集合は**名前空間ごとの不変改訂**であり、版ごとに持たない（版ごとにすると新しい版が自分の合格条件を自分で書き換えられる。[ADR-0022](docs/adr/0022-competency-question-sets.md) 決定2）。書き込みは `owner`、`approve` は `maintainer` 以上に分けてある（決定6）。**ただしロールは階層なので `owner` は両方できる。** そこで `approve` が**「その版を書いた主体が、publish 後に基準を書き換えた」ことを 422 で拒否する**（[ADR-0029](docs/adr/0029-criteria-authorship.md)、`P2B-16`）。この検査は `require_two_person_approval` に従う（**スイッチを増やさない** — 片方だけ切って「四眼原則を有効にしたつもり」になれる状態を作らないため）。**「基準を先に定めた責任者が他人の版を承認する」は止めない** — それは統制のあるべき姿である。止まったときの対処は**別の主体が基準を改訂し直す**こと（内容が同じでもよい。改訂は不変で `reason` が必須なので「確認した」行為が記録される）
15. **承認時の検査でトリプルストアに問い合わせてはならない。** その時点でその版はまだ射影されていない。SHACL・廃止・想定質問はいずれも**正本の TTL** に対して評価する（ADR-0022 決定3）。ストアに依存させると、不変条件3 が守ろうとしているものの逆向きになる（射影の可用性が正本の書き込みを止める）
16. **領域をまたぐ用語を論理的帰結を持つ述語で結んではならない。** マッピングに使える述語は SKOS の 5 つ（`exactMatch` / `closeMatch` / `broadMatch` / `narrowMatch` / `relatedMatch`）だけである（[ADR-0023](docs/adr/0023-cross-domain-mappings.md) 決定1）。**`owl:equivalentClass` で結ぶと両方のクラスが充足不能になる**（ELK で実測。ADR-0009 決定8 は候補に挙げていたが却下した）。**相違は消さない** — 逆向きのマッピングを自動生成せず（決定3）、両側の述語が食い違ったら両方残して `disputed` で見せる（決定4）
17. **名前空間を削除する経路と Blob に `.ttl` を書く経路は、同じ行ロックを取る。** `NamespaceRepository.get_locked`(`SELECT ... FOR UPDATE`)を、削除は**Blob の検査より前**に、`publish` は**Blob への書き込みより前**に呼ぶ（[ADR-0024](docs/adr/0024-namespace-delete-locking.md)）。**`DELETE` 文が暗黙に取る行ロックでは遅すぎる** — その時点では既に Blob に TTL が書かれている。ローダは PostgreSQL を見ず Blob だけを見て再構築するので、**Blob に TTL があって PostgreSQL に名前空間が無い状態は「削除したはずの名前空間の復活」を意味する**。「後から Blob を再検査する」では窓は閉じない

## 開発環境

```bash
cp .env.example .env     # ローカル開発用の設定
just up                  # Fuseki + PostgreSQL + Azurite + Blob コンテナ作成
just migrate             # テーブル作成
just dev-api             # Core API 起動
```

### 既知の罠

- **ポート 3030 が別プロジェクトと衝突する場合がある。** `FUSEKI_PORT=3131` を環境変数で指定する。`docker compose` / `pytest` / `Settings`（つまり `scripts/check-questions.py` も）がすべて同じ変数を読む（`POSTGRES_PORT` / `AZURITE_PORT` も同様）。
  **`SPARQL_QUERY_ENDPOINT` 等を明示した場合はそちらが勝つ**（デプロイ環境では Bicep が内部 ingress の FQDN を注入するため。`P2A-13`）
- **`just up` は Azurite に Blob コンテナを作る。** これを飛ばすと publish と削除が `ContainerNotFound` で失敗する。名前空間の作成と SPARQL 参照は Blob を触らないため動いてしまい、原因が分かりにくい
- **`just clean` は PostgreSQL のボリュームごと消す。** 消した後は `just migrate` をやり直す必要がある。さらに `alembic` を素で叩くときは `.env` を読まないので、`POSTGRES_*` を環境変数で明示する（`just migrate` は `--env-file` を使っている）。読み込まれないと既定値で接続を試み、`InvalidPasswordError` になる
- **integration テストがトランザクションを開いたまま失敗すると、スイート全体が固まる。**
  次のテストの `drop_all` の `DROP TABLE` が**無期限に待つ**(実測。行ロックの変異
  テストで踏んだ)。`packages/api/tests/conftest.py` は `SET lock_timeout = '15s'` を
  置き、セッションのフィクスチャで必ず `rollback` するようにしてある。
  **タスクやロックを残すテストを書くときは `try` / `finally` で必ず片付けること**
  (`test_delete_publish_race.py` の `_cleanup` がその形)
- **`git checkout -- <ディレクトリ>` は未コミットの変更を巻き戻す。** 変異テストの
  復元に使うと、**変異と無関係な実装まで消える**。新規ファイルは untracked なので
  残り、既存ファイルの変更だけが消えるため**被害が分かりにくい**(実際に 1 度、
  実装の半分を消した)。**1 ファイル単位で `cp` して戻す**こと
- **`git commit` はインデックス全体をコミットする。** `git add <パス>` で絞っても、他に staged なものがあれば混ざる。**コミット前に `git diff --cached --name-only` で確認する**
- **PowerShell の `>` は UTF-16LE で書き出す。** ファイル出力はシェルに任せず、生成側の言語で `encoding='utf-8'` を明示する
- **docker のボリュームを `/lib` にマウントしてはいけない。** Alpine の `/lib` は musl libc 等の
  システム共有ライブラリの場所で、そこを自分のディレクトリで覆うと `/bin/sh` 自身が動かなくなり
  `exec /bin/sh: no such file or directory` で全滅する。`/work` などに置くこと。
  `jq` が必要なシェルテストを docker で回すときに踏む
- **`MSYS2_ARG_CONV_EXCL='*'` を `export` してはいけない。** docker のためにこれを
  シェル全体へ広げると、**Windows のバイナリに渡す POSIX パスも変換されなくなる**。
  `scripts/preprovision.test.sh` が `指定されたパスが見つかりません。 (os error 3)` で
  5 件落ちた(実測。uv がパスを解釈できない)。**docker のコマンドの前置きにして
  1 コマンドに閉じる**こと(`MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' docker run ...`)。
  スクリプトの中で `export` してよいのは、そのスクリプトが Windows バイナリへ
  絶対パスを渡さない場合に限る(`containers/reasoner/*.sh` は相対パスしか渡さない)
- **Git Bash は `/` で始まる引数を Windows パスに変換する。** `az` に ARM のリソース ID を渡すと壊れる。**docker の `-w /work` も壊れる**(`W:/` になって拒否される)。`MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'` をそのコマンドの**前置き**にするか(**`export` はしない** — 上の罠)、リソース ID ではなく名前を渡す。
  抑止すれば `-v "$PWD:/mnt"` の `/c/...` 形式は Docker Desktop が受け付けるが、**`docker build` のビルドコンテキストは受け付けない**(`path not found`)。スクリプトの中では `cygpath -m` で `C:/...` に直す(`containers/reasoner/*.sh` がその形)

## 検証

変更をコミットする前に全部通すこと。

```bash
uv run pytest                                  # 883 件(件数は増える。減っていたら何かを壊している)
uv run ruff check . && uv run ruff format --check .
uv run mypy packages
sh containers/fuseki/lib/validate.test.sh      # シェル側の検証関数
sh containers/fuseki/load-snapshot.test.sh     # ローダの制御フロー
sh scripts/lint-shell.sh                       # シェルの移植性(素の python 等)
sh scripts/preprovision.test.sh                # provision を止めるゲート(要: uv)
sh containers/reasoner/reasoner-check.test.sh  # OWL 推論器の検査(要: docker、uv。約 2 分)
sh scripts/check-reasoning.sh samples          # 同梱サンプルの論理的整合性(要: docker、uv)
# **Git Bash では docker の前に変換抑止が必要。** 無いと `-w /mnt` が
# `C:/Program Files/Git/mnt` に変換されて docker が拒否する(実測)。
# **`export` ではなく前置きにする** — 広げると uv が壊れる(下の罠を参照)。
# shellcheck は CI と同じバージョンを使う(apt 版 0.9.0 と指摘が違うため固定)
MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:v0.11.0 \
  scripts/*.sh containers/fuseki/*.sh containers/fuseki/lib/*.sh containers/reasoner/*.sh
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
# Git Bash では変換抑止が必要(無いと `-w /w` が `W:/` になって拒否される)。
MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
docker run --rm -v "$(pwd):/w" -w /w alpine:3.20 sh -c \
  'apk add --no-cache jq >/dev/null && sh containers/fuseki/load-snapshot.test.sh'
```

**ルータのハンドラ名が、同じモジュールで import している関数を上書きすることがある。** エンドポイント関数を `validate_version` と命名したところ、入口検証に使っている `ontology_core.graphs.validate_version` を隠してしまい、**他のハンドラの検証が黙って効かなくなった**。ハンドラ名は `validate_version_shacl` のように用途を付けて衝突を避ける（回帰テストあり）。

**SQLAlchemy の `session.execute()` の戻り値に `rowcount` は無い（mypy strict）。** `rowcount` は `CursorResult` にしか無く、`execute()` の宣言型はそれより広い `Result[Any]` である。DELETE の件数が欲しいときは `cast` で型を潰すのではなく、**存在確認してから削除する**（1 クエリ増えるが意図が読める。`RoleRepository.revoke` がこの形）。

**`io.open(path, "w", ...)` は引数を検証する前にファイルを切り詰める。** 不正な `newline` を渡すと `ValueError` になるが、**その時点でファイルは既に 0 バイトになっている**（実際に既存のテストファイルを消した。コミット済みだったので復元できた）。生成スクリプトでファイルを書き換えるときは、**開く前に引数を確定させる**か、一時ファイルに書いて差し替える。

**FastAPI の `Query(...)` を既定値の位置に書くと、ハンドラを直接呼ぶテストで `Query` オブジェクトが値として流れ込む。** `limit: int = Query(default=50)` の既定値は `50` ではなく `Query` インスタンスである。FastAPI 経由なら解決されるので、**HTTP で叩くテストだけでは気づけない**。このリポジトリのルータのテストはハンドラを直接呼ぶため必ず踏む。`limit: Annotated[int, Query(...)] = 50` と書けば既定値は素の値になる。

**rdflib の空白ノードの正規化は空白ノードの数だけで決まり、急激に伸びる。** トリプル総数はほとんど効かない。実測で 2 グラフの差分が空白ノード 300 個で 2〜4.5 秒、500 個で 8 秒、1,000 個で 42 秒（`graph_diff`）。SHACL の property shape は 1 つずつ空白ノードを作るので実用規模で数千に達しうる。**上限（`ontology_core.diff.MAX_BLANK_NODES`）を超えたら計算せず、「計算できなかった」と返す**（[ADR-0016](docs/adr/0016-semantic-diff.md) 決定5）。なお `graph_diff` は引数を内部で `to_canonical_graph` に通すので、外から `to_isomorphic` を挟む必要は無い。

**`server_default` は挿入時にしか効かない。** `INSERT ... ON CONFLICT DO UPDATE` の更新側で列に触らないと、「最後に更新した時刻」が**最初の挿入時刻のまま止まる**。UPSERT で時刻を持つ列は `set_` に必ず含める（`term_access.last_accessed_at`）。**テストで `>=` を使うとこの不具合を見逃す** — 等号を許すと更新していない実装でも通る（変異テストで実際に見逃した）。

**テストファイルの基底名は 3 つのテストディレクトリ全体で一意にする。** `packages/*/tests/` に `__init__.py` が無いため、同じ基底名（`test_health.py` を core と api の両方に置く等）は pytest の収集時に `import file mismatch` で**全体が止まる**（1 ファイルの衝突で全テストが走らなくなる）。用途を名前に含める（`test_health_metrics.py` / `test_health_api.py`）。

**アプリの時計と PostgreSQL の時計は別物である。** `occurred_at` などの
`server_default=now()` の列はサーバ側の時計で入るので、`datetime.now(UTC)`(アプリ側)を
境界にして比較すると**間欠的に落ちる**(実測: 監査の期間絞り込みのテストが 3 回に 1 回
落ちた。docker の中と外で時計が一致しない)。**境界は DB から読み戻した時刻で作る。**

**PostgreSQL の `now()` はトランザクション開始時刻を返す。** `server_default=now()` の列は、同一トランザクション内で挿入した複数行が**同じ値になる**。時系列で並べたいときは主キーを第二キーに加える（`audit_events` の決定記録の並び順で実際に必要になった）。

**`scripts/*.py` で `print` を使ってはいけない**(`scripts/lint-shell.sh` が機械的に検査する)。理由は次のとおり。

**Python の標準出力は Windows では cp932 になる。** `print` に日本語を渡すと cp932 のバイト列が出る一方、周りのシェルスクリプトの `echo` はソースの UTF-8 をそのまま出すため、**同じログに 2 つのエンコーディングが混ざる**。azd のフックのログが読めなくなり、ログを機械的に検査するテストも通らない（実際に踏んだ）。cp932 に無い文字（絵文字・ダッシュ）があると `UnicodeEncodeError` で**スクリプトごと落ちる**。運用者に見せる出力は `ontology_core.console` の `say` / `warn` を使う。

**入力側も同じである。** `json.load(sys.stdin)` は cp932 として読むため、**日本語を含む JSON をパイプで渡すと壊れる**（`JSONDecodeError` になる。実測）。`json.loads(sys.stdin.buffer.read().decode("utf-8"))` と書く。シェルから `uv run python -c` に JSON を流すテストで必ず踏む。

**`set -e` の下では `cmd; rc=$?` が書けない。** `cmd` が非ゼロで終わった時点でスクリプトが終わり、`rc` を読む行に到達しない（実測で確認）。終了コードで分岐したいときは `rc=0; cmd || rc=$?` にする。**「2 なら止める、1 なら続行する」のような多値の分岐**を書くときに必ず踏む。

**`uv run --directory` に Git Bash のパス（`/c/...`）を渡してはいけない。** Windows の uv が「指定されたパスが見つかりません。 (os error 3)」で落ちる。**パスを渡すのではなく `cd` してから `uv run` する**（`scripts/check-reasoning.sh` がその形）。

**シェルスクリプトで素の `python` を呼んではいけない。** 多くの現代的な Linux には `python` が無く `python3` しかない（Python 3 が既定になった時点で各ディストリが無印の提供をやめた）。実測で Azure Linux 3.0 には無い。**`uv run python` を使う**（このリポジトリのスクリプトは既に uv に依存しているため、前提を増やさない）。`scripts/lint-shell.sh` が機械的に検査する。

**シェルの単一引用符で囲んだ埋め込み Python のコメントに、バッククォートを書いてはいけない。** shellcheck がコマンド置換と誤認して SC2016(`Expressions don't expand in single quotes`)を出し、**検査が落ちる**（shellcheck は info でも終了コード 1 を返す）。`uv run python -c '...'` の中で識別子を強調したいときは「」で囲む。

**コメント行を静的解析ツールの名前だけで始めてはいけない。** `#` の直後にツール名が来ると、ツール自身がディレクティブ指定として解釈して SC1072 / SC1073 で失敗する。説明したいときは「静的解析ツール」と書くか、行頭に別の語を置く（2 回踏んだ）。

**Windows では拡張子の無いスタブが `shutil.which` に拾われない。** `az` をスタブに差し替えてテストするとき、`PATH` の先頭に拡張子なしの `az` を置いても Python 側は `PATHEXT`（`.cmd` / `.exe`）しか見ないため**本物の `az` が呼ばれる**（実際に一度、意図せず実テナントへ読み取りを飛ばした）。sh から呼ぶスクリプトのテストでは拾われるが、Python から呼ぶ場合は `subprocess.run` 自体を差し替えるか `az.cmd` を置く。

**Windows の Azure CLI のトークンキャッシュは Linux から使えない。** `~/.azure/msal_token_cache.bin` は DPAPI 暗号化（先頭が `01 00 00 00 D0 8C 9D DF`）で、Windows ユーザーに紐づく。`~/.azure` をコンテナへ複製すると `az account show`（ローカルのメタデータだけ）は通るのに、**トークンを要求するコマンドはすべて失敗する**ので「az は動いている」と誤解しやすい。Linux 側で az を使うには、そちら側で `az login`（対話的）が別途必要。

**`azd up` は docker が動いていないと即座に失敗する。** Docker Desktop が落ちていると `error checking for external tool Docker` で終わる（**課金は始まらない**）。azd 自身が `remoteBuild: true` を提案してくる（ACR 側でビルドする。ローカル docker が不要になる）。

**`azd up` を `> log 2>&1` で包んで終了コードを見るときは、azd 自身の `$?` を取ること。** `azd up ... > log; echo $?` のように後続コマンドを挟むと、報告される終了コードは複合コマンド全体のものになり、**azd の失敗が成功に見える**（実際に一度誤読した）。

**`azd provision` は Entra 管理者の登録について冪等でない。** 既存環境に再 provision すると `AadAuthPrincipalCreationFailed: role "..." already exists` で失敗する。同じ環境に作り直すのではなく `azd env new` で別環境を使うか、管理者登録を先に削除する。

**Bicep のパラメータは `string` で宣言する（`int` にしない）。** azd の `main.parameters.json` の置換（`${SUPERSEDED_RETAIN=0}`）は**文字列**を渡すため、`int` で宣言すると ARM が型エラーで落ちる。既存のパラメータが全て `string` なのはこの理由である（`fusekiCpu` が `'0.5'` なのも同じ）。値の検証はアプリ側（`Settings`）で行う。

**ARM の output 名は camelCase で返る。** `SERVICE_API_URI` は `servicE_API_URI` として返ってくる。復元するときは先頭 1 文字だけ大文字化するのではなく、キー全体を `upper()` する（`ServicE_API_URI` のような中途半端な名前を作らないため）。

**Windows の環境変数は大文字小文字を区別しない。** `.azure/<env>/.env` に大文字小文字だけ違う残骸（空の値）があると、正しい変数を上書きして**フックから見えなくなる**。フックが「必要な環境変数が無い」と言い出したら、`.env` の重複を疑う。

**`az postgres flexible-server firewall-rule` の引数は紛らわしい。** サーバは `--server-name` / `-s`、**規則名は `--name` / `-n`**。`--rule-name` は存在しない（`--name` にサーバ名を渡すと「`--server-name` が必要」と言われ、`--rule-name` を渡すと「認識されない引数」になる）。

**新しい azd 環境を作ると `AZURE_SUBSCRIPTION_ID` は引き継がれない。** 環境ごとに独立しているため、`azd env new` の後に `azd env set AZURE_SUBSCRIPTION_ID <id>` が必要（`azd up` が `prompt required` で止まる）。

**Python の `pathlib.Path.write_text` は Windows で LF を CRLF に変える。** 読み込み側
(`read_text`)が CRLF を LF に正規化するので、LF のファイルを読んで書き戻すと**静かに
CRLF になる**。シェルスクリプトでこれをやると Alpine の `sh` が
`set: line 24: illegal option -` で落ちる(実測。`` が引数に混ざる)。Python で
シェルスクリプトを書き換えるときは **`read_bytes` / `write_bytes` を使う**か
`newline=""` を指定する。`.gitattributes` が `eol=lf` を宣言していても、
**作業コピーの内容はそれとは別に壊れる**。

**変異テストの復元表を「ファイルの基底名」で作ってはいけない。** `routers/mappings.py` と
`repositories/mappings.py` のように基底名が同じファイルを 1 つの辞書に入れると
**キーが衝突して片方が復元されない**(実際に踏んだ。変異した実装が commit 直前まで
残っていた)。**復元の対象は絶対パスで持ち、変異を当てたファイルと復元するファイルが
一致していることを `diff` で確認する**。テストファイルの基底名を 3 ディレクトリで
一意にしている理由と同じ形の罠である。

**マシンが混んでいると Fuseki の `$/datasets` が `ReadTimeout` になる。** 実測で、変異テストを
長く回した直後の全テストで `test_competency_run.py` の 2 件が**セットアップ段階で**落ちた
(`GET http://localhost:3131/$/datasets` の読み取りタイムアウト)。**落ちる場所が変更と無関係**
なので原因を探しに行きたくなるが、**単独で回し直すと通る**。全体が通常の 2 倍以上の時間
(3 分半 → 8 分半)かかっていたら、まずマシンの負荷を疑う。

**`uv run pytest` を 2 つ同時に走らせてはいけない。** `packages/api/tests/conftest.py` の
`session` フィクスチャは**テストごとにスキーマを作り直す**(`drop_all` / `create_all`)ため、
2 プロセスが同じ PostgreSQL に対して走ると `DROP TABLE` で
`DeadlockDetectedError: deadlock detected` になる。**症状が原因を指さない** — 落ちるのは
`drop_all` をしたテストではなく、その隣で「publish した版が見つかりません」と言うテストである
(実測で 5 failed + 2 errors。単独で走らせると全件通った)。背景プロセスでスイートを走らせた
まま前景でもう一度走らせると踏む。

## 新しいテストを書くときの規律

**修正前のコードでそのテストが落ちることを確認してから直す。** 通るだけのテストを書かないため。このリポジトリでは実際にこの手順で複数の見せかけの修正を防いでいる。
