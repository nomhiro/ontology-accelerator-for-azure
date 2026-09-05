# バックログ

**このファイルはタスクの状態を持つ単一の正本である。** 方針は [`docs/roadmap.md`](roadmap.md)、設計判断は [`docs/adr/`](adr/) にある。

最終更新: 2026-09-06（`P1-C2` / `P1-18` の実装後）

## 使い方

- **ID は変えない。** コミットメッセージや ADR から参照するため
- **状態を変えたら同じコミットでこのファイルを更新する。** これが守られないとセッションをまたいだ引き継ぎが壊れる
- 各タスクの **出典** は「なぜこのタスクが存在するか」を示す。後から来た人が背景を辿れるようにするため
- 完了したタスクは削除せず `完了` にして残す。判断の履歴が消えると同じ議論を繰り返す

状態: `未着手` / `進行中` / `完了` / `見送り`
優先: `Critical` / `高` / `中` / `低`

---

## 今すぐ着手すべきもの

### `P1-C1` 既定グラフに全バージョンが載り、新旧の定義が同時に返る

- **状態**: 完了(2026-09-05、`P1-15` / `P1-16` と同一ラウンドで実装)
- **優先**: **Critical**
- **Phase**: 1（欠陥修正）
- **内容**: ローダが `approved/<namespace>/` の全 TTL を各名前付きグラフへ読み込み、`tdb2:unionDefaultGraph = true` のため既定グラフが全バージョンの和集合になる。`GRAPH` 句なしのクエリで矛盾する定義が同時に返る
- **実測**（2026-09-01）:
  ```
  公開: 1.0.0 (上位 20%) / 2.0.0 (上位 10%)
  GRAPH 句なしのクエリ → 両方が返る (2 件)
  ```
- **なぜ Critical**: (1) 2 つ目の版を公開した瞬間から発生する (2) エラーにならず静かに両方返る (3)「AI に正しい定義を渡す」という製品の中核目的に直接反する
- **完了条件**: 既定グラフが常に単一の版を指すこと。2 版を公開した状態で `GRAPH` 句なしのクエリが 1 件だけ返す統合テストを追加し、**修正前のコードで落ちることを確認**してから直す
- **出典**: [ADR-0009](adr/0009-ontology-operations.md) 決定 2。ADR-0006 が決定した保持ポリシーの実装漏れ
- **設計**: [ADR-0010](adr/0010-approval-and-projection.md) 決定 6 で確定。`unionDefaultGraph` をやめ、承認済み現行版を既定グラフにも読み込む
- **完了メモ**（2026-09-05）: `containers/fuseki/config.ttl` / `containers/fuseki/templates/config-tdb2` / `load-snapshot.sh` の `write_assembler` から `unionDefaultGraph` を削除。`ProjectionService.approve` が既定グラフへ `put_default_graph`（GSP の `?default`）で PUT する(丸ごと置き換わるため前の承認済み版は自動的に消える)。2 版を approve した状態で `GRAPH` 句なしのクエリが 1 件だけ返ることを実物の Fuseki に対して実証(`packages/api/tests/test_state_projection.py::test_p1_c1_two_approved_versions_default_graph_returns_exactly_one`)。修正前のコード・旧イメージで 2 件返ることも実測済み(報告参照)
- **関連**: `P1-C2`（同じローダを触る）、**`P1-15` / `P1-16` と同一ラウンドで実装する**

### `P1-C2` publish 前に TTL の構文検証をしていない

