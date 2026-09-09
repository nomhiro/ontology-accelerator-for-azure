# バックログ

**このファイルはタスクの状態を持つ単一の正本である。** 方針は [`docs/roadmap.md`](roadmap.md)、設計判断は [`docs/adr/`](adr/) にある。

最終更新: 2026-09-06（`P1-12` の実装後。Phase 1 で残るのは `P1-S2` / `P1-14` / `P1-25` の 3 件）

## 使い方

- **ID は変えない。** コミットメッセージや ADR から参照するため
- **状態を変えたら同じコミットでこのファイルを更新する。** これが守られないとセッションをまたいだ引き継ぎが壊れる
- 各タスクの **出典** は「なぜこのタスクが存在するか」を示す。後から来た人が背景を辿れるようにするため
- 完了したタスクは削除せず `完了` にして残す。判断の履歴が消えると同じ議論を繰り返す

状態: `未着手` / `進行中` / `完了` / `見送り`
優先: `Critical` / `高` / `中` / `低`

---

## 今すぐ着手すべきもの

**Phase 1 に未着手のタスクは 3 件だけで、いずれもすぐには着手できない。**

| ID | 内容 | 着手の前提 |
|---|---|---|
| `P1-S2` | ACA の idle/active 課金比率の実測 | 数時間〜数日デプロイを維持する。**費用が発生するのでユーザーの確認が必要** |
| `P1-14` | POSIX 経路での `azd up` の通し確認 | 実機デプロイ 1 回。**同上** |
| `P1-25` | reconcile が欠落グラフを自動復旧するか | ADR での設計判断が先（`projected_at` の意味と `SUPERSEDED_RETAIN` との関係） |

`P1-S2` と `P1-14` は**同じデプロイ窓に相乗りさせる**。MCP 認証（`P1-12`）の実機再確認も一緒に行うと 1 回で済む。

**費用の出る作業に入れないときは Phase 2 に進む。** 柱 A（AI が作れる）と柱 B（運用し続けられる）のどちらから始めるかは [`roadmap.md`](roadmap.md) を参照。最も安い施策は `P2B-08`（`reason` / `diff` を書いて参照時に返す。列は既に存在する）。

---

## 完了した Critical（判断の履歴として残す）

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

- **状態**: **完了**（2026-09-06。[ADR-0010 補記1](adr/0010-approval-and-projection.md)）
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
- **実装は完了条件と違う方法を取った（2026-09-06）**: **列を追加せず、
  ストアに実際にあるグラフと正本を突き合わせる**形にした。
  `reconcile` が `list_graphs(dataset)`（SPARQL の
  `SELECT DISTINCT ?g WHERE { GRAPH ?g { ?s ?p ?o } }`）でグラフ一覧を取り、
  `draft` 以外の版の `graph_iri` と比較して、
  「自分たちの IRI 接頭辞に一致し、かつ正本が求めていない」グラフを削除する。
  - **なぜ変えたか**: 列追加案はマイグレーションが必要な上、`reject` 以外の
    原因で生じた乖離（ストアの部分的な巻き戻し、手動操作）を拾えない。
    実際にあるものと正本を比較する方が原因に依存せず強い
  - 接頭辞に一致しないグラフは `foreign_graphs` として**報告するだけで削除しない**
    （`GRAPH_IRI_BASE` 変更後の古いグラフや持ち込みストアの別用途のグラフを
    消さないため）。削除してよい根拠の違いは ADR-0010 補記1 に書いた
  - 追加したテスト7件。**FakeStore に対する4件は修正前に落ちることを確認してから
    実装した**（残留グラフの回収 / `in-review`・`approved` のグラフを消さない /
    接頭辞外のグラフは報告のみ / 削除失敗でも reconcile を止めない）。
    残り3件は新しい経路そのものの検証:
    - 実物の Fuseki に対する `list_graphs` の検証（`test_state_projection.py`）。
      **クエリが既定グラフを名前付きグラフとして返さないことを実測で確認した**
      （返してしまうと reconcile が既定グラフを残留と誤判定する）
    - 実物の Fuseki に対する残留グラフの回収
    - `list_graphs` の応答形式エラーが `SparqlStoreError` に包まれること（不変条件4）
  - `uv run pytest` 131 → **138 件**（実物の Fuseki に対する2件と応答形式の1件を含む）
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

### `P1-21` マニフェスト取得不可・不正な名前空間のスキップ制御に自動テストが無い

- **状態**: **完了**（2026-09-06。`containers/fuseki/load-snapshot.test.sh`）
- **優先**: 中
- **Phase**: 1
- **内容**: `P1-18` で状態別の振り分け（`projection_targets`）は純粋関数化してテストしたが、
  **その手前の「マニフェストが取得できない・不正な名前空間を丸ごとスキップし、
  ローダ全体は落とさない」制御フロー**（`build_tdb` / `fetch_manifest`）は未検証。
  `validate_manifest_json` 自体はテスト済みだが、それを使う側の制御は対象外だった
- **経緯**: `P1-18` のバックログ記載に含めていたが、controller が書いたブリーフで
  範囲外に指定したため未達になった。**controller の不整合**であり、実装者が
  エスカレーションして分割することになった
- **完了条件**: マニフェスト取得失敗・不正 JSON の名前空間がスキップされ、
  **他の名前空間の読み込みが継続する**ことを再実行可能な形で検証する
- **実装した内容（2026-09-06）**: `containers/fuseki/load-snapshot.test.sh`（新規）。
  **関数を source せず、`curl` と `tdb2.tdbloader` をスタブに差し替えて
  スクリプトを丸ごと実行する**ブラックボックステストにした
  - **なぜ source しないか**: 検証対象は「curl の失敗と `set -eu` の相互作用」を
    含む制御フローそのものであり、純粋関数に切り出せる性質のものではない。
    ローダ本体を書き換えずに検証できる形を選んだ（本体の構造変更は Azure 実機で
    しか最終確認できず、費用が発生する）
  - 4 つの名前空間で検証する: マニフェスト正常 / 取得失敗（curl が 22 で終了）/
    JSON が壊れている / Blob にある版がマニフェストに載っていない（読み込む版 0 件）
  - 検証項目14件。**ローダ全体が成功で終わること**、正常な名前空間の assembler が
    書かれること、不備のある3つの assembler が書かれないこと、理由がログに出ること、
    **既定グラフへの読み込みがちょうど1回**であること（複数版が載ると `P1-C1` の
    Critical が再来する）、スキップした名前空間が `tdbloader` に渡らないこと
  - **変異テストでテストの有効性を確認した**: 不正マニフェストの `continue` を
    外すと 3 件が NG になる（うち1件は「既定グラフへの読み込みが2回」）。
    通るだけのテストではないことを実測で確かめてから採用した
  - CI（`shell` ジョブ）と `CLAUDE.md` の検証コマンドに追加した。
    **`jq` が必要**なので、jq が無い環境（Windows）では docker 経由で回す
- **関連**: `P1-19`（スキップの可視化）と同じ経路を触るので、合わせて1ラウンドにする

