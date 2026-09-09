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

**Phase 1 は全 22 件が完了した（2026-09-09）。** 現在は Phase 2 を進めている。

Phase 2 で完了したもの: `P2A-05`（SHACL 検証）/ `P2A-06`（名前空間 RBAC と四眼原則）/ `P2A-09`（`platform-admin` の設定の自動化。**実機確認は未了**）/ `P2B-03`（廃止のライフサイクル）/ `P2B-04`（用語単位の責任者）/ `P2B-07`（想定質問の CI 実行）/ `P2B-08`（`reason` の記録と参照）/ `P2B-09`（意味的差分の計算）/ `P2B-11`（監査を読み出す API）。

**次に着手する候補**（費用が発生しないものを優先順に）:

| ID | 内容 | なぜ次か |
|---|---|---|
| `P2B-05` | アクセスログの実装 | 健全性指標（`P2B-06`）の 6 項目のうち複数がこれに依存する。ADR-0006 §4 の「エージェントに何を返したか」。**Phase 2 柱 B で残っている最も大きい前提** |
| `P2B-02` | 保持ポリシーの実装 | ADR-0006 の補記が「Phase 2 の必須項目」としているが未着手。`P1-C1` の応急処置（既定グラフは常に単一の版）は入っているが、ストアに載せる版の制御そのものは無い |
| `P2B-06` | 健全性指標の集計と提示 | `P2B-04` で「責任者が未設定の用語」、`P2B-09` で「SHACL 違反」と「削除された IRI」、`P2B-11` で監査の照会ができた。残りは `P2B-05` に依存する |
| `P2B-14` | 想定質問をデプロイ済みの名前空間に紐づける | `P2B-07` はリポジトリ内の質問を CI で回すところまで。承認をブロックするかの判断が要る |

**費用が発生するもの**（ユーザーの確認が必要）: `P2A-09` の**実機確認**。自動化（`just setup-app-role` と `preprovision` のゲート）は 2026-09-09 に完了し、ローカルでは 416 件のテストで検証済みだが、**実 Entra テナントに対しては一度も実行していない** — アプリロールの定義（Graph への PATCH）、割り当て、実トークンの `roles` クレーム、`platform-admin` を持たない主体が 403 になること、`platform-admin` が四眼原則を飛び越えられないことは、いずれもデプロイ窓が必要。

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
- **Phase 1 の制約**（**`P2A-06` で解消。2026-09-09**）: 実装当時は名前空間 RBAC が
  未実装で、**認証済みの呼び出し元が誰でも承認できた**。`approved_by` の記録は正しいが
  強制が無い状態で、README にもそう明記していた。現在は `approve` / `reject` に
  `maintainer` 以上を要求し、四眼原則も既定で有効である（[ADR-0014](adr/0014-namespace-rbac.md)）。
  責任者（`P2B-04`）は引き続き未実装
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
  - ~~四眼原則（提案者と承認者を別人にする。設定で無効化可能）~~ → **`P2A-06` で完了**（2026-09-09。`namespaces.require_two_person_approval`、既定有効）
  - ~~名前空間 RBAC との統合~~ → **`P2A-06` で完了**（`approve` / `reject` は `maintainer` 以上）
  - **残り**: 責任者のみが `approve` できる。`P2B-04` は 2026-09-10 に完了したが、**これを承認の条件にするかは別の判断として残っている**。RBAC の `maintainer` は「承認してよい人」、責任者は「その用語について答えるべき人」で層が違う。版は複数の用語を含みうるため、「その版が触る全用語の責任者の承認を要求する」のか「1 人でよい」のかを決める必要がある（決めていない）
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
| `P2A-05` | pyshacl による SHACL 検証 | 高 | **完了**（2026-09-09） |
| `P2A-06` | 名前空間 RBAC の強制 | 高 | **完了**（2026-09-09） |
| `P2A-07` | 監査証跡の PROV-O 表現 | 中 | 未着手 |
| `P2A-08` | `SPARQL_MAX_RESULTS` の強制 | 中 | 未着手 |
| `P2A-09` | `platform-admin` アプリロールの設定を自動化する | 高 | **自動化は完了**（2026-09-09。**実機確認は未了**） |
| `P2A-10` | 既存スクリプトの日本語出力が Windows で cp932 になる | 低 | 未着手 |
| `P2A-11` | パスパラメータ名が `{namespace}` と `{name}` で揺れている | 低 | 未着手 |
| `P2A-12` | ドキュメントだけの push が、コードを検証中の CI を打ち消す | 中 | **完了**（2026-09-10） |

`P2A-06` の補足（**完了。2026-09-09**）: 設計原則「名前空間 x ロールは PostgreSQL で管理し API で強制する」の実装。根拠は [ADR-0014](adr/0014-namespace-rbac.md)。

**Phase 1 の「記録は正しく、強制が無い」状態を終わらせた。** ADR-0010 は「機構を先に作り、権限を後から締めるのは順序として妥当（逆はできない）」としてこれを意図的に受け入れていた。その後半をここで実行した。

| 操作 | 必要なロール |
|---|---|
| SPARQL 読み取り / 版の一覧 / 決定記録 / SHACL 検証 | `data-analyst` |
| `publish` / `submit` | `data-steward` |
| `approve` / `reject` | `maintainer` |
| 名前空間の削除 / ロールの付与・取り消し | `owner` |
| 名前空間の作成 / `POST /admin/reconcile` | `platform-admin`（Entra アプリロール） |

**設計上の要点**:

- **付与が 1 件も無い名前空間は「誰も権限を持たない」。** 「付与が無ければ全員に許可」のような暗黙のフォールバックは作らない。それは**「強制していない」を「強制している」と誤認させる**、この製品で最も避けたい形である（ADR-0014 決定6）。既存デプロイの移行はマイグレーション `0002_namespace_rbac` が `namespaces.created_by` から `owner` を 1 件付与する。**実 PostgreSQL で backfill を確認した**（旧スキーマで作った行に `owner` が付き、`require_two_person_approval` が `false` になる。`downgrade base` → `upgrade head` の往復も確認）
- **`reject` は `maintainer`。** 却下は「この提案を現行にしない」という決定であり、承認と同じ重みの判断である。提案者が取り下げたいなら新しい版を出せばよい（オントロジーは不変リビジョン）
- **四眼原則は名前空間ごとの設定。既定は有効。** 有効なら**その版を `publish` した主体は `approve` できない**。提案者を `publish` 側で判定したのは、`submit` が「レビューに出す」という事務的な操作でありうる（他人の draft を代わりに submit する）ため
- **`platform-admin` は名前空間の権限を飛び越えるが、四眼原則は飛び越えない**（決定5）。管理者が自分の提案を自分で承認できてしまうと、四眼原則が「管理者以外への制約」に成り下がり、規制対応の文脈で意味を失う
- **`PermissionDeniedError`（403）と `TwoPersonApprovalError`（409）を分けた。** 運用者が取るべき対処が違う。権限不足はロールを付与すれば解決するが、四眼原則違反は「別の人に承認してもらう」しかない。同じ 403 に混ぜると、ロールを足して解決しようとして解決しない
- **`principal_id` は Entra のオブジェクト ID。** UPN や表示名は変わりうるし、ゲストの `#EXT#` 形式は書き換えの罠がある（`P1-11` で実際に踏んだ）
- **DB に未知のロール文字列が入っていたら権限なしとして扱う。** 「読めない付与」を「上位ロール」と誤解すると権限が広がる
- **最後の `owner` は取り消せない**（409）。取り消せると誰もその名前空間を管理できなくなる
- **同梱サンプルの名前空間だけ `require_two_person_approval: false` で作る。** `postdeploy` が明示的にそう指定する。**「デモのために既定を緩める」のではなく「デモの名前空間だけを緩める」**（`azd up` は 1 主体で publish → submit → approve するため、既定のままでは Phase 1 の完了条件が壊れる）

**実装中に踏んだこと**: RBAC と四眼原則を入れた瞬間に既存のルータテスト 5 件が `TwoPersonApprovalError` で落ちた（**機能が設計どおり動いた結果**）。承認者用の主体を分けて `created_by != approved_by` を検証する形に直した。mypy strict では `session.execute()` の戻り値に `rowcount` が無い（`CursorResult` にしか無い）ので、`revoke` は存在確認してから削除する形にした。

**見つけたが直していない（`P2A-09` に分離）**: `platform-admin` は Entra アプリ登録の `appRoles` で定義してトークンの `roles` クレームで届くが、**その定義と割り当てが自動化されていない。** アプリ登録は `azd` の管理外（`azd down` でも消えない永続的な成果物、`P1-09`）であり、Bicep からは作れない。そのため **`azd up` の `postdeploy` は、運用者に `platform-admin` が割り当てられていないと名前空間の作成で 403 になって止まる。** README に手順（`az ad app update` と `appRoleAssignments`）を書き、`postdeploy` が 403 のときに原因を言い切るようにしたが、**設定そのものは手作業のままである。**

**実環境での確認は未実施。** ローカルでは 243 件のテストで検証しているが、Entra の実トークンに `roles` クレームが乗ること、`platform-admin` を持たない主体が実際に 403 になることは実機で確認していない。費用の出るデプロイ窓が必要（`P2A-09` の完了条件に含める）。

テストは 218 → **243 件**。

`P2A-05` の補足（**完了。2026-09-09**）: ADR-0005 決定1「SHACL 検証は pyshacl で完結させる」の実装。Java 依存は Phase 4 まで発生しない。

**2 段階で検証する。段階 1 の価値は実測で確かめた。**

| 壊し方 | 段階1（SHACL-SHACL） | 段階2（データ検証） |
|---|---|---|
| `sh:minCount "いち"`（型違い） | 違反として報告 | pyshacl が読み込み時に例外 |
| `sh:property` に `sh:path` が無い | 違反として報告 | pyshacl が実行時に例外 |
| **`sh:targetClass "C"`（リテラル）** | **違反として報告** | **適合（何も検出しない）** |
| `sh:minCoun 1`（制約名のタイプミス） | 適合 | 適合 |

**段階 1 だけが捕まえるのは 3 行目である。** 対象クラスがリテラルを指している shape はどのノードにも当たらないので、データ検証は「違反ゼロ」を返す。制約を書いたつもりが何も検査していない状態で、このリポジトリが繰り返し戦っている「静かに間違う」型の問題そのもの。1・2 行目は段階 2 では**例外**になり（「違反が見つかった」ではなく「検証できなかった」）違反箇所が失われるので、**段階 1 が落ちたら段階 2 は実行しない**。

**4 行目は両方とも検出できない（既知の限界）。** SHACL の処理系は知らない述語を単に無視する仕様なので、制約名のタイプミスは「制約が 1 つも書かれていない shape」と区別がつかない。**検出できないことをテストとして固定した**（将来 SHACL 側に仕組みが入ったらそのテストが落ちて気づける）。

**どこで止めるか**:

- `publish` では止めない。`draft` は編集途中でありうる（ADR-0010 決定1 が publish と approve を分離したのはこのため）
- `approve` で止める（**422**）。ADR-0009 決定1「形式的に決定可能なものはブロッキング」。SHACL 適合性は形式的に決定可能。**検証は状態遷移より前**に置いた（承認後の検証は意味が無い。TTL の構文検証を Blob 書き込みの前に置いているのと同じ理由）
- `POST /namespaces/{ns}/versions/{v}/validate` は**状態を変えずに報告する**。ADR-0005 の「専門家がレビューする画面で『この提案は制約に違反しています』と即座に示す」ための口
- **「検証できなかった」を違反として扱わない。** Blob へ到達できない・pyshacl が実行時エラーの場合は **502** にする。「制約を満たしている」と「確かめられなかった」を混同すると壊れた定義を承認してしまう

**実装中に自分で作った不具合**: エンドポイント関数を `validate_version` と命名したところ、同じモジュールが入口検証に使っている `ontology_core.graphs.validate_version` を**上書きして**しまい、他のハンドラの版検証が黙って効かなくなった（既存テスト 4 件が落ちて発覚）。`validate_version_shacl` に改名し、**同じ間違いを機械的に防ぐテスト**を追加した。

**何を検証しないか**: 外部の実データへの適用は Phase 3（ADR-0009 の未解決事項「Ontop 経由の連邦クエリが前提」）。オントロジーの構造規約（「すべてのクラスにラベルがあるか」等）は想定質問の `expect: empty`（`P2B-07`）が担う。**同じ役割の仕組みを 2 つ持たない。SHACL はデータの形、想定質問は語彙と規約。**

テストは 207 → **218 件**（`P2A-05` 時点）。

`P2A-09` の補足（**自動化は完了。2026-09-09。実機確認は未了**）: `P2A-06` の副作用として発生した前提を閉じた。

- **何が問題だったか**: `P2A-06` 以降、名前空間の作成には `platform-admin` が必要である。`azd up` の `postdeploy` は同梱サンプルの名前空間を作るため、**割り当てが無いと `azd up` が 403 で止まる**。しかも止まるのは約 11 分のプロビジョニングと課金を使い切った**後**である
- **なぜ Bicep で作れないか**: Entra のアプリ登録は ARM のリソースではなく `azd` の管理外にある（`azd down` でも消えない。`P1-09` に記録）。Bicep からアプリロールは定義できない
- **やったこと**:
  1. `scripts/setup-app-role.py`（`just setup-app-role`）でアプリロールの定義と割り当てを行う。**冪等**で、`--dry-run` で何をするかだけ見られる。`--principal-id` でエージェントのサービスプリンシパルにも割り当てられる
  2. `preprovision` が**プロビジョニングの前に**確認して止める。`scripts/preprovision.test.sh` で両方向の分岐を固定した
  3. `postdeploy` が 403 のときに原因を言い切る（トークンは取れているので「認証の失敗」と読み違えやすい）
- **設計上の要点**:
  - **判定はトークンの `roles` クレームで行う。** Microsoft Graph の `appRoleAssignments` を読む方法は却下した。(1) **グループ経由の割り当てを見落として偽のブロッカーになる**（ユーザーの `appRoleAssignments` は直接の割り当てしか返さない） (2) Graph の読み取り権限を要求する (3) API が見るのは `roles` クレームなので、同じものを見れば食い違いが起きない
  - **「確認できなかった」ときは止めない。** `check-platform-admin.py` の終了コードを 1（読めなかった）と 2（読めたが無い）に分け、2 だけで止める。**通すべきときに止めると、確認の仕組み自体が新しいブロッカーになる**（`az login` が切れている、スコープが違う等は起こりうる）。止めるべきときに通すと 11 分と課金を捨てるが、こちらのほうが害が小さい
  - **`appRoles` は複合プロパティなので既存の定義を落とさない。** 同じ罠を `api`（スコープと `preAuthorizedApplications`）で踏んでいる（`P1-09` の罠1）。合成は純粋関数 `merge_app_role` に切り出して、既存を落とさないこと・**同名ロールの ID を再利用すること**（作り直すと既存の割り当てが全部無効になり運用者が自分の権限を失う）を自動テストで固定した
- **残っているもの（実機確認。費用が発生するのでユーザーの確認が必要）**:
  1. 実トークンに `roles` クレームが乗ること
  2. `platform-admin` を持たない主体が名前空間の作成で 403 になること
  3. `platform-admin` が四眼原則を飛び越えられないこと（ADR-0014 決定5）
  4. `setup-app-role.py` が実テナントで通ること（**Graph への PATCH / POST は未実行**。スタブで配線を確認しただけである）
- **出典**: `P2A-06` の実装（2026-09-09）で、名前空間の作成に `platform-admin` を要求した結果として発生

`P2A-12` の補足（**未着手**。`P2B-03` で発見）: **緑のチェックが何も検証していない状態を作れる。**

- **何が起きるか**: `ci.yml` は `concurrency: cancel-in-progress: true` を持つ。コードを push して CI が走っている最中にドキュメントだけの push をすると、**走っていた run がキャンセルされる**。新しい run は paths-filter でコードのジョブを全部スキップし（`CLAUDE.md` しか変わっていないため）、**`success` で終わる**。結果として「最新のコミットは緑」なのに、**コードは一度も検証されていない**
- **実際に踏んだ**: `P2B-03` のコミットの CI が `cancelled` になり、次のドキュメントコミットの CI が `changes: success / python: skipped / shell: skipped / ...` で成功した（2026-09-10）。手動で `gh run rerun` して検証した
- **なぜ問題か**: このリポジトリが繰り返し避けている「見せかけの X」そのものである。ブランチ保護や「最新のコミットが緑か」で判断する運用に入ると、静かにすり抜ける
- **やったこと**: `cancel-in-progress` を `${{ github.ref != 'refs/heads/main' }}` にした。**PR のブランチでは最新のコミットだけが問題なので打ち消してよいが、main は 1 コミットずつ検証されるべきなので打ち消さない。**
- **却下した対処**:
  - **`concurrency` の `group` にジョブの種類を含める**: paths-filter の結果は run が始まるまで分からないので、group を分けられない
  - **`cancel-in-progress` を無条件に外す**: PR で連続 push したときに古い run が走り続け、CI 時間を無駄に使う
  - **スキップを「成功」として報告しない集約ジョブを置く**: paths-filter でスキップするのは**意図した設計**（ドキュメントだけの変更でコンテナをビルドしない）。スキップを失敗にすると、その設計を壊す