- **状態**: 完了(2026-09-06)
- **優先**: 高
- **Phase**: 1（欠陥修正）
- **内容**: `rdflib` は `packages/core` の依存に入っているが**使用 0 ファイル**。TTL は解析されずに Blob（正本）へ書かれる。壊れた TTL は正本に入り、その後 `put_graph` で失敗する。失敗は握り潰される設計（正しい）ため呼び出し元には成功が返り、`reconcile` は永久に失敗し続ける。さらに `P1-C1` の 409 ガードにより名前空間を削除できない
- **完了条件**: `publish` が Blob へ書く**前**に rdflib で解析し、失敗を 422 で返す。壊れた TTL を投入するテストを追加
- **完了メモ**（2026-09-06）: `packages/core/src/ontology_core/turtle.py` に
  `validate_turtle` / `TurtleSyntaxError` を新設。`ProjectionService.publish` は
  Blob への最初の書き込み(`put_version`)より前にこれを呼ぶ(`asyncio.to_thread`
  経由。rdflib の解析は同期・CPU バウンドで、20MB の TTL で実測約 18.5 秒
  かかるため、イベントループを塞がないよう別スレッドに逃がした)。
  `routers/versions.py` は `TurtleSyntaxError` を 422 にマップ(`AutoVersionError`
  と同じ形)。**rdflib の解析エラーは型が一貫していない**ことを実測で確認した
  ── ブリーフの例(`ex:A a` のような述語だけで終わる入力)は
  `rdflib.plugins.parsers.notation3.BadSyntax` ではなく `IndexError` になり、
  未終端の文字列リテラルは `AssertionError` になる。特定の例外型に絞ると検証を
  すり抜けるため、`validate_turtle` は `Exception` を広く捕まえて
  `TurtleSyntaxError` に正規化する(`packages/core/tests/test_turtle.py` で
  3 つの経路それぞれを固定)。Blob に何も書かれないこと・PostgreSQL に行が
  無いことを `test_projection.py` / `test_versions_router.py` で明示的に確認
  (修正前のコードでは両方とも書かれてしまうことを実際に確認してから直した)
- **出典**: 2026-09-01 の実装調査（`rdflib` 使用 0 ファイル）

---

## この修正が残した論点

### `P1-15` 未承認の版が射影される

- **状態**: 完了(2026-09-05、`P1-C1` / `P1-16` と同一ラウンドで実装)
- **優先**: 高
- **Phase**: 1（`P1-C1` と同じ族）
- **内容**: `publish` が版を `draft` として記録するようになった（誰も承認していないのに
  `approved` と記録する状態を解消した）。**しかし射影の挙動は変えていない**ため、
  未承認の版がそのまま Fuseki に載り、エージェントが未承認の定義を受け取りえる。
  Blob のプレフィックスが `approved/` であることも実態と食い違っている
- **設計**: [ADR-0010](adr/0010-approval-and-projection.md) で確定。**射影先を分けて解く**
  - `approved`（現行 1 版）→ 既定グラフ + 名前付きグラフ
  - `in-review` → 名前付きグラフのみ（レビュアが `GRAPH` 句で検証できる）
  - `draft` → 射影しない
  - `superseded` → 保持ポリシーの範囲で名前付きグラフのみ
  - ローダへは Blob 上の `versions/<ns>/_state.json` で状態を渡す（ローダは PostgreSQL を見ない）
  - Blob プレフィックスを `approved/` → `versions/` に改名
- **完了条件**: エージェント（`GRAPH` 句なし）が承認済み現行版だけを見ること、
  レビュア（`GRAPH` 句あり）が `in-review` の版を検証できることを統合テストで実証
- **完了メモ**（2026-09-05）: Blob プレフィックスを `versions/` に改名(全箇所は
  コミットの diff を参照)。`ProjectionService.publish` は Fuseki に触れず
  Blob(TTL) + PostgreSQL + マニフェストのみ書く。`submit`/`approve`/`reject` を
  実装し、状態ごとに射影先を分けた(`packages/api/tests/test_state_projection.py`、
  `test_approval.py` で実証)。ローダ(`load-snapshot.sh`)はマニフェスト
  (`versions/<ns>/_state.json`)を見て版ごとに名前付きグラフ/既定グラフへの
  読み込みを判断するよう書き換えた
- **出典**: 2026-09-03 の `publish` の status 修正。修正が新たに生んだ論点として記録
- **関連**: `P1-C1` / `P1-16` と同一ラウンドで実装する