### `P1-22` スキップの理由がログから失われた

- **状態**: **完了**（2026-09-06。`projection_targets` が `skip:<理由>` を返す）
- **優先**: 中
- **Phase**: 1
- **内容**: `P1-18` で振り分けを純粋関数に切り出した結果、`build_namespace_tdb` に
  判断が無くなり、**「superseded だからスキップ」と「マニフェストに無いからスキップ」を
  区別する情報がその場に無くなった**。旧コードは理由別にログを出していた
- **なぜ重要**: 「なぜスキップされたか」が失われると、このプロジェクトが繰り返し
  戦っている「静かに間違う」に近づく
- **対応方針**: `reconcile` 側で拾うのではなく、**`projection_targets` の出力に理由を
  含める**（例: `skip:superseded-retain-0` / `skip:not-in-manifest`）。判断を1箇所に
  保ったまま、呼び出し元が理由をログに出せる
- **実装した内容（2026-09-06）**: `projection_targets` の契約を
  「空文字 = スキップ」から **`skip:<理由>`** に変えた。理由の値は
  `superseded-retain-0` / `not-in-manifest` / `unknown-status-<状態>`
  - **`unknown-status-<状態>` を足した。** 将来 API 側が新しい状態を導入したとき、
    ローダが黙って読み込まないのではなく状態名を添えて言えるようにするため
    （推測して読み込むことは絶対にしない）
  - **空文字は返さない契約にした。** 空文字だと呼び出し元でうっかり未設定の
    変数と区別が付かず、「静かに読み込まれない」状態に戻る。`build_namespace_tdb`
    側は `named` / `named default` / `skip:*` 以外を受け取ったら
    「呼び出し規約の違反」として異常をログに出してその版を諦める
  - テスト3件（修正前に落ちることを確認済み）
- **出典**: 2026-09-06 の実装者の自己申告

### `P1-23` テスト後に `just migrate` が DuplicateTableError で失敗する

- **状態**: **完了**（2026-09-06。フィクスチャが `alembic_version` を head で stamp する）
- **優先**: 低
- **Phase**: 1
- **内容**: `packages/api/tests/conftest.py` の session フィクスチャが
  `Base.metadata.create_all` でテーブルを作るが、**`alembic_version` を stamp しない**。
  そのためテストを実行した後に `just migrate` すると既存テーブルに当たって失敗する
- **なぜ重要**: 「テストを回してから `just migrate`」という自然な順序で貢献者が踏む。
  テストには影響しない（フィクスチャが毎回 drop_all → create_all する）ので害は
  限定的だが、初見では原因が分からない
- **対応方針**: フィクスチャで `alembic stamp head` 相当を行うか、README に
  `just clean` を先に実行する旨を書く
- **実装した内容（2026-09-06）**: `session` フィクスチャが `create_all` の後に
  `alembic_version` を head で stamp する。head は**ハードコードせず**
  alembic のスクリプトから読む（マイグレーションを追加したときに古い値を
  黙って stamp しないため）
  - **修正前に実際に再現させてから直した**: テスト実行後は `alembic_version`
    テーブルが存在せず、`just migrate` が DuplicateTableError で失敗する。
    修正後は同じ手順で成功する（`alembic_version` に `03fff5e0d815` が入る）
  - 回帰テスト1件。**stamp を外すと落ちることも確認した**
  - `alembic_version` は `Base.metadata` に無いため `drop_all` の対象外。
    フィクスチャで毎回 head に揃える（冪等）
- **出典**: 2026-09-06 の実装者の報告

### `P1-20` TTL のサイズ上限が解析コストに対して大きすぎる

- **状態**: **完了**（2026-09-06。上限を 5,000,000 文字に下げた）
- **優先**: 中
- **Phase**: 1
- **内容**: `PublishRequest` は `max_length=20_000_000`（20MB）を許すが、rdflib の
  解析コストは**約 1.0 秒/MB**（controller が実測: 0.12MB→0.11秒、1.27MB→1.30秒。
  実装者の 20MB→18.5秒と整合）。20MB の入力は**約20秒スレッドを占有する**
- **なぜ重要**: `asyncio.to_thread` でイベントループは塞がないが、スレッドプールを
  占有する経路になる。認証が必要な API なので外部からの無認証 DoS ではないが、
  上限値としては大きすぎる
- **対応方針**: 実オントロジーの規模を踏まえて上限を下げる（数 MB 程度が妥当か）。
  上限を超える投入を別の経路（非同期ジョブ）に回す設計も選択肢
- **実装した内容（2026-09-06）**: `MAX_TURTLE_LENGTH = 5_000_000` を
  `routers/versions.py` に定数として置き、根拠（実測値と占有時間の見積り）を
  同じ場所にコメントで残した
  - 5MB を選んだ理由: オントロジーの定義（クラス・プロパティ・制約）は実データを
    含まないため、企業のドメイン1つ分でも Turtle で数百 KB に収まる。
    5MB（約5秒）は実用上十分に余裕がある
  - **これを超える規模が必要になったら上限を上げず、非同期ジョブ経路に回す**
    方針をコメントに明記した。上限を上げるとスレッドの占有時間がそのまま伸びる
  - テスト3件（`packages/api/tests/test_request_limits.py`）: 上限が
    5,000,000 以下であること（引き上げに気づくため）/ 上限ちょうどは受理 /
    上限超過は**解析前に**スキーマ検証で 422
- **出典**: 2026-09-06 の実測

### `P1-19` 名前空間がスキップされたことに運用者が気づけない

- **状態**: **完了**（2026-09-06。`POST /admin/reconcile` の `missing_graphs` で検出できる）
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
- **実装した内容（2026-09-06）**: `reconcile` が**ローダと一切協調せずに**検出する。
  正本（PostgreSQL）はどの版の射影を求めているかを知っているので、
  `list_graphs(dataset)` で取ったストアの実際のグラフと突き合わせれば、
  「射影されているべきなのに無い」を判定できる。`ReconcileReport.missing_graphs`
  に `<名前空間>: <グラフIRI> (<状態>)` の形で入る
  - **`projected_at` は過去に射影したときのまま残る**ため `unprojected()` では
    拾えない。この突き合わせが唯一の検出手段になる
  - **`superseded` は報告しない。** 既定（`SUPERSEDED_RETAIN=0`）でローダが
    読み込まないため、無いのが正常。報告すると正常な構成で毎回ノイズが出て
    本当の異常が埋もれる
  - **自動復旧はしない**（`P1-25` に分離した）。ただし reconcile はこの後
    マニフェストを正本から再生成するため、スキップの原因がマニフェストの
    欠落・破損であれば**次の再構築では直っている**。運用者はこの報告を見て
    再構築を促せばよい
  - テスト2件（修正前に落ちることを確認済み）: 承認済みの版の欠落を報告する /
    `superseded` の欠落は報告しない
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