- **残っている限界**: ドキュメントだけの push の run は依然として「全部スキップして成功」である。**それ自体は正しい**（その push はコードを変えていない）。問題だったのは、それが**別の run を打ち消していた**ことである
- **出典**: `P2B-03` の push 後に CI の内訳を確認して発見（2026-09-10）

`P2A-11` の補足（**未着手**。`P2B-11` で発見）: OpenAPI のパスパラメータ名が揺れている。`namespaces.py` は `/namespaces/{name}`、他のルータは `/namespaces/{namespace}` を使う。

- **なぜ問題か**: 生成される TypeScript の型（`just gen-api`）でパラメータ名が経路ごとに違い、呼び出し側が混乱する。ドキュメントの説明も一貫しない
- **なぜ直していないか**: **パスパラメータ名の変更は URL を変えないので後方互換だが、生成される型の名前が変わる**。Web（`P2A-03`）が型を使い始める前に揃えるのが安い。今揃えると、まだ誰も使っていない型を変えるだけで済む
- **どちらに揃えるか**: `{namespace}` が多数（5 経路 対 3 経路）で、意味も明確
- **出典**: `P2B-11` の実装後に OpenAPI の経路一覧を確認して発見（2026-09-10）

`P2A-10` の補足（**未着手**。`P2A-09` で発見）: `scripts/bootstrap-db.py` と `scripts/check-questions.py` が `print` で日本語を出しており、**Windows では cp932 のバイト列になる**。周りのシェルスクリプトの `echo` は UTF-8 を出すため、azd のフックのログに 2 つのエンコーディングが混ざる。cp932 に無い文字（絵文字・ダッシュ）を出そうとすると `UnicodeEncodeError` で**スクリプトごと落ちる**。`P2A-09` で作った `ontology_core.console` の `say` / `warn` に置き換えれば済む。**害は「ログが読みにくい」に留まる**ため優先度は低いが、`bootstrap-db.py` は権限分離の失敗を運用者に説明する経路なので、読めないと困る場面がある。

`P2A-08` の補足: 現在は値が保持され Bicep が注入しているが強制されていない。任意の SPARQL に LIMIT を後付けするのは副問い合わせや CONSTRUCT で壊れるため、安価で正しい手段が無い。README と `config.py` に未強制であることを明記済み。

---

## Phase 2 柱 B — 運用し続けられる

すべて [ADR-0009](adr/0009-ontology-operations.md) が根拠。決定番号を併記する。

| ID | 内容 | ADR-0009 | 優先 | 状態 |
|---|---|---|---|---|
| `P2B-01` | OWL 推論器（ELK）を CI へ前倒し | 決定 1 | 高 | 未着手 |
| `P2B-02` | 保持ポリシーの実装（ストアに載せる版の制御） | 決定 2 | 高 | 未着手 |
| `P2B-03` | 廃止のライフサイクル（`owl:deprecated` + 後継） | 決定 3 | 高 | **完了**（2026-09-10） |
| `P2B-04` | 名前空間と用語の責任者 | 決定 4 | 高 | **完了**（2026-09-10） |
| `P2B-05` | アクセスログの実装（ADR-0006 §4、健全性指標の原資料） | 決定 5 | 高 | 未着手 |
| `P2B-06` | 健全性指標の集計と提示 | 決定 5 | 中 | 未着手 |
| `P2B-07` | 想定質問を SPARQL テストとして CI で実行 | 決定 6 | 高 | **完了**（2026-09-09） |
| `P2B-08` | `reason` / `diff` を書き、参照時に返す | 決定 7 | 中 | **完了**（2026-09-09） |
| `P2B-09` | 意味的差分の計算と差分レビュー | 決定 7 | 中 | **計算は完了**（2026-09-10。**画面は `P2A-03`**） |
| `P2B-10` | 領域間マッピング（SKOS `closeMatch` 等） | 決定 8 | 中 | 未着手 |
| `P2B-11` | 監査を読み出す API | 決定 7 | 中 | **完了**（2026-09-10） |
| `P2B-12` | 削除の TOCTOU を閉じる（監査付き削除） | — | 中 | 未着手 |
| `P2B-14` | 想定質問をデプロイ済みの名前空間に紐づける | 決定 6 | 中 | 未着手 |

`P2B-03` の補足（**完了。2026-09-10**）: ADR-0009 決定3 の 3 項目すべてを実装した。設計は [ADR-0017](adr/0017-deprecation-lifecycle.md)。**同 ADR-0009 が未解決のまま残した「廃止された用語を参照するクエリへの警告をどの層でどう返すか」も閉じた。**

**廃止は TTL に書く。** `owl:deprecated true` と後継（`dcterms:isReplacedBy`）は**オントロジーの内容そのもの**であり、W3C の語彙がそれを表現するために存在する。**責任者（`P2B-04`）を TTL に書かなかったのと逆の判断で、理由も逆である** — 責任者は現在の状態で変わるたびに新しい版を公開することになるので TTL には置けなかったが、廃止は定義の一部であり版に固定されるのが正しい（不変条件7）。副産物として `?s owl:deprecated true` で SPARQL からそのまま引ける。