### `P1-16` 最小の承認 API（submit / approve / reject）

- **状態**: 完了(2026-09-05、`P1-C1` / `P1-15` と同一ラウンドで実装)
- **優先**: 高
- **Phase**: 1（`P1-15` の前提）
- **内容**: `publish` が `draft` しか作らないため、承認する手段が無いと `publish` が
  書き込み専用の操作になる。[ADR-0010](adr/0010-approval-and-projection.md) 決定 1・3 の
  最小実装が必要
  ```
  POST /namespaces/{ns}/versions/{v}/submit   draft → in-review
  POST /namespaces/{ns}/versions/{v}/approve  in-review → approved（前の approved を自動 superseded）
  POST /namespaces/{ns}/versions/{v}/reject   in-review → draft（reason 必須）
  ```
- **Phase 1 の制約**: 名前空間 RBAC（`P2A-06`）と責任者（`P2B-04`）が未実装のため、
  **認証済みの呼び出し元が誰でも承認できる**。`approved_by` に誰が承認したかは記録される。
  「記録は正しく、強制が無い」状態であることを README に明記する。四眼原則も Phase 2
- **完了条件**: 状態遷移が `audit_events` に記録され、`approved_by` / `approved_at` が
  書かれること。`approve` が前の承認済み版を `superseded` にすること
- **完了メモ**（2026-09-05）: `routers/versions.py` に `submit`/`approve`/`reject` を
  追加(権限は強制しない。README に明記)。不正な遷移は 409、存在しない版は 404、
  `reject` の空 `reason` は 422。`approve` は前の `approved` を自動で `superseded`
  にし、`audit_events` に `submitted`/`approved`/`rejected`/`superseded` を記録する
  (`packages/api/tests/test_approval.py`、`test_versions_router.py`)
- **出典**: [ADR-0010](adr/0010-approval-and-projection.md) の「受け入れるコスト」
- **関連**: `P1-C1` / `P1-15` と同一ラウンド。本格版は `P2B-13`

### `P1-17` reject の名前付きグラフ削除が失敗すると回収経路が無い

- **状態**: 未着手
- **優先**: 低
- **Phase**: 1(`P1-16` の実装中に発見)
- **内容**: `reject` は `draft` に戻す際、`submit` で射影済みの名前付きグラフを
  `delete_graph`(GSP DELETE)で外す。この削除自体が失敗した場合(Fuseki
  一時障害等)、`draft` は `VersionRepository.unprojected()` の対象外
  (ADR-0010 決定5)なので、`reconcile()` はこの版を拾わない。つまり
  名前付きグラフの内容が消えずに残留し続け、`GRAPH` 句で審査済みのはずの
  却下版が見え続ける可能性がある。実害は限定的(既定グラフには影響しない。
  `GRAPH` 句を明示したレビュア・監査経路のみ)だが、自動回収する手段が無い
- **完了条件**: `reconcile()` が「`draft` かつ名前付きグラフが射影されたことがある
  (`projected_at` が過去に設定されていた形跡)」版を検出し、`delete_graph` を
  再試行できるようにする。現状のスキーマには「かつて射影されていたか」を
  区別する列が無いため、列追加を含めた設計が必要
- **出典**: 2026-09-05 の `P1-16` 実装時に発見(`ProjectionService.reject` の
  コメント参照)。直さず記録のみ
- **関連**: `P1-16`

### `P2B-13` 承認 API と状態遷移

- **状態**: 未着手
- **優先**: 高
- **Phase**: 2（柱 B）
- **内容**: `OntologyVersionStatus` は `draft` / `in-review` / `approved` / `superseded` の
  4 状態を定義しているが、**到達可能なのは `draft` だけ**（他は 0 箇所で未使用）。
  状態遷移を動かす API と、`approved_by` / `approved_at` を書く経路が必要
- **[ADR-0010](adr/0010-approval-and-projection.md) で決定済み**: 状態遷移、承認の粒度（版単位）、
  `superseded` は自動、承認の実体は API に置く（UI・外部システムはそれを叩く）、Git ベースは却下