- **状態**: **完了**（2026-09-05、Azure 実機で検証。検証後に `azd down --purge`）
- **優先**: 高
- **内容**: API 用のアプリ登録を作り、`ENTRA_API_AUDIENCE` を Bicep から注入し、client credentials でトークンを取って publish / reconcile が通ることを実機で確認する
- **完了条件**: publish が 201、reconcile が 200 を返すことを Azure 実機で確認
- **出典**: Phase 1 実機検証。`ENTRA_API_AUDIENCE` が空であることを実測し、認証必須の経路が原理的に検証できないと判明
- **前提**: テナントに `allowedToCreateApps: true` を確認済み（個人の既定ディレクトリ）
- **実測結果**:
  ```
  トークン無し  /namespaces      -> 401
  トークン有り  /namespaces      -> 200
  トークン有り  /admin/reconcile -> 200
  ```
- **設定で踏んだ罠2件**（再構築時の参照用）:
  1. **`api` は複合プロパティ**なので部分 PATCH すると `api` 全体が置き換わる。
     スコープと `preAuthorizedApplications` は**同時に送る**必要がある
  2. **`aud` は `api://` の URI ではなく appId の GUID**。`ENTRA_API_AUDIENCE` に
     URI を入れると `Audience doesn't match` で失敗する（実コードで両方試して確認）
  3. `requestedAccessTokenVersion` を **2** にする。既定の `null`（v1 相当）では
     `iss` が `sts.windows.net` になり、`entra.py` が固定している v2.0 発行者と一致しない
- **クライアントシークレットを持たない設計**: Azure CLI
  （`04b07795-8ddb-461a-bbee-02f9e1bf7b46`）をアプリ登録の
  `preAuthorizedApplications` に登録し、`az account get-access-token` で
  運用者自身の資格情報からトークンを取る。秘密を扱う経路が無い
- **アプリ登録は `azd down` では消えない。** テナントに残る永続的な成果物

### `P1-10` postprovision を Core API 経由にする

- **状態**: **完了**（2026-09-05、Azure 実機で検証）
- **優先**: 高
- **内容**: 現在 postprovision は Blob に直接書き、PostgreSQL に行を入れない。そのため `GET /namespaces` と MCP の `list_namespaces` が空配列を返し、**デプロイしたサンプルがエージェント経路から発見できない**。書き込み順序 Blob → PostgreSQL → Fuseki の 2 段目が飛んでいる
- **完了条件**: `azd up` 後に MCP の `list_namespaces` がサンプルの名前空間を返すこと
- **なぜ重要**: Phase 1 の完了条件「AI エージェントが MCP 経由で参照できる」のうち**発見経路が満たされていない**
- **出典**: ブランチ全体レビュー I-6
- **依存**: `P1-09`（認証が必要）
- **設計上の発見**: **`postprovision` は `deploy` の前に走る**ため、その時点で
  コンテナのイメージはプレースホルダで **API が起動していない**。API 経由にするには
  `postdeploy` へ移す必要があった。`scripts/postprovision.{sh,ps1}` を
  `scripts/postdeploy.{sh,ps1}` に置き換え、`azure.yaml` のフックも変更した
- **`continueOnError: false` にした理由**: ここが失敗すると Phase 1 の完了条件が
  満たされないため、黙って成功扱いにしてはいけない。スクリプト自身が最後に
  `GET /namespaces` を引いて発見できることを確認する
- **実測結果**（`azd up` の中で `postdeploy` が走ったことを時刻で確定）:
  ```
  名前空間 created_at : 16:52:31.86
  version created_at  : 16:52:32.31
  approved_at         : 16:52:33.36
  azd up 終了         : 16:52:36
  GET /namespaces     -> retail-core が 1 件
  SPARQL owl:Class    -> Customer / Order / Product / Store の 4 件
  reconcile           -> 孤児ゼロ（datasets_created / versions_projected /
                         failures / orphan_datasets / orphan_blobs すべて空）
  ```
  明示的に 2 回目を実行すると 409 が返り、**スクリプトが冪等**であることも実証された

### `P1-11` PostgreSQL の最小権限ロール

- **状態**: **完了**（2026-09-06。Azure 実機で 19 項目を検証し NG 0 件）
- **優先**: 高
- **内容**: UAMI が PostgreSQL の Entra 管理者として登録されており、API の実行時 ID が `azure_pg_admin` 権限を持つ。侵害されれば DB を DROP できる
- **完了条件**: API が必要最小限の権限で動作し、管理者権限を持たないこと
- **出典**: Task 8 レビューの差分外指摘。「管理者権限で動いてしまうために誰も困らず、最小権限化の欠落が発覚しなかった」
- **実機で確認した現状**（2026-09-05）:
  ```
  Entra 管理者        : id-<suffix> (ServicePrincipal) = API の実行時 ID そのもの
  POSTGRES_USER       : id-<suffix>
  publicNetworkAccess : Enabled（VNet 統合なし）
  ファイアウォール    : AllowAllAzureServicesAndResources (0.0.0.0) のみ
                        = **運用者のマシンからは届かない**
  マイグレーション    : API の docker-entrypoint.sh で起動時に実行
  ```
- **先に決めるべき設計判断（ADR-0011 の対象）**: API が管理者でなくなると
  **起動時のマイグレーション（DDL）が実行できない**。選択肢:
  - **(A) `SET ROLE` で実行時に権限を落とす** — 管理者として接続し、
    マイグレーション後にアプリ用ロールへ切り替える。実装は小さいが
    `RESET ROLE` で戻せるため強い境界にならない
  - **(B) マイグレーションを API の外へ出す** — ACA Job か `postdeploy` フックで
    実行し、API の ID は非管理者にする。標準的な答えで `maxReplicas: 3` の
    同時実行問題も根本的に解消するが、**ファイアウォールが Azure 内のみ**なので
    `postdeploy`（運用者マシン）からは届かない → ACA Job が必要
  - **(C) アプリ用ロールに自スキーマの DDL を与える** — 単純だが
    「侵害 → DROP DATABASE」が「侵害 → DROP TABLE」に緩和されるだけ
  - **(D) コンテナに 2 つのマネージド ID を持たせる** — ACA は複数の
    ユーザー割り当て ID を持てる。起動時は管理者 ID、実行時はアプリ ID。
    `AZURE_CLIENT_ID` の切り替えが必要で込み入る
- **[ADR-0011](adr/0011-database-privilege-separation.md) で決定済み**（2026-09-06）。
  設計を詰める中で**最初の見立てより深い問題**が判明した:
  - **管理者の資格情報が API コンテナから到達可能なら最小権限は達成されない。**
    同一コンテナに2つの ID を持たせる案 (D) は、侵害されれば管理者トークンも
    取れるので**見せかけ**である
  - **アプリのロールがテーブルを所有していると DDL 権限の有無に関わらず削除できる。**
    案 (C)（スキーマ限定の DDL）はサーバー管理者を外すが、`audit_events` を
    `DROP` / `TRUNCATE` できるため **ADR-0006 の監査可能性が攻撃者に対して成立しない**
  - 監査を守るには**テーブルの所有者とアプリのロールを分ける**必要があり、
    その結果マイグレーションはアプリのロールでは実行できない。
    **「監査を守る」という要求が実行主体の分離を強制する**
  - 案 (B) の ACA Job は正しい構造だが、**azd がサービス以外のイメージを更新しない**
    問題（ADR-0002 の補記。init コンテナで一度踏んだ轍）を再来させる危険がある