**決定可能であることは、ブロックすべきであることを意味しない。** ブロックが妥当なのは、規則が一義的で**かつ従う正当な手段が常にある**ときだけである。

| 検査 | 決定可能 | 扱い |
|---|---|---|
| **IRI の削除**（現行版にあった用語が消えた） | はい | **ブロック**（422） |
| **廃止された用語に後継も理由も無い** | はい | **ブロック**（422） |
| 生きている用語が廃止された用語を参照している | はい | **報告のみ** |

**最後をブロックしないのは、SHACL の形状やマッピングが廃止された用語を正当に参照するから**である。旧データを検証する形状、旧→新のマッピング（`dcterms:replaces`）はどちらも書けなければならない。ブロックすると移行のための記述が禁止される。なお `dcterms:replaces` と `rdfs:seeAlso` による参照は**歴史的参照**として最初から問題に数えない。

**後継の指定を必須にしなかった。** 後継が存在しない廃止は正当にある（間違って作った用語、統合されずに消える概念）。必須にすると存在しない後継を捏造させることになる。後継**または**理由（`rdfs:comment` / `skos:historyNote`）のどちらかを要求する。**空文字列や空白だけの理由は理由として数えない**（すり抜けを塞ぐ）。

**クエリの警告は層ごとに置き場所が違う**（ADR-0017 決定3）。

| 層 | 置き場所 | 理由 |
|---|---|---|
| Core API (`POST .../sparql`) | **ヘッダ** `X-Ontology-Deprecated-Terms` | 本文は標準の SPARQL Results JSON のまま。ADR-0001 の「SPARQL 1.1 Protocol をハード境界にする」を守る |
| MCP (`sparql_query`) | **本文**（`deprecation_warnings`） | ツール結果の形はこの製品が定義するもの。**エージェントはヘッダを見ない** |

**MCP だけ本文に載せるのがこの決定の要点である。** 「AI エージェントに正しいコンテキストを渡す」ことが目的なので、エージェント経路で警告が届かないなら警告を作る意味がない。一方で Core API の SPARQL 応答は標準であり続けなければならない。

**削除のブロックは廃止の経路と同じ変更で入れた**（ADR-0016 決定6 が送った判断）。順序を逆にすると、縮める手段が無い状態で削除だけが禁止される期間ができる。

**既存のテストを 4 件更新した**（仕様変更の帰結であり、回避ではない）。

- `test_state_projection.py` の `P1-C1` の実証: V1 が `ex:A`、V2 が `ex:B` だけを持つ形は**IRI の削除**なので承認できなくなった。廃止して残す形に変え、判別を「主語の数」から**「同じ用語に新旧の定義が同居するか」**に変えた。これは `P1-C1` で実際に報告された不具合そのものなので、**判別力は上がった**
- `test_version_diff.py` の削除のテスト: 「報告するだけ」から「422 で拒否」に変え、**正しい縮め方（廃止して残す）が通ることのテストを足した**
- 差分の失敗のテスト: Blob の読み出し回数が 3 → 4 回に増えた（廃止の検査が 2 回読む）
- MCP のテスト: 応答から `headers` を読むようになった

**未解決**: 名前空間をまたぐ参照（別の名前空間で廃止された用語は検出しない）、廃止された用語の責任者（残すか外すか）、クエリ本文に書かれた廃止済み IRI（結果が空でも警告に値する）、推移的な後継の解決。

テストは 371 → **416 件**。11 種類の変異（参照もブロックする・削除をブロックしない・理由を認めない・空の理由を認める・歴史的参照を問題に数える・上限超過で空を返す 等）で検出力を確認した。

`P2B-04` の補足（**完了。2026-09-10**）: ADR-0009 決定4 の実装。**ADR-0009 が未解決のまま残した「責任者の粒度」を [ADR-0015](adr/0015-term-owners.md) で閉じた。**

**答えは「両方」だが、名前空間単位は新しい仕組みを作らなかった。** `namespace_roles` の `owner` ロールがその名前空間の責任者である（ADR-0014 が既にそう決めていた）。用語単位（`term_owners`）だけが新設である。**同じ役割の仕組みを 2 つ持たない** — 名前空間単位で権限と責任者を分けても、ほぼ一致する 2 つの一覧を同期させる手間だけが残る。用語単位こそが、権限では表現できない粒度である。

| 操作 | 必要なロール |
|---|---|
| 責任者の一覧・解決 | `data-analyst` |
| 責任者の付与・取り消し | `maintainer` |

**読み取りを `data-analyst` に開いたのは意図的**である。`namespace_roles` の一覧は `owner` を要求する（誰がどの権限を持っているかは管理する立場の人が知るべき情報）。責任者は逆で、**「この用語について誰に聞けばよいか」を知ることが主目的**なので、分析者が引けなければ存在する意味がない。

**設計上の要点**:

- **1 つの用語に責任者は 1 人。** 説明責任が分散するとその意味を失う（尋ねて 3 人返ってくるなら誰も答えない）。付け替えは置き換えで冪等
- **ルーティングにはフォールバックを作った。** 用語の責任者 → 名前空間の `owner` → 解決不能。**ADR-0014 決定6（権限に暗黙のフォールバックを作らない）に反しない** — 安全側の向きが逆である

  | | 無いときの安全側 | 理由 |
  |---|---|---|
  | 権限 | **拒否**する | 「付与が無ければ全員に許可」は「強制していない」を「強制している」と誤認させる |
  | ルーティング | **上位に回す** | 誰にも届かない問い合わせは放置され、**放置されたことも分からない** |