- **Phase 2 でやること**: `P1-16` の最小実装に**権限の強制**を足す
  - 責任者のみが `approve` できる（`P2B-04` に依存）
  - 四眼原則（提案者と承認者を別人にする。設定で無効化可能）
  - 名前空間 RBAC との統合（`P2A-06` に依存）
- **ADR-0010 が未決として残した問い**: 保持ポリシーの既定値、却下された `draft` の扱い、
  廃止（用語単位）と `superseded`（版単位）の関係、外部承認システムとの連携の具体形
- **出典**: 2026-09-03 の実装調査（`DRAFT` / `IN_REVIEW` / `SUPERSEDED` が 0 箇所）

---

### `P1-19` 名前空間がスキップされたことに運用者が気づけない

- **状態**: 未着手
- **優先**: 中
- **Phase**: 1
- **内容**: ローダはマニフェストが取得できない・不正な名前空間を**丸ごとスキップ**する
  （1 件の設定不備が他の名前空間を巻き込んで全滅させないため。実装者の判断を
  controller が承認済み）。推測はしないので `P1-C1` の再来はない。
  **しかし気づく手段がログしかない。** スキップされた名前空間のデータセットは
  存在するが空になるため、エージェントから見ると「データが無い」と区別がつかない
- **なぜ重要**: このプロジェクトが繰り返し戦っている「静かに間違う」型の問題。
  設定不備が「0 件が返る」として現れると、原因の特定に時間がかかる
- **完了条件**: スキップされた名前空間を運用者が API から検出できること。
  `POST /admin/reconcile` の報告に含めるのが自然（既に `orphan_datasets` /
  `orphan_blobs` を報告している）。健全性指標（`P2B-06`）にも含める
- **出典**: 2026-09-06 に controller が実装のコメントを読んで気づいた論点

### `P1-18` ローダの制御フローに自動テストが無い

- **状態**: 完了(2026-09-06、範囲を絞って。下記メモ参照)
- **優先**: 中
- **Phase**: 1
- **内容**: `load-snapshot.sh` の `fetch_manifest` / `build_namespace_tdb` / `build_tdb`
  （マニフェストの取得、状態別の読み込み先の振り分け、マニフェスト無しの名前空間を
  スキップして全体を落とさない制御）に**再実行可能なテストが無い**。
  `validate.sh` に追加された `validate_manifest_json` / `manifest_current` /
  `manifest_status_for_version` は `validate.test.sh` で検証されているが、
  それらを使う側の制御フローは検証されていない
- **実装者の報告**: 実 Fuseki コンテナに対してシェル関数を `awk` で抽出し、
  合成した4パターン（approved / in-review / superseded / 未掲載）のマニフェストで
  手動駆動して期待どおりの結果を確認した。**ただし再実行可能な形では残っていない**
- **なぜ重要**: `P1-C1` の Critical はローダの振り分けが正しいことに依存している。
  ここが壊れると既定グラフに複数版が載る状態へ静かに戻る
- **完了条件**: 4パターンのマニフェストに対する振り分けを `validate.test.sh` と
  同じ形式で自動化する。マニフェスト無しの名前空間で失敗を明示することも含める
- **完了メモ**（2026-09-06）: `build_namespace_tdb` にインラインで埋まっていた
  状態別振り分け(旧 `case` 文)を、副作用の無い純粋関数 `projection_targets`
  (`containers/fuseki/lib/validate.sh`)に切り出した。`build_namespace_tdb` は
  この関数の出力(`"named default"` / `"named"` / 空)を解釈するだけになり、
  状態を判定する `case` 文はもう持たない(二箇所に判断があると片方だけ直して
  食い違うため)。`validate.test.sh` に 6 パターン(承認済み+current一致/
  不一致、in-review、superseded×SUPERSEDED_RETAIN=0/2、未掲載版)を追加し、
  修正前は関数が存在せず `not found` で落ちることを確認してから直した。
  **範囲を絞った**: ブリーフ(`.superpowers/sdd/2026-09-06-validation-and-loader-tests/brief.md`)
  の指示により、`fetch_manifest`(curl の I/O)と、マニフェストが取得できない・
  不正な名前空間を丸ごとスキップする `build_tdb` の制御は今回の対象外にした
  (前者は外部 I/O、後者は `validate_manifest_json` の形式検証は既にテスト
  済みで、スキップの分岐そのものの制御フローテストはまだ無い)。したがって
  この完了条件の「マニフェスト無しの名前空間で失敗を明示することも含める」は
  **未達のまま**。`P1-19`(スキップの可視化)と合わせて別ラウンドで扱うのが
  自然