- **決定した構成**:
  1. Entra 管理者を**デプロイ実行者**にする（UAMI を管理者から外す）
  2. **`ontology_owner`（NOLOGIN）がテーブルを所有**し、アプリのロールには DML のみ。
     `audit_events` の `DELETE` は与えない（追記専用）
  3. マイグレーションを `docker-entrypoint.sh` から外し **`postdeploy` で運用者が実行**
  4. 運用者の IP を許可する規則を `postdeploy` の中で作り、最後に削除する
  5. API 起動時にテーブルが無い窓を許容する（`/healthz` は DB を触らない）
  6. `production` プロファイル（VNet 統合）での完全な分離は **Phase 4**
- **受け入れるコスト**: 無人の CI/CD では動かない（運用者のマシンが必要）。
  これは Phase 4 で解決する
- **実装した内容（2026-09-06、修正1〜6すべて対応）**:
  1. `infra/modules/postgres.bicep`: Entra 管理者を UAMI からデプロイ実行者
     （`principalId` / `principalName` / `principalType`）に変更。`identityName`
     は接続ユーザー名の組み立てだけに残す
  2. `scripts/bootstrap-db.py`（新規）: `asyncpg` ベースの冪等なブートストラップ。
     `pre` / `post` の2フェーズ
  3. `packages/api/alembic/env.py`: `MIGRATION_ROLE` が設定されていれば
     `SET ROLE` してからマイグレーションを実行する
  4. `packages/api/docker-entrypoint.sh`: マイグレーション実行を削除し、
     uvicorn を直接 exec する。ADR-0011 で Task 8 の判断を覆した理由をコメントに残した
  5. `scripts/postdeploy.{sh,ps1}`: ファイアウォール規則の作成・削除（trap /
     try-finally）、`bootstrap-db.py pre` → マイグレーション → `bootstrap-db.py post`
     を既存のサンプル投入の前に追加
  6. `scripts/preprovision.{sh,ps1}`（新規）+ `azure.yaml`: 運用者の Entra 表示名
     （UPN）を解決して `AZURE_PRINCIPAL_NAME` を設定する。`AZURE_PRINCIPAL_ID` は
     azd が provision 時に自動的に解決するが（Microsoft Learn の環境変数一覧で
     確認）、名前（UPN/表示名）側にはこの既定変数が無いため
  - `infra/main.bicep` に `POSTGRES_SERVER_NAME` output を追加（ファイアウォール
    規則の操作に `--name` として必要。既存の `POSTGRES_HOST` はFQDNで使えない）
  - `README.md` の「必要な Azure 権限と Entra ID の前提」を更新
- **ブリーフの記述を2件修正した（実装前に報告済み。理由は下記）**:
  1. `pgaadauth_create_principal` はアプリの DB ではなく `postgres`
     （メンテナンス用）データベースに接続して実行する必要がある。他の DB から
     呼ぶと `function ... does not exist`（Microsoft Q&A で複数件確認・実測でも
     再現）。`bootstrap-db.py` は `postgres` DB への別接続でこのステップだけ実行する
  2. PostgreSQL 15 以降 `public` スキーマの `CREATE` は `PUBLIC` から剥奪されている
     ため、`ontology_owner` に `GRANT USAGE, CREATE ON SCHEMA public` が無いと
     `SET ROLE ontology_owner` 下での `CREATE TABLE`（マイグレーション）が
     `permission denied for schema public` で失敗する（ローカルの
     postgres:16-alpine で実測確認）。ブリーフのSQLに無かったため追加した
- **実装中に見つけたバグ（テストで検出。ブリーフには無い）**: `env.py` で
  `SET ROLE` を実行するために `connection.execute()` を呼ぶと、`AsyncConnection`
  が暗黙にトランザクションを開始する（autobegin）。その状態のまま Alembic の
  `begin_transaction()` に入ると、Alembic 自身は成功ログを出す（内部的には
  ネストしたトランザクションの commit が成功している）にもかかわらず、**外側の
  トランザクションは一度も commit されず、`engine.connect()` を抜ける際に暗黙に
  ROLLBACK されてマイグレーションの DDL が丸ごと消える**。実測で再現し、
  `SET ROLE` の直後に明示的な `await connection.commit()` を追加して解消した
  （修正前に一度 `alembic upgrade head` が exit 0 のままテーブルを1つも作らない
  ことを確認してから直した）
- **ローカルで確認した内容**:
  - `uv run pytest` 131件、`ruff check` / `ruff format --check`、`mypy packages`、
    `az bicep build` すべて成功
  - `bootstrap-db.py` の冪等性: `pre` / `post` それぞれ2回連続実行して両方成功
    （ローカル postgres:16-alpine、使い捨てDBに対して）
  - `MIGRATION_ROLE` の分岐: **両方を実測で確認した**。設定時（`MIGRATION_ROLE=
    ontology_owner`）は `SET ROLE` してマイグレーションを実行し、テーブルの
    所有者が `ontology_owner` になる。未設定時は何もせず、テーブルの所有者は
    接続ユーザー自身（`ontology`）になる ── 使い捨てDBに対して両方の版を
    それぞれ作り直し、`pg_tables.tableowner` の違いで対比を確認した。
    既存の `just migrate` の経路は変更なし（131件のテストに影響なし。
    テストは alembic を経由せず `Base.metadata.create_all` を使うため、
    この分岐自体はテストの対象外であることに注意）
  - 権限分離の実効性そのもの: 非スーパーユーザーの `LOGIN` ロール（UAMI の代役）
    で接続し、`INSERT` は成功、`DELETE FROM audit_events` と `DROP TABLE` は
    どちらも権限エラーで拒否されることを確認
  - `#EXT#` を含むゲストUPN形式の名前の扱い（3点）: (1) Bicep の `principalName`
    と `bootstrap-db.py` の `asyncpg.connect(user=...)` はどちらも UPN を
    そのまま文字列として渡す口で、SQL識別子としての引用は関与しない
    (2) SQL識別子として運用者名を直接埋め込む経路（`GRANT ontology_owner TO
    "<運用者>"`）は `CURRENT_USER` を使う設計に変えて構造的に無くした
    (3) それでも `#` `@` `.` を含む名前を二重引用符で `CREATE ROLE` できることを
    ローカルで実測確認した（`"nom40hiro21_...#EXT#@...onmicrosoft.com"`）。
    実際のEntra側の `pgaadauth_create_principal` 呼び出し自体は Azure 実機での
    確認が必要（デプロイ時チェックリストに記載）
  - `AZURE_PRINCIPAL_ID` が azd から供給されるかは、Microsoft Learn の
    「Environment variables FAQ」で `Determined automatically during provisioning`
    と明記されているのを確認した（実機でのライブ確認は未実施。controller が
    デプロイ時に確認する）