- **ただし暗黙にはしない。** 解決結果の `source`（`term-owner` / `namespace-owners` / `unresolved`）で、代替で解決したことを必ず見せる。見えなければ `P2B-06` が「責任者が未設定の用語」を数えられない
- **用語の実在を検査しない。** ストアは再構築可能な射影であって正本ではない（ADR-0002）。存在確認は**正本への書き込みを射影の可用性に依存させる**（不変条件2・3 が禁じている向き）。未承認の版で定義される用語に先に責任者を決めることも自然に起こる
- **責任者がその名前空間のロールを持っているかも検査しない。** 退職して RBAC から外れた人が責任者のまま残る状態は起こる。それは書き込みを拒否して防ぐものではなく、健全性指標が可視化するもの。拒否すると「責任者を決めてからロールを付ける」順序が動かなくなる
- **用語 IRI は絶対 IRI であればよく、`base_iri` 配下に限らない。** ADR-0009 決定8（領域間マッピング）で外部語彙への `skos:closeMatch` を張るとき、そのマッピングの妥当性について説明責任を負うのは張った側である。外部 IRI に責任者を置けなければ記録できない
- **責任者を外すとき、設定が無ければ 404 を返す。** 「外した」と「もともと無かった」を同じ 204 にすると、**IRI のタイプミスに気づけない**（実在を検査しない設計なので、割り当て時には分からない）

**マイグレーション `0003_term_owners` に backfill は無い。** `0002` は「更新した瞬間に誰も何もできなくなる」ため backfill が必須だったが、責任者は権限ではないので無くても操作は止まらない。加えて用語単位の責任者は既存のどの列からも導出できない（`created_by` は「その版を publish した主体」であって、個々の用語について答えるべき人ではない）。**推測で埋めると「責任者がいる」と誤認させる。** 実 PostgreSQL で `upgrade` → `downgrade` → `upgrade` の往復と `alembic check`（差分なし）を確認した。

**エージェント（MCP）への露出は `P2B-11` で入れた**（2026-09-10）。`term_owner` ツールで「この用語は誰に聞けばよいか」を引ける。ADR-0015 はこれを範囲外として `P2B-11` に送っていた。

テストは 268 → **311 件**（用語 IRI の検証 23 件 + 責任者 20 件）。

`P2B-07` の補足（**完了。2026-09-09**）: 想定質問（Competency Questions）を YAML で書き、SPARQL として実行する仕組みを入れた。

**設計上の要点は「スキーマだけのオントロジーには行を返す質問が書けない」こと。** 同梱サンプルはクラス・プロパティ・SKOS・SHACL だけで、インスタンスデータを含まない（実データは Ontop 経由の連邦クエリ = Phase 3 で初めて現れる）。それまで想定質問が検査できるのは「**その問いに答えるための語彙と関係が存在するか**」である。そのため判定モードを 3 つに分けた。

| `expect` | 意味 | 主な用途 |
|---|---|---|
| `ask_true` | ASK が true | **語彙の表現力**。「注文から顧客へ辿る関係が定義されているか」 |
| `non_empty` | SELECT が 1 行以上 | **データ検索**。Phase 3 以降 |
| `empty` | SELECT が 0 行 | **規約の遵守**。「ラベルの無いクラスが無いこと」 |

`empty` は ADR-0009 **決定1** の「合意済みの規約（命名規則、必須項目）はテストとして機械が実行する」をそのまま満たす。想定質問と規約チェックが同じ仕組みで書けたのは設計上の収穫である。

実装した範囲:

- `ontology_core.competency`: 質問ファイルの読み込み・検証と実行。**壊れたファイルは黙って読み飛ばさず全体を失敗させる**（0 件も失敗。conftest の「静かにスキップしない」方針と同じ）
- **想定質問は読み取り専用に強制する。** `ensure_agent_safe_query` を通して SPARQL Update と `SERVICE` 句を弾く。テストがストアを書き換えてよい理由は無い
- **応答の形が `expect` と噛み合わないときは、偶然の合否で隠さず失敗させる。** ASK を書くつもりで SELECT を書いた間違いを通してしまわない
- `samples/retail-core.questions.yaml`: 同梱サンプルの質問 11 件（語彙 6 件・規約 5 件）。ADR-0009 決定6 の例「ロットのリコールで影響を受ける顧客」は、実データが無くても**商品 → 注文 → 顧客の経路が語彙として存在するか**を検査できる（`cq-03`）
- `packages/api/tests/test_competency_run.py`: **実 Fuseki に対して CI で回る本体**。承認・射影された既定グラフに対して実行する
- `scripts/check-questions.py` + `just check-questions`: オントロジーの作成者が自分の質問を自分の環境に対して回せる CLI。実行して 11/11 を確認済み
- `pyyaml` を core の依存に、`types-PyYAML` を dev に**明示した**（推移的に入っていることに頼らない）

**テストに歯があることを確認した**: サンプルが満たさない要求（`retail:Return` の存在）をわざと投げ、それだけが落ちることを実 Fuseki で確認している。

**取り込まなかったもの**: デプロイ済みの名前空間に質問を紐づける仕組み（`P2B-14`）。storage を勝手に発明しない。

テストは 181 → **207 件**。

`P2B-09` の補足（**計算は完了。2026-09-10。画面は `P2A-03`**）: ADR-0009 が未解決のまま残した「意味的差分の計算方法」を [ADR-0016](adr/0016-semantic-diff.md) で決めた。