- **出典**: 2026-09-05 の実装者の自己申告。「検証はしたが自動化はできていない」

## Phase 1 の残り

### `P1-09` Entra アプリ登録と認証経路の実証

- **状態**: 未着手
- **優先**: 高
- **内容**: API 用のアプリ登録を作り、`ENTRA_API_AUDIENCE` を Bicep から注入し、client credentials でトークンを取って publish / reconcile が通ることを実機で確認する
- **完了条件**: publish が 201、reconcile が 200 を返すことを Azure 実機で確認
- **出典**: Phase 1 実機検証。`ENTRA_API_AUDIENCE` が空であることを実測し、認証必須の経路が原理的に検証できないと判明
- **前提**: テナントに `allowedToCreateApps: true` を確認済み（個人の既定ディレクトリ）
- **ブロックしているもの**: `P1-10`

### `P1-10` postprovision を Core API 経由にする

- **状態**: 未着手
- **優先**: 高
- **内容**: 現在 postprovision は Blob に直接書き、PostgreSQL に行を入れない。そのため `GET /namespaces` と MCP の `list_namespaces` が空配列を返し、**デプロイしたサンプルがエージェント経路から発見できない**。書き込み順序 Blob → PostgreSQL → Fuseki の 2 段目が飛んでいる
- **完了条件**: `azd up` 後に MCP の `list_namespaces` がサンプルの名前空間を返すこと
- **なぜ重要**: Phase 1 の完了条件「AI エージェントが MCP 経由で参照できる」のうち**発見経路が満たされていない**
- **出典**: ブランチ全体レビュー I-6
- **依存**: `P1-09`（認証が必要）

### `P1-11` PostgreSQL の最小権限ロール

- **状態**: 未着手
- **優先**: 高
- **内容**: UAMI が PostgreSQL の Entra 管理者として登録されており、API の実行時 ID が `azure_pg_admin` 権限を持つ。侵害されれば DB を DROP できる
- **完了条件**: API が必要最小限の権限で動作し、管理者権限を持たないこと
- **出典**: Task 8 レビューの差分外指摘。「管理者権限で動いてしまうために誰も困らず、最小権限化の欠落が発覚しなかった」

### `P1-12` MCP → Core API のトークン伝播

- **状態**: 未着手
- **優先**: 中
- **内容**: MCP サーバが Core API を呼ぶ際の認証。現状は未設計
- **依存**: `P1-09`

### `P1-13` 基準バージョンによる lost update の検出

- **状態**: 未着手
- **優先**: 中
- **内容**: `PublishRequest` は `turtle` と `version` だけで基準バージョンを渡す口が無い。2 人が同じ版から編集して公開すると、後の版に前の変更が含まれない。検出も警告もされない
- **完了条件**: 基準バージョンを受け取り、最新と一致しなければ 409 を返す。同時編集のテストを追加
- **出典**: 2026-09-01 の実装調査（`base_version` / `If-Match` 相当が 0 件）

### `P1-S2` スパイク②: ACA の idle/active 課金比率の実測

- **状態**: 未着手
- **優先**: 中
- **内容**: idle 単価は active の 1/8。Fuseki が常時 active と判定されると 1 vCPU 構成で月 $105 前後まで上振れする。費用試算の最大の不確実性
- **完了条件**: 実測値を `docs/cost-estimate.md` に反映
- **注意**: 数時間〜数日デプロイを維持する必要があり、費用が発生する。実施前に確認を取る