- **デプロイで確認した項目（controller が実施。すべて確認済み）**:
  - UAMI が Entra 管理者でないこと
  - `preprovision` が `AZURE_PRINCIPAL_NAME` を正しく解決すること（運用者の UPN。
    `#EXT#` を含む形式であることも含めて実際の値を確認する）
  - `postdeploy` の中でマイグレーションが成功すること（`bootstrap-db.py pre` →
    `alembic upgrade head` → `bootstrap-db.py post`）
  - API が DML で正常に動くこと（publish → submit → approve → SPARQL）
  - **アプリのロール（UAMI）で `DELETE FROM audit_events` が失敗すること**
    ← 最重要
  - **アプリのロールで `DROP TABLE` が失敗すること**
  - ファイアウォール規則が処理の最後に残っていないこと
  - **既存環境への再デプロイでは、古い UAMI の Entra 管理者登録が ARM の
    incremental デプロイでは削除されない**可能性がある（子リソースが
    テンプレートから外れても自動削除されないため）。既存環境で確認する場合は
    `az postgres flexible-server microsoft-entra-admin list` で古い登録が
    残っていないか確認し、残っていれば手動で削除するか、環境を作り直す
  - `pgaadauth_create_principal('<UAMI名>', false, false)` が成功し、API が
    実際に接続できること。名前ベースの登録が失敗する場合は
    `pgaadauth_create_principal_with_oid` へのフォールバックを検討する
- **Azure 実機の検証結果（2026-09-06、`rg-p1-privsep`。検証後 `azd down --purge`）**:
  ```
  Entra 管理者   : nom40hiro21_..._outlook.onm (User, oid fa7120dd-...)
  UAMI          : id-qlx37mkenwuwc (principalId d4c1e181-...)  ← 管理者に含まれない
  テーブル所有者 : alembic_version / audit_events / namespaces / ontology_versions
                  すべて ontology_owner
  アプリのロールの権限（has_table_privilege）:
    audit_events       SELECT=Y INSERT=Y UPDATE=Y DELETE=n TRUNCATE=n
    その他3テーブル     SELECT=Y INSERT=Y UPDATE=Y DELETE=Y TRUNCATE=n
    public.CREATE=n / pg_has_role(ontology_owner)=false
  SET ROLE してアプリのロールとして実行した結果:
    SELECT audit_events            -> 3 行（成功）
    DELETE FROM audit_events       -> permission denied for table audit_events
    TRUNCATE audit_events          -> permission denied for table audit_events
    DROP TABLE audit_events        -> must be owner of table audit_events
    CREATE TABLE                   -> permission denied for schema public
    ALTER TABLE audit_events       -> must be owner of table audit_events
  postdeploy: bootstrap-db pre → alembic upgrade head（03fff5e0d815）→ post
              → ファイアウォール規則削除 → publish 201 / submit 200 / approve 200
              → GET /namespaces に retail-core が現れる
  API: SPARQL（GRAPH 句なし）-> 60 トリプル、owl:Class 4 件
       版の状態 = approved、projected_at あり
       INSERT DATA -> 400（読み取り専用）、未認証 -> 401
  ファイアウォール規則: AllowAllAzureServicesAndResources のみ（一時規則は残らず）
  ```
  **`#EXT#` を含む 75 文字の UPN でも接続できた。** PostgreSQL 側のロール名は
  63 文字に切り詰められる（`...outlook.onm` で終わる）が、`asyncpg.connect(user=<完全なUPN>)`
  は Azure 側の認証で解決されるため、呼び出し側で切り詰める必要は**無い**
  （事前に切り詰めが必要かを懸念していたが、実測で不要と判明した）
- **見つけたが直していない問題（範囲外のため。出典: この実装作業中の調査）**:
  1. `infra/main.parameters.json` は `principalType` を `AZURE_PRINCIPAL_TYPE`
     に結び付けていない（Bicep 側は `principalType` パラメータを持つが既定値
     `'User'` に固定されたまま）。`AZURE_PRINCIPAL_ID` と同様 `AZURE_PRINCIPAL_TYPE`
     も azd が provision 時に自動的に解決する環境変数だが、これを使っていない
     のは今回の変更前からの既存不備。**この変更で影響が広がった**: CI がサービス
     プリンシパルでデプロイすると、PostgreSQL の Entra 管理者登録が実際の型
     ではなく `User` として作られてしまう
  2. README.md の「初回デプロイ時の注意」に「サンプルオントロジーの投入は
     `postprovision` フックが自動で行います（`scripts/postprovision.sh` /
     `scripts/postprovision.ps1`）」という記述が残っている。実際には `P1-10` で
     `postdeploy` に移行済み・ファイル名も `postdeploy.{sh,ps1}` に変わっており、
     同じ箇条書きの後半はそれを正しく説明しているため内容として矛盾している
  3. `infra/modules/shared.bicep` の `output identityPrincipalId` が、この変更で
     参照元（`postgres.bicep` の旧 `entraAdministrator`)を失い未使用になった
  4. この開発環境に `shellcheck` が入っておらず、`scripts/*.sh` の shellcheck は
     実行できなかった。代わりに `sh -n`(構文チェックのみ)と、`.ps1` は
     `[System.Management.Automation.Language.Parser]::ParseFile`(構文チェックの
     み)で検証した

### `P1-12` MCP → Core API のトークン伝播

- **状態**: **完了**（2026-09-06。[ADR-0012](adr/0012-mcp-to-core-api-authentication.md)）
- **優先**: 中
- **内容**: MCP サーバが Core API を呼ぶ際の認証
- **着手時に判明した実態（「未設計」より悪かった）**: MCP は Core API を
  **認証ヘッダなしで**呼んでいた。つまり `AUTH_MODE=entra` のデプロイ環境では
  **ツール呼び出しがすべて 401 になる**。`tools/list` は MCP サーバ自身が
  返すため成功するので、外からは「MCP は動いている」ように見えていた。
  **README はこれを「Phase 1 で動作するもの」に挙げていた**（同じコミットで修正）
- **決定（ADR-0012）**: MCP と Core API が**同一のオーディエンス**（同一アプリ登録）を
  共有し、MCP は受信トークンを自分で検証してから `Authorization` を**そのまま転送**する
  - **マネージド ID で呼ぶ案を却下した理由**: Core API から見た呼び出し元が常に
    MCP になり、監査の `actor` が実際のエージェントを指さなくなる。
    ADR-0006 が中核価値としている帰属が壊れる。**機能が動くことと引き換えに
    製品の中核価値を捨てる交換**になる
  - OBO も却下（機密クライアント資格情報が必要。このリポジトリはシークレットを
    持たない方針を通してきた）。将来必要になったらマネージド ID の
    Federated Identity Credential 経由の OBO が経路
  - MCP 仕様の「サーバー宛てでないトークンの転送禁止」に抵触しない根拠を
    ADR に先回りで書いた（同一オーディエンスであり、MCP 自身も検証する）