**正規化は rdflib の `graph_diff` に任せ、用語単位の層を自作した。** 空白ノードの正規化は RDF Dataset Canonicalization の問題そのもので、**微妙に間違えても静かに間違う**。実測で必要な性質（接頭辞・トリプルの順序・**空白ノードのラベル**の違いを差分にしない）を満たすことを確認した。

| 分類 | 意味 |
|---|---|
| `added` | 新しい版で初めて主語として現れた IRI |
| `removed` | 旧版にあり、新しい版では主語として一切現れない IRI |
| `deprecated` | 新しい版で `owl:deprecated true` を得た IRI |
| `modified` | 両方にあり、記述が変わった IRI |

**設計上の要点**:

- **`deprecated` を `removed` と分けるのが分類の要点。** ADR-0009 決定3 は「廃止を追加と同格に扱い、IRI を削除も再利用もしない」と決めた。**廃止は正しい縮め方、削除は規律違反**であり、同じ「減った」に混ぜてはいけない
- **`added` / `removed` は正規化なしで厳密に求まる。** 「IRI が主語として現れるか」だけで決まり、空白ノードの同一性に依存しない。**差分の最も重要な部分（規律違反の検出）が最も安い**
- **正規化のコストは空白ノードの数だけで決まる**（実測。トリプル総数はほとんど効かない）。2 グラフの差分で 300 個 → 2〜4.5 秒、500 個 → 8 秒、**1,000 個 → 42 秒**。上限を 300 個にし、超えたら `triple_status` を `skipped-too-many-blank-nodes` にして**理由と個数を添える**。`modified` も `null` になる
- **「差分が無い」と「計算できなかった」を混同させない。** 混同すると IRI の削除を見落とす（`P2A-05` で「検証できなかった」を違反として扱わないと決めたのと同じ形）
- **基準は「この承認によって `superseded` になる版」。** publish 時ではなく approve 時に計算する — `draft` は数週間放置されうるので、publish 時点の「現行」は承認時点の「現行」と違う
- **保存するのは要約（JSON）。** 全トリプルは載せない。版は Blob に不変で残るので厳密な差分はいつでも再計算できる（不変条件7）。載せると監査行が非有界に育つ。用語の一覧は 50 件で切り、**切ったことを `truncated` で明示する**
- **差分は承認をブロックしない。** IRI の削除は形式的に決定可能でブロック対象に見えるが、**廃止の仕組み（`P2B-03`）が無い状態で削除をブロックすると、縮める正当な手段が 1 つも無くなる**。順序の問題であり、ブロックは `P2B-03` の一部として入れる
- **差分の計算に失敗しても承認は失敗させない。** 差分は記述的なメタデータであって承認の前提条件ではない。Blob 到達不能で承認が止まるのは不変条件3 と同じ向きの誤りである

**実装中に自分で作った不具合**: 用語の変更判定を「差分のトリプルの主語が IRI か」で行ったところ、**空白ノードの内部だけが変わった用語を検出できなかった**（`sh:property [ sh:minCount 1 ]` を `2` に変えても `modified` が空）。SHACL の制約はレビュアが最も見たい部分なので、これを落とすと差分が役に立たない。**自分で書いたテストが落ちて発覚した。** 空白ノードを参照している IRI へ**推移的に**遡る形に直し、入れ子（`sh:or` のリスト）と循環のテストを追加した。併せて `is_empty` の判定を用語の一覧からトリプル数に変えた（用語の一覧は導出物であり、トリプル数が原本である）。

**もう 1 つ**: `graph_diff` は引数を内部で `to_canonical_graph` に通すので、外から `to_isomorphic` を挟むのは冗長だった（rdflib の実装を読んで確認。実測でも所要時間は変わらない）。**変異テストで `to_isomorphic` を外しても落ちなかった**ことから気づいた。

**未解決**: `modified` の中身の分類（「制約が厳しくなった」と「緩んだ」の区別）、空白ノードの島ごとの正規化（上限が問題になったときの選択肢）、人が読む提示形式（`P2A-03`）。

テストは 333 → **371 件**。

`P2B-11` の補足（**完了。2026-09-10**）: `GET /namespaces/{ns}/audit`。ADR-0009 決定7 の読み出しを汎用化した。設計は [ADR-0006 補記 2](adr/0006-ontology-versioning-and-audit.md) に記録。

**読み出す口は 2 段構えになった。**

| 口 | 粒度 | 用途 | 権限 |
|---|---|---|---|
| `versions/{v}/decisions`（`P2B-08`） | 1 つの版。**起きた順** | エージェントが答えの根拠を示す（MCP の `version_decisions`） | `data-analyst` |
| `audit`（`P2B-11`） | 名前空間全体。**新しい順**・絞り込み・ページング | 運用者が「先週何が起きたか」「この人が何をしたか」を追う | `data-analyst` |

**設計上の要点**:

- **並び順とページングの鍵は `id`。`occurred_at` ではない。** `occurred_at` は `now()`（トランザクション開始時刻）なので、同一トランザクション内の複数イベントは**同じ値になる**。時刻を鍵にすると境界の同時刻の複数件を取りこぼすか二重に返す。`id` は追記専用テーブルの単調増加する主キーなので全順序で安定している
- **offset ではなく keyset（cursor）。** 照会中に追記されるのは普通に起こる。降順の offset だと新しい行が先頭に入るたびに窓が 1 件ずれ、**既読のページの末尾を取りこぼす**。`id < cursor` なら追記は必ず大きい `id` を持つので既読分は動かない
- **`next_cursor` が `None` なら最後のページ。** 件数が `limit` ちょうどでもそうなる（`limit + 1` 件取って判断している）。「返った件数が `limit` より少ないから最後」という判定を呼び出し側に押し付けない
- **集約した口に、より強い権限を要求しない。** 版単位の `decisions` が既に `data-analyst` で読めるので、版を列挙すれば同じ情報が集まる。**足し合わせれば見えるものを集約したときだけ隠すのは見せかけの制限**であり、「守られている」という誤解を作るぶん制限が無いより悪い
- **期間は半開区間 `[since, until)`。** 境界を両側とも含めると期間を並べて集計したときに二重に数える
- **タイムゾーンの無い日時は 422。** 素朴に UTC と解釈しない。**監査の照会で 9 時間ずれた結果を返すのは「何も返らない」よりたちが悪い**（誤った結論の根拠になる）
- **`limit` の上限は 500。** `audit_events` は追記専用で無限に伸びる（ADR-0011 決定2 で `DELETE` を剥奪している）ので、上限が無いと 1 リクエストで全件を読み出せる