### `P1-S3` スパイク③: Ontop 配布物のライセンス確認

- **状態**: 未着手
- **優先**: 低
- **内容**: Ontop 本体は Apache-2.0 だが、配布イメージに同梱される JDBC ドライバは別ライセンスの可能性がある
- **完了条件**: `docs/third-party-licenses.md` に結論を記載
- **出典**: 当初計画の R6

### `P1-14` POSIX 経路での `azd up` の通し確認

- **状態**: 未着手
- **優先**: 低
- **内容**: 実機検証は Windows（pwsh）経路のみ。POSIX 経路で `azd up` を通した人がいない。`scripts/postprovision.sh` の実行ビット欠落は修正済みだが、それは既知のブロッカーを除去したにすぎない
- **出典**: Task 8 レビュー I-3。「1 つの経路で確認して両方に一般化する」失敗を避けるため明示

---

## Phase 2 柱 A — AI がオントロジーを作れる

| ID | 内容 | 優先 | 状態 |
|---|---|---|---|
| `P2A-01` | 顧客 DB 接続とスキーマ自動発見（scan-job） | 高 | 未着手 |
| `P2A-02` | LLM によるオントロジー候補生成（OWL/SHACL） | 高 | 未着手 |
| `P2A-03` | Web でのレビュー・承認フロー | 高 | 未着手 |
| `P2A-04` | グラフ可視化（Cytoscape.js 等） | 中 | 未着手 |
| `P2A-05` | pyshacl による SHACL 検証 | 高 | 未着手 |
| `P2A-06` | 名前空間 RBAC の強制 | 高 | 未着手 |
| `P2A-07` | 監査証跡の PROV-O 表現 | 中 | 未着手 |
| `P2A-08` | `SPARQL_MAX_RESULTS` の強制 | 中 | 未着手 |

`P2A-08` の補足: 現在は値が保持され Bicep が注入しているが強制されていない。任意の SPARQL に LIMIT を後付けするのは副問い合わせや CONSTRUCT で壊れるため、安価で正しい手段が無い。README と `config.py` に未強制であることを明記済み。

---

## Phase 2 柱 B — 運用し続けられる

すべて [ADR-0009](adr/0009-ontology-operations.md) が根拠。決定番号を併記する。

| ID | 内容 | ADR-0009 | 優先 | 状態 |
|---|---|---|---|---|
| `P2B-01` | OWL 推論器（ELK）を CI へ前倒し | 決定 1 | 高 | 未着手 |
| `P2B-02` | 保持ポリシーの実装（ストアに載せる版の制御） | 決定 2 | 高 | 未着手 |
| `P2B-03` | 廃止のライフサイクル（`owl:deprecated` + 後継） | 決定 3 | 高 | 未着手 |
| `P2B-04` | 名前空間と用語の責任者 | 決定 4 | 高 | 未着手 |
| `P2B-05` | アクセスログの実装（ADR-0006 §4、健全性指標の原資料） | 決定 5 | 高 | 未着手 |
| `P2B-06` | 健全性指標の集計と提示 | 決定 5 | 中 | 未着手 |
| `P2B-07` | 想定質問を SPARQL テストとして CI で実行 | 決定 6 | 高 | 未着手 |
| `P2B-08` | `reason` / `diff` を書き、参照時に返す | 決定 7 | 中 | 未着手 |
| `P2B-09` | 意味的差分の計算と差分レビュー | 決定 7 | 中 | 未着手 |
| `P2B-10` | 領域間マッピング（SKOS `closeMatch` 等） | 決定 8 | 中 | 未着手 |
| `P2B-11` | 監査を読み出す API | 決定 7 | 中 | 未着手 |
| `P2B-12` | 削除の TOCTOU を閉じる（監査付き削除） | — | 中 | 未着手 |