- **実装中に見つけた別のバグ（同じコミットで直した）**: **MCP SDK は
  `ToolError` 以外の例外のメッセージを隠す。** `ValueError` を投げると
  エージェントに届くのは `Error executing tool <name>` だけで理由が失われる
  （実測で確認）。既存のクエリガード（`ensure_agent_safe_query` の拒否）も
  `ValueError` だったため、**「なぜ拒否されたか」がエージェントに届いて
  いなかった**。意図的な拒否はすべて `ToolError` に変えた
- **検証**:
  - `packages/mcp-server/tests/`（**このパッケージには従来テストが 0 件だった**。
    `testpaths` に入っているのにディレクトリが無かった）に 11 件。
    **変異テストで有効性を確認済み**（転送を外すと落ちる）
  - **`scripts/verify-mcp-auth.sh`（新規・手動）で実トークンの E2E を通した。**
    API と MCP をローカルで `AUTH_MODE=entra` 起動し、`az account get-access-token`
    の実トークンで確認（**費用は発生しない**。使うのは既存のアプリ登録と
    自分のログインだけ）。結果: トークン無し→理由付きで拒否 /
    壊れたトークン→MCP 側の検証で拒否 / 実トークン→Core API まで通る
  - pytest にしていないのは `az` ログインに依存するため。conftest の
    「静かにスキップしない」方針と衝突させない
- **範囲外にしたもの**: MCP 認可仕様のフルセット（Protected Resource Metadata、
  `WWW-Authenticate` によるディスカバリ）。ADR-0012 の未解決事項に記載
- **依存**: `P1-09`

### `P1-13` 基準バージョンによる lost update の検出

- **状態**: **完了**（2026-09-06。`base_version` を受け取り最新と不一致なら 409）
- **優先**: 中
- **内容**: `PublishRequest` は `turtle` と `version` だけで基準バージョンを渡す口が無い。2 人が同じ版から編集して公開すると、後の版に前の変更が含まれない。検出も警告もされない
- **完了条件**: 基準バージョンを受け取り、最新と一致しなければ 409 を返す。同時編集のテストを追加
- **実装した内容（2026-09-06）**: `PublishRequest.base_version`（任意）を追加し、
  `ProjectionService.publish` が最新版と比較する。不一致なら
  `ConcurrentUpdateError` → ルータで 409
  - **検査の位置が本質**（コメントに残した）:
    - **内容ハッシュによる冪等判定より後**に置く。前に置くと、タイムアウト後の
      再送（同じ本文・同じ `base_version`）が「最新が進んでいる」として 409 に
      なってしまう。これは競合ではなく再送なので弾いてはいけない
    - **正本への書き込みより前**に置く。後に置くと、弾く前に Blob へ書いてしまう
  - **任意にした理由**: 最初の公開には基準が無く、`postdeploy` のような自動投入も
    基準を持たない。README に「人が編集する経路では必ず渡すこと」と、
    渡さない場合に何が起きるかを書いた
  - `latest_for` の呼び出しは自動採番と共用にした（クエリを増やさない）
  - テスト6件（すべて修正前に落ちることを確認済み）: 一致すれば成功 /
    **古い基準は拒否**（本題）/ 省略時は検査しない（後方互換）/
    1 版も無いのに基準を主張したら拒否 / **同一内容の再送は冪等のまま** /
    ルータで 409 になり Blob・PostgreSQL が増えないこと
- **出典**: 2026-09-01 の実装調査（`base_version` / `If-Match` 相当が 0 件）

### `P1-S2` スパイク②: ACA の idle/active 課金比率の実測

- **状態**: **完了**（2026-09-09。**メトリクスから判定条件を再構成する方法で解決**）
- **優先**: 中
- **内容**: idle 単価は active の 1/8。Fuseki が常時 active と判定されると 1 vCPU 構成で月 $105 前後まで上振れする。費用試算の最大の不確実性
- **完了条件**: 実測値を `docs/cost-estimate.md` に反映
- **結論（2026-09-09）**: **Fuseki は待機時に idle 単価の条件を満たす。**
  最も厳しい CPU の条件（0.01 vCPU = 10,000,000 ナノコア未満）に対して
  **約 4 倍の余裕**があり、懸念していた「8 倍に上振れして月 $105 前後」は
  **起きない**。`docs/cost-estimate.md` の idle 課金前提はそのまま有効
- **測定方法を変えた（ここが本題）**: 「数時間〜数日デプロイを維持する」という
  当初の想定では**測れない**ことが分かった
  - **無料枠を使い切るまで課金行に単価が乗らない。** 0.5 vCPU / 1 GiB なら
    100 時間、1 vCPU / 2 GiB でも 50 時間かかる
  - これに Cost Management の反映遅延（EA/MCA で 8〜24 時間、従量課金で
    最大 72 時間）が乗る。**稼働 2〜5 日 + 待ち 1〜3 日**で、しかも結果は
    `azd down --purge` の後に届く
  - 一方 **idle の判定条件は公開されていて、Azure Monitor のメトリクスで
    そのまま再構成できる**（`Replicas` / `Requests` / `UsageNanoCores` /
    `RxBytes`）。**費用ほぼゼロ・即日**で同じ問いに答えられる。こちらに切り替えた
  - 実測データと限界（PT1M 粒度 vs 秒単位の課金判定、1 vCPU 構成は未計測）は
    `docs/cost-estimate.md` に記載した
- **副産物の確認事項**:
  - **メモリは idle と active が同単価。** idle 割引は vCPU にしか効かない
    （Retail Prices API で 3 リージョン一致を確認）。`cost-estimate.md` が
    メモリを単一単価で扱っていたのは結果的に正しかった
  - **API / MCP の scale-to-zero が実測で確認できた。** 最後のトラフィックから
    MCP は約 1 分、API は約 5 分で 0 レプリカに落ちた（0 レプリカは課金ゼロ）
  - **環境レベルのメーターは乗っていない。** `Environment Management Hour` 等は
    各 $0.145/時 ≒ 月 $105 だが、minimal 構成は Consumption のみ・VNet なし・
    計画メンテナンスなしで該当しないことを確認した。**`production`（Phase 4）
    では乗る**ため、概算に注記を追加した
  - **Liveness プローブ（30 秒周期）は idle 判定を壊さない。** `RxBytes` の
    約 200 B/s はほぼプローブによるものだが、閾値 1,000 B/s の 1/4 以下
- **注意**: 数時間〜数日デプロイを維持する必要があり、費用が発生する。実施前に確認を取る
  （↑ この前提は上記のとおり**不要になった**。メトリクス経路なら短時間で済む）

### `P1-S3` スパイク③: Ontop 配布物のライセンス確認