**MCP に `term_owner` ツールを足した**（ADR-0015 がここへ送った分）。「この用語は誰に聞けばよいか」をエージェントが引ける。`version_decisions` が「誰が承認したか」（過去の行為者）を返すのに対し、こちらは**現在の責任者**を返す。ツールの説明に「`source` が `namespace-owners` なら、その用語には責任者がいない」ことを明記した — エージェントが「担当者はこの人です」と断定しないようにするため。

**名前空間全体の監査照会は MCP に出していない。** エージェントが必要とするのは「この定義の根拠」であって「名前空間の全履歴」ではない。運用者の視点の口をエージェントに渡すと、返す量ばかり増えて答えの質が上がらない。

**実装中に踏んだこと**: FastAPI の `Query(...)` を既定値の位置に書くと、**ハンドラを直接呼ぶテストで `Query` オブジェクトが値として流れ込む**（`limit: int = Query(default=50)` の既定値は `50` ではない）。FastAPI 経由なら解決されるので HTTP で叩くテストだけでは気づけない。`Annotated[int, Query(...)] = 50` に直した。CLAUDE.md に記録した。

**変異テストで 1 件取り逃していた。** `until` を `<` から `<=` に変えても落ちなかった — 境界を 2 つのイベントの「間」に置いていたため、含む・含まないを区別できていなかった。**記録されたイベントの `occurred_at` を読み戻して境界に使う**テストを足して固定した。

**未解決**: 名前空間をまたぐ照会（「この人が全名前空間で何をしたか」）。権限判定が名前空間単位で収まらないため、`platform-admin` に限るか見える名前空間だけを合成するかを決めていない。

テストは 311 → **333 件**。

`P2B-08` の補足（**完了。2026-09-09**）: `audit_events` の `reason` 列は最初から存在したが、`reject` 以外は誰も渡していなかった。「誰が承認した定義に基づく答えかを説明できること」が中核価値（ADR-0006）なのに、**理由が空の監査は説明にならない**。

実装した範囲:

- **書き込み**: `publish` / `submit` / `approve` が `reason` を受け取って記録する（`reject` は元から必須）。**任意にした** — 必須にすると `postdeploy` のような自動投入や既存のクライアントが動かなくなる。人が操作する経路（Web UI、`P2A-03`）では必ず書かせる
- **`superseded` の理由はシステムが書く。** 人が書けない自動遷移で理由が空だと「なぜこの版が現行でなくなったか」を辿れない（この分は元から実装されていた）
- **読み出し**: `GET /namespaces/{ns}/versions/{v}/decisions` が決定記録を**起きた順**に返す。存在しない版は空配列ではなく **404**（「記録が無い版」と「存在しない版」を区別するため）
- **MCP ツール `version_decisions`**: 決定 7 の目的は「用語を引いた人や**エージェント**が同時に得られる」ことなので、エージェント経路に口が無ければ達成されない。`sparql_query` が返すのは定義そのもので、その根拠は含まれない
- **同梱サンプルでも実演する**: `postdeploy.{sh,ps1}` が publish / submit / approve に意味のある理由を渡す

**並び順の罠**: `occurred_at` の既定値は `now()` で、PostgreSQL では**同一トランザクション内のイベントが同じ値になる**。`id` を第二キーにしないと「published のあとに submitted」という履歴が読めない。`list_for_subject` は両方で並べる。

**取り込まなかったもの**: `diff`（意味的差分）の計算は `P2B-09`（**2026-09-10 に完了**）。埋めたふりをせず `None` のまま返した。汎用の監査照会（名前空間全体・期間・実行者での絞り込み、ページング）は `P2B-11`（**2026-09-10 に完了**）。

テストは 172 → **181 件**（サービス層 5 件・ルータ層 3 件・MCP 1 件。すべて修正前に落ちることを確認済み）。

`P2B-14` の補足（`P2B-07` の実装中に分離）: `P2B-07` はリポジトリ内の質問ファイルを CI で回すところまでである。**デプロイ済みの名前空間に質問を紐づける仕組みは未決**で、決めるべきことが 3 つある。

1. **どこに置くか。** TTL と同じ Blob（`versions/<ns>/<version>.questions.yaml`）が素直だが、質問は版ごとに変わるのか名前空間ごとに 1 つなのかが決まっていない
2. **`approve` をブロックするか。** ADR-0009 決定1 は「合意済みの規約はテストとして機械が実行」とし、形式的に決定可能なものはブロッキングとしている。想定質問は「合意済みの規約」に当たるので、落ちたら承認を止めるのが筋である。ただし承認経路に SPARQL 実行を挟むと、Fuseki が落ちているときに承認できなくなる（不変条件3 との関係を決める必要がある）
3. **誰が書くか。** 名前空間の責任者（決定4、`P2B-04`）と同じ主体だと思われるが、`P2B-04` が未着手

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