`P2B-08` の補足: `audit_events` の `reason` / `diff` 列は既に存在するが `publish` が渡していない。**最も安い施策**。

`P2B-12` の補足: `DELETE` の Blob 判定と PG 削除の間に並行 publish が入ると、Blob だけが残る狭い競合がある。現状は `POST /admin/reconcile` の `orphan_blobs` で検出できる（自動削除はしない）。

### ADR-0009 が未決として残した問い

設計時に決める必要がある。

- 責任者の粒度（名前空間単位か、用語単位か、両方か）
- 廃止された用語を参照するクエリへの警告を、どの層でどう返すか
- 意味的差分の計算方法（既存ツールか rdflib で自作か）

---

## Phase 3

| ID | 内容 | 優先 | 状態 |
|---|---|---|---|
| `P3-01` | Ontop VKG（R2RML 管理 + 連邦クエリ） | 高 | 未着手 |
| `P3-02` | Azure AI Search 統合（`search_context` ツール） | 高 | 未着手 |
| `P3-03` | Metric Service | 中 | 未着手 |
| `P3-04` | Context Manager のオーケストレーション | 高 | 未着手 |
| `P3-05` | 定義と実データの乖離検出 | 中 | 未着手 |
| `P3-06` | （任意）Microsoft Purview コネクタ | 低 | 未着手 |

`P3-05` の補足: SHACL 形状を定期的に実データへ当て、0 件しかマッチしない定義を報告する。Ontop 経由の連邦クエリが前提のため Phase 3 に置く。

---

## Phase 4

| ID | 内容 | 優先 | 状態 |
|---|---|---|---|
| `P4-01` | production プロファイル（VNet / Private Endpoint / AKS 昇格ガイド） | 高 | 未着手 |
| `P4-02` | 可観測性・負荷試験 | 高 | 未着手 |
| `P4-03` | ライセンス自動スキャンの CI 化 | 中 | 未着手 |
| `P4-04` | GitHub Actions の依存更新（Node.js 20 対象のアクション） | 低 | 未着手 |
| `P4-05` | 表示名の最終決定（商標の論点） | 中 | 未着手 |
| `P4-06` | `docs/superpowers/` を公開範囲に含めるかの決定 | 低 | 未着手 |
| `P4-07` | awesome-azd 申請 | 低 | 未着手 |
| `P4-08` | v0.1.0 リリース | 低 | 未着手 |

`P4-06` の補足: `docs/superpowers/plans/` は 2400 行超の内部実装計画で、controller 向けの指示文を含む。実リソース名は伏せ字にしたが、公開範囲に含めるか自体は未決。

---

## 意図的に見送っているもの

「やらないと決めた」ことの記録。同じ提案が再浮上したときの判断材料になる。

| 内容 | 理由 |
|---|---|
| AI に矛盾の判断を委ねる | 論理的矛盾は決定可能で推論器の仕事。業務的妥当性は説明責任の問題で AI は責任を負えない（[ADR-0009](adr/0009-ontology-operations.md)） |
| 品質スコアでリリースをゲートする | 点数が行動に結びつかない。想定質問テストと SHACL という落ちた理由が自明な形を採る |
| 全社で単一のオントロジーを作る | 合意形成コストが規模の二乗で増える。名前空間で分離しマッピングで繋ぐ |
| 廃止を扱わず削除で済ませる | 過去の判断が説明不能になり、監査可能性を失う |
| owlready2 の採用 | 改変版 HermiT（LGPL-3.0）を同梱するため（[ADR-0005](adr/0005-reasoner-boundary.md)） |
| HermiT の同梱 | LGPL-3.0。任意有効化のビルド時取得 + 別プロセス実行のみ |
| Microsoft Purview への依存 | CU 課金が発生する。任意のソースコネクタに留める（[ADR-0007](adr/0007-no-purview-dependency.md)） |
| Smithy による API 契約 | FastAPI/Pydantic → OpenAPI → TS 型生成で足りる（[ADR-0004](adr/0004-api-contract-strategy.md)） |