- **状態**: **完了**（2026-09-06。`docs/third-party-licenses.md` に結論を記載）
- **優先**: 低
- **内容**: Ontop 本体は Apache-2.0 だが、配布イメージに同梱される JDBC ドライバは別ライセンスの可能性がある
- **完了条件**: `docs/third-party-licenses.md` に結論を記載
- **結論（2026-09-06、一次情報で確認）**: **当初懸念していたリスクは存在しなかった。**
  公式の Ontop イメージには **JDBC ドライバが一切同梱されていない**（公式
  チュートリアルが利用者側で `jdbc/` を用意してマウントすることを求めている）。
  Ontop 本体は Apache-2.0
  - 論点は「自前イメージに何を入れるか」に移った。**同梱してよい**のは
    pgjdbc（BSD-2-Clause）と mssql-jdbc（MIT）。**同梱しない**のは
    MySQL Connector/J（GPL-2.0 with FOSS exception。例外条項の解釈に
    依存する形で再配布するリスクを取る必要がない）と Oracle JDBC
    （独自条項で再配布が許諾されていない）
  - **`/opt/ontop/jdbc` をマウント可能なまま保つ**ことで、同梱しない
    ドライバも利用者が実行時に置ける。「私たちが再配布しないこと」と
    「利用者が使えないこと」は別である
- **出典**: 当初計画の R6

### `P1-14` POSIX 経路での `azd up` の通し確認

- **状態**: **完了**（2026-09-09。POSIX で実機に対して `postdeploy.sh` を通し、**実バグ 2 件と CI の穴 1 件**を発見して修正）
- **優先**: 低
- **内容**: 実機検証は Windows（pwsh）経路のみ。POSIX 経路で `azd up` を通した人がいない。`scripts/postprovision.sh` の実行ビット欠落は修正済みだが、それは既知のブロッカーを除去したにすぎない
- **やったこと（2026-09-09）**: Linux コンテナ（`mcr.microsoft.com/azure-cli` +
  uv）にリポジトリをマウントし、**実際にデプロイ済みの Azure 環境に対して**
  `scripts/postdeploy.sh` を実行した。結果は終了コード 0
  - 実物を叩いた部分: uv による Linux 上の venv 構築と依存解決 /
    `bootstrap-db.py pre`・`post` → 実 PostgreSQL（冪等。ロール既存を検出）/
    `alembic upgrade head` を `MIGRATION_ROLE=ontology_owner` で → 実 PostgreSQL
    （アドバイザリロックの取得・解放も含む）/ Core API への publish・submit・
    approve と発見確認 → 実 API（201/409/409。**繰り返し実行しても失敗しない**
    ことも同時に確認）
  - **`az` だけはスタブにした。** Windows の Azure CLI のトークンキャッシュは
    DPAPI で暗号化されていて（`msal_token_cache.bin` の先頭が
    `01 00 00 00 D0 8C 9D DF`）**Linux から復号できない**ため。az が返すはずの
    値（ARM 操作の成否とアクセストークン）は Windows 側で取得したものを注入した。
    ファイアウォール規則は Windows 側で先に開けた
  - **したがって「POSIX で `azd up` を通した」とは言えない。** azd 自身の
    provision / deploy の実行は Windows 上である（Docker Desktop の WSL 統合が
    無効で、有効化には GUI 操作と Docker の再起動が必要だったため踏み込まなかった）。
    **OS 依存のロジックはすべてフックスクリプトの中にあり、そこは POSIX で
    実行して確認した**という位置づけ。完全な POSIX `azd up` には対話的な
    `az login` が 1 回必要
- **見つけた不具合 1（修正済み）: `postdeploy.sh` が素の `python` を呼んでいた**
  - **多くの現代的な Linux には `python` が無く `python3` しかない**（Python 3 が
    既定になった時点で各ディストリが無印の `python` の提供をやめた）。
    実測で Azure Linux 3.0 には無く、`python: command not found` になった
  - 該当は 1 箇所（サンプル投入時の JSON 組み立て）。他はすべて `uv run python`
    だったため、**同じ前提に揃える**方針で `uv run python -c` にした
    （`python3` に変えるのではなく。uv が動くなら必ず動く）
  - **Windows 経路では起きない**（PowerShell 版は `Get-Content` で読む）。
    「1 つの経路で確認して両方に一般化する」失敗の実例そのもの
- **見つけた不具合 2（修正済み）: `preprovision` が認証の失敗を型の問題として誤報していた**
  - `az ad signed-in-user show` の失敗理由を `2>/dev/null` で捨てて
    「サービスプリンシパルとして解決します」と表示していた。しかしこのコマンドは
    **`az login` の期限切れやトークンキャッシュが読めない等でも失敗する**。
    検証中に実際にこの誤報を踏んだ（真の理由は
    `User '...' does not exist in MSAL token cache. Run 'az login'.`）
  - 両方の経路の標準エラーを保持し、どちらも失敗したときに
    「型の問題ではなく認証の問題である可能性が高い」と添えて両方出すようにした。
    `.sh` / `.ps1` の両方を直し、Linux で誤報が直っていることと正常系
    （`#EXT#` を含むゲスト UPN の解決）の両方を実行して確認した
- **見つけた穴 3（修正済み）: `scripts/*.sh` が CI で一度も検査されていなかった**
  - `shell` ジョブは `containers` フィルタで起動し、検査対象も
    `containers/fuseki/*.sh` だけだった。**`scripts/*.sh` だけを変更しても
    shellcheck が走らなかった**ため、不具合 1 が CI をすり抜けていた
  - `shell` フィルタ（`scripts/**` + `containers/**`）を新設し、shellcheck の
    対象に `scripts/*.sh` を追加した
  - あわせて **`scripts/lint-shell.sh`（新規）** を作った。shellcheck は素の
    `python` 呼び出しを検出しない（構文としては正しい）ため、機械的な検査として
    残す。**変異テストで有効性を確認済み**（`uv run python` を `python` に
    戻すと落ちる）
- **副産物の罠 2 件**（`CLAUDE.md` に追記）: コメント行を静的解析ツールの名前だけで
  始めると、ツール自身がディレクティブとして解釈して失敗する（SC1072/SC1073。
  2 回踏んだ）/ Windows の Azure CLI のトークンキャッシュは DPAPI 暗号化で
  Linux から使えない
- **見つけたが直していない（範囲外）**: 同一内容の再 publish が **201 Created** を
  返す。冪等な扱い（既存の版をそのまま返す）としては正しいが、既存リソースに
  対して 201 は HTTP の意味としてはずれている。`P1-26` に記録した
- **出典**: Task 8 レビュー I-3。「1 つの経路で確認して両方に一般化する」失敗を避けるため明示

---

### `P1-24` `identityPrincipalId` の出力が未使用になった

- **状態**: **完了**（2026-09-06。未使用の output を削除した）
- **優先**: 低
- **内容**: ADR-0011 で Entra 管理者を UAMI からデプロイ実行者に変えたため、
  `infra/modules/shared.bicep` の `output identityPrincipalId` が未使用になった。
  害は無いが、使われていない出力は誤解を招く
- **出典**: 2026-09-06 の実装者の報告（範囲外として直していない）

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

### `P1-25` reconcile が欠落した名前付きグラフを自動復旧するか

- **状態**: **完了**（2026-09-09。[ADR-0013](adr/0013-reconcile-repairs-observed-divergence.md)）
- **優先**: 低
- **Phase**: 1（`P1-19` の実装中に分離）
- **内容**: `P1-19` で `reconcile` が「射影されているべきなのにストアに無い
  名前付きグラフ」を `missing_graphs` として**報告**するようにしたが、
  復旧はしていない。Blob には TTL があるので技術的には再射影できる
- **判断が必要な点**（これがあるので `P1-19` から分離した）:
  1. **`projected_at` が非 NULL の版を再射影してよいか。** 現在の `reconcile` は
     `unprojected()`（`projected_at IS NULL`）だけを射影対象にしている。
     この境界を広げると「一度射影に成功した版」の意味が変わる
  2. **`SUPERSEDED_RETAIN` との関係。** 欠落の検出からは `superseded` を
     除いているが、復旧を入れるなら「ローダが意図的に読み込まなかった版を
     reconcile が読み込み直してしまう」問題に正面から答える必要がある
  3. **自動復旧が根本原因を隠さないか。** ローダがスキップした本当の理由
     （マニフェストの欠落・破損）に運用者が気づかなくなる恐れがある。
     報告と復旧の両方を行う形が妥当だと思われるが、決めていない
- **完了条件**: 上記3点を ADR で決め、決めた方針を実装する
- **決めたこと（[ADR-0013](adr/0013-reconcile-repairs-observed-divergence.md)、2026-09-09）**:
  1. **論点1 は問いの立て方が悪かった。** `projected_at` に「書き込み経路が完了した」と
     「ストアが今それを保持している」の 2 つの意味を混同させていた。分離すると
     問いは「**ストアが持っていないと観測された版を直してよいか**」に変わり、
     答えは自明に「よい」になる（ストアは再構築可能な派生物。ADR-0002）。
     `unprojected()` の境界は広げず、**観測を根拠にグラフ単位の突き合わせが修復する**。
     証拠は 2 種類、修復の実装は 1 つ（`_project_named_graph` / `_project_default_graph`）
  2. **論点2: `superseded` は reconcile の契約外にした。** 欠落を報告も修復もせず、
     存在していても残留として削除しない。**在否をどちらも不問**にすることで、
     ストアに載せるかの決定をローダ 1 箇所に保つ。reconcile 側に
     `SUPERSEDED_RETAIN` を持たせる案は、同一の決定を 2 箇所で設定することになり
     却下した（`P1-22` で一度解いた問題）
  3. **論点3: reconcile は背景で回っていない**（`POST /admin/reconcile` からのみ。
     今後も回さない）。運用者が明示的に叩いたときだけ動くので「勝手に直った」は
     起きない。その上で **`missing_graphs` は修復しても消さず**、修復できたものを
     `graphs_repaired` として別に報告する。**これが空でないことは成功報告ではなく
     上流に問題があるという信号**である
- **起草中に見つけた 4 つめの論点（同じ ADR で決めた）**: **既定グラフの欠落は
  `list_graphs` では見えない。** あれは名前付きグラフの IRI しか返さない
  （SPARQL に既定グラフを名前で列挙する仕組みが無い）。ところが**エージェントが
  読むのは既定グラフ**（`GRAPH` 句なしのクエリ。ADR-0010 決定6）なので、
  「名前付きグラフは揃っているのに既定グラフが空」は最も害が大きいのに検出に
  一切映っていなかった。`SparqlStore.has_default_graph_content` を追加して
  別に確認・修復するようにした。**内容の食い違い（別の版が載っている）は
  検出しない**（正規化を伴う比較が必要。ADR-0013 の未解決事項）
- **テスト**: 9 件追加（`uv run pytest` 163 → **172 件**）。FakeStore に対する
  8 件は修正前に落ちることを確認済み。うち 1 件（存在する `superseded` を
  削除しない）は既存挙動の回帰テストとして通ることを確認した。
  **実 Fuseki に対する 1 件**では、データセットを作り直して中身を失わせてから
  reconcile し、**`GRAPH` 句なしのクエリが再び答えを返す**ところまで確認した
- **既存テストを 1 件更新した**: `P1-19` のテストが `missing_graphs` の要素数 1 を
  期待していたが、決定4 で既定グラフも報告するようになったため 2 件になった。
  挙動の変更であり回帰ではない
- **出典**: 2026-09-06 の `P1-19` 実装時に分離
- **関連**: `P1-19`、`P1-17`、[ADR-0010 補記1](adr/0010-approval-and-projection.md)

### `P1-26` 同一内容の再 publish が 201 Created を返す

- **状態**: **完了**（2026-09-09）
- **優先**: 低
- **Phase**: 1（`P1-14` の検証中に発見）
- **内容**: `POST /namespaces/{ns}/versions` に同一内容（`content_hash` が一致）を
  再投入すると、`ProjectionService.publish` は既存の版をそのまま返す（冪等。
  意図した設計）。しかしルータの `status_code` が `201` に固定されているため、
  **新規作成していないのに 201 Created が返る**
- **なぜ気づいたか**: POSIX 経路で `postdeploy.sh` を 2 回目として実行したとき、
  名前空間は 409、submit / approve は 409 なのに publish だけ 201 だった
- **実害**: 小さい。クライアントが 201 を「新しい版ができた」と解釈すると、
  版番号を取り違える余地がある（応答本文には正しい既存の版が入っている）
- **完了条件**: 冪等な一致のときは 200 を返す（新規作成のときだけ 201）。
  FastAPI では `response.status_code` を動的に設定するか、`Response` を
  注入して書き換える。`postdeploy.{sh,ps1}` の期待コードも合わせる
- **実装した内容（2026-09-09）**: サービス層に `PublishOutcome`（`CREATED` /
  `REUSED`）と `publish_with_outcome` を追加し、ルータが結果に応じて
  200 / 201 を設定する
  - **`publish` の戻り値の型は変えなかった。** `publish` の呼び出しは
    テストを含め 58 箇所あり、HTTP のステータスコードを知る必要があるのは
    ルータだけなので、`publish` を `publish_with_outcome` の薄いラッパとして
    残した（既存の呼び出しを 1 つも壊していない）
  - **競合に負けた側も `REUSED` にした。** 一意制約の競合から回復した経路では
    自分はその版を作っていない。勝った側が 201、負けた側が 200 を受け取る
  - **ハンドラで 200 と 201 の両方を明示的に設定した。** ルートの
    `status_code=201` は OpenAPI の既定値として残すが、実際のコードはハンドラで
    決める。そうしないとハンドラの契約がフレームワークの既定に依存し、
    関数を直接呼ぶ既存のテスト方式で検証できない
  - `postdeploy.{sh,ps1}` の期待コードに 200 を追加し、README にも明記した
  - テスト2件（修正前に落ちることを確認済み）: 同一内容の再投入で 200 かつ
    版が増えないこと / 内容が変われば 201 のままであること
- **出典**: 2026-09-09 の `P1-14` 実機検証

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
