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

Phase 2 で完了したもの: `P2A-05`（SHACL 検証）/ `P2A-06`（名前空間 RBAC と四眼原則）/ `P2A-09`（`platform-admin` の設定の自動化。**実機確認は未了**）/ `P2A-12`（CI が「緑なのに何も検証していない」状態を作らないようにした）/ `P2B-01`（OWL 推論器を CI へ）/ `P2B-02`（保持ポリシー）/ `P2B-03`（廃止のライフサイクル）/ `P2B-04`（用語単位の責任者）/ `P2B-05`（アクセスログ）/ `P2B-06`（健全性指標）/ `P2B-07`（想定質問の CI 実行）/ `P2B-08`（`reason` の記録と参照）/ `P2B-09`（意味的差分の計算）/ `P2B-11`（監査を読み出す API）/ `P2B-10`（領域間マッピング）/ `P2A-10`（スクリプトの cp932 出力）/ `P2A-08`（結果件数の上限の強制）/ `P2A-11`（パスパラメータ名の統一）/ `P2A-13`（`FUSEKI_PORT` が CLI に届かない）/ `P2B-12`（削除と公開の競合）/ `P2B-14`（想定質問をデプロイ済みの名前空間に紐づける）/ `P2A-07`（監査証跡の PROV-O 表現）/ `P2A-15`（何から編集したかの記録）/ `P2B-15`（導出された含意を射影するかの決定 → **射影しない**）/ `P2B-16`（受け入れ基準の出自の検査）/ `P2B-18`（マッピング先の生死）/ `P2B-17`（マッピングを射影するかの決定 → **射影せず Turtle で書き出す**）/ `P2B-19`（名前空間の退役）/ `P2B-20`（孤児のマニフェスト）/ `P2A-14`（`CONSTRUCT` / `DESCRIBE`）。

**次に着手する候補**（費用が発生しないものを優先順に）:

| ID | 内容 | なぜ次か |
|---|---|---|
| `P2A-16` | 主体が人間かサービスプリンシパルかを記録する | `P2A-07` の未解決。`prov:Person` / `prov:SoftwareAgent` を出せるようになる。**記録の時点で保存する**必要がある（書き出し時に Entra へ問い合わせると監査の書き出しが Entra の可用性に依存する） |
| `P2A-17` | 監査証跡の JSON-LD 表現 | `P2A-07` の代替案から分離。**`@context` の公開先**という新しい問題が生まれる |
| `P2B-21` | 廃止された先を指すマッピングの数を健全性指標に出す | `P2B-18` の未解決。**指標が他の名前空間の Blob の可用性に依存する**ため要求が出てから |
| `P2B-23` | `CONSTRUCT` / `DESCRIBE` の結果にも廃止済み用語の警告を出す | `P2A-14` の未解決。`deprecated_iris_in_results` は SPARQL Results JSON の形を前提にしている。**出せないものを出したふりをしない**ために、いまは出していない |
| `P2B-22` | `ASK` のアクセスログが `returned_row_count = 0` を記録する | `P2A-14` で `CONSTRUCT` / `DESCRIBE` を `null` にしたが、`ASK` は既存の振る舞いを変えていない。**意味を変えると既存の記録の読み方が変わる**ため分離した |

**設計の好みが絡むもの**（実装の判断に人の好みが入る）: `P2A-03`（Web でのレビュー・承認フロー）/ `P2A-04`（グラフ可視化）。どちらも見た目と操作の設計が中心で、**ADR で決めるより実物を見て決めるほうが早い**。

**費用が発生するもの**（ユーザーの確認が必要）: `P2A-09` の**実機確認**。自動化（`just setup-app-role` と `preprovision` のゲート）は 2026-09-09 に完了し、ローカルでは 416 件のテストで検証済みだが、**実 Entra テナントに対しては一度も実行していない** — アプリロールの定義（Graph への PATCH）、割り当て、実トークンの `roles` クレーム、`platform-admin` を持たない主体が 403 になること、`platform-admin` が四眼原則を飛び越えられないことは、いずれもデプロイ窓が必要。

---

## 完了した Critical（判断の履歴として残す）

### `P2B-C1` マニフェストの schema が食い違い、ローダが全名前空間をスキップしていた

**状態: 完了（2026-09-12）。出典: `P2B-19` の設計のためにローダを読んでいて気づき、実測で確認した。**

`containers/fuseki/lib/validate.sh` の `validate_manifest_json` が `.schema == 1` を要求していたのに、`_build_manifest`（Python）は **ADR-0019（`P2B-02`）以降 `"schema": 2` を書いていた**。

```
$ . containers/fuseki/lib/validate.sh
$ validate_manifest_json '{"schema":2,"namespace":"x","versions":[]}' ; echo $?
1        # ← 拒否される
```

**帰結**: ローダは `_state.json` が「不正な形式」だと判断し、`build_tdb` が `continue` で**その名前空間を丸ごとスキップする**。マニフェストは全名前空間に書かれているので、**レプリカを作り直すとストアが空になる**。`POST /admin/reconcile` は手動なので、回すまでクエリは静かに 0 行を返し続ける。

**不変条件1（トリプルストアは再構築可能な射影である）の前提が成り立っていなかった。** ADR-0002 が設計の中心に置いた「いつでも作り直せる」が、この間だけ嘘だった。

**なぜ誰も気づかなかったか**: シェル側のテストが **`schema 1` のマニフェストしか食わせていなかった**。`validate.test.sh` は schema 2 で `manifest_projection_for_version` と `projection_targets` を検査していたが、**`validate_manifest_json` には schema 2 を渡していなかった**。`load-snapshot.test.sh` のスタブも schema 1 だった。**書き手が実際に出す形を読み手に食わせるテストが 1 本も無かった。**

**直したこと**:

- `MANIFEST_SCHEMAS="1 2"` を宣言し、`validate_manifest_json` はこの集合と照合する。**`schema` が数値であることも要求する**（`jq -r` は `"2"` と `2` を同じ出力にするので、型を見ないと壊れたマニフェストが通る）
- **未知の（新しい）schema は拒否する。** そのマニフェストが運んでいる指示を知らないまま射影すると「新しい指示を黙って無視した射影」になる。`skip:unknown-status-*` と同じ方針である
- **その代わり、安全に関わる指示を新しい schema にだけ載せない。** 古いローダが無視すると事故になるものは既存の欄で表現する（`P2B-19` の退役が `projection` の `skip:` を使うのはこの理由）
- `validate.test.sh` に **schema 2 を受理する / 未知の schema を拒否する / 文字列の schema を拒否する**の 3 件を足した
- `load-snapshot.test.sh` のスタブを **schema 2（書き手が実際に出す形）**に変えた
- **`packages/api/tests/test_manifest_contract.py` を足した。** `_build_manifest` の `schema` が `validate.sh` の許可リストに入っていることを、**シェルのソースを読んで**検査する。値をテストに書き写さないのが要点で、書き写すとシェル側を直し忘れても通ってしまう（今回とまったく同じ形になる）

**教訓**: **言語の境界をまたぐ契約は、両側にテストを書くだけでは守られない。** 型検査も lint も境界を越えないので、「**両者が同じ値を見ていること**」を直接検査する必要がある。

**副産物**: Python の `pathlib.write_text` が Windows で LF を CRLF に変え、Alpine の `sh` が `illegal option -` で落ちた。CLAUDE.md に記録した。


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
| `P2A-07` | 監査証跡の PROV-O 表現 | 中 | **完了**（2026-09-12） |
| `P2A-08` | `SPARQL_MAX_RESULTS` の強制 | 中 | **完了**（2026-09-11） |
| `P2A-14` | `CONSTRUCT` / `DESCRIBE` に対応する | 低 | **完了**（2026-09-12） |
| `P2A-15` | `base_version` を保存する（派生関係を出せるようにする） | 中 | **完了**（2026-09-12） |
| `P2A-16` | 主体が人間かサービスプリンシパルかを記録する | 低 | 未着手 |
| `P2A-17` | 監査証跡の JSON-LD 表現 | 低 | 未着手 |
| `P2A-09` | `platform-admin` アプリロールの設定を自動化する | 高 | **自動化は完了**（2026-09-09。**実機確認は未了**） |
| `P2A-10` | 既存スクリプトの日本語出力が Windows で cp932 になる | 低 | **完了**（2026-09-11） |
| `P2A-11` | パスパラメータ名が `{namespace}` と `{name}` で揺れている | 低 | **完了**（2026-09-11） |
| `P2A-12` | ドキュメントだけの push が、コードを検証中の CI を打ち消す | 中 | **完了**（2026-09-10） |
| `P2A-13` | `FUSEKI_PORT` が `scripts/check-questions.py` に効かない | 低 | **完了**（2026-09-11） |

`P2A-07` の補足（**完了。2026-09-12**）: 設計は [ADR-0026](adr/0026-provenance-export.md)。ADR-0006 に補記 3 を追加した。

**W3C 標準忠実を掲げる製品が、自身のメタデータだけ独自スキーマで出していた。** [ADR-0006](adr/0006-ontology-versioning-and-audit.md) 決定3 は「監査証跡を PostgreSQL に記録し、**PROV-O で表現する**」と決め、根拠の節で「監査だけ独自スキーマ、という不整合を避けられる」と書いていた。記録は実装されていたが**表現の側が未実装**で、ADR-0006 自身が却下した案（独自スキーマ）と実質同じ状態だった。

`GET /namespaces/{ns}/provenance` が `text/turtle` を返す。絞り込みと権限は `GET .../audit` と同じ（`data-analyst`）。

**PROV-O へ写そうとして、記録していない事実が 1 つ見つかった。**

`audit_events` には**「どの版から編集したか」が無い**。`publish` は `base_version` を受け取るが、lost update の検出にだけ使って保存していない（`P1-13`）。[ADR-0016](adr/0016-semantic-diff.md) 決定2 が差分の基準を「この承認によって `superseded` になる版」と決めたのは**実務上の基準**であって、著者が実際に何から編集したかではない。

つまり「2.0.0 は 1.0.0 の次に承認された」は記録されているが、「2.0.0 は 1.0.0 から派生した」は記録されていない。

**設計上の要点**:

- **ADR-0006 が名前を挙げていた `prov:wasDerivedFrom` / `prov:wasRevisionOf` を意図的に出さない**（決定2）。承認の順序から派生を出すのは、測っていないことを標準語彙で主張することである。しかも**相互運用性があるぶん害が大きい** — 外部の PROV ツールは派生の系譜として表示し、誰も疑わない。「相互運用性がある」という ADR-0006 の根拠は、嘘を書いたときの害も同じだけ大きくする（`P2A-15` で `base_version` を保存すれば出せる）
- **主体を `prov:Person` / `prov:SoftwareAgent` に分けない**（決定3）。`actor` は Entra のオブジェクト ID だけで人間か機械かを持っていない。**四眼原則を記録する監査証跡で、人間の承認を自動化された行為として見せるのは最悪の誤りである**（`P2A-16`）
- **行為の種類を独自の下位クラスで保つ**（決定4）。`ont:Publish` 等を `rdfs:subClassOf prov:Activity` として併記する。`prov:Activity` だけにすると「何をしたか」が消える。**対応表に無い `action` は種類を名乗らせない** — 知らない行為を既知のどれかに丸めると、未知の行為が既知の行為として集計される。生の文字列は `ont:action` / `ont:subject` に必ず残す
- **`published` だけが `prov:generated`**（決定4）。全部を `generated` にすると「1 つの実体が 5 回生成された」という読めない記録になる
- **切り詰めを RDF の中に書く**（決定5）。ADR-0016 決定5 / ADR-0020 決定3 / ADR-0021 決定1 / ADR-0022 決定5 / ADR-0025 決定3 と**同じ原則の 6 例目**。`ont:truncated` は**偽でも明示的に**出す（RDF は「無い」と「返していない」を区別できない）。`ont:includes` で束と行為を結ぶのは、旗だけでは「どの行為がその束の中身か」が辿れず使えないため
- **トリプルストアには射影しない**（決定6）。ADR-0023 決定7 と同じ理由（出自の記録はどの版にも属さないので版の名前付きグラフに混ぜられない）
- **IRI の空間を分ける**（決定7）。版は `urn:ontology:revision/<ns>/<version>`。**版のグラフ IRI（`urn:ontology:graph/...`）を流用しない** — あれは「その版のトリプルが載るグラフ」の識別子で、版そのものではない

**外部由来の文字列で Turtle を壊せないようにした。** `actor` は `AUTH_MODE=disabled` では任意の文字列になりうる。符号化しないと IRI が途中で閉じ、**書き出し全体がパースできなくなる**か、最悪トリプルが注入される。テストは書き出しを**パースし直して**検査する（文字列一致だと壊れた Turtle を「それらしい文字列が入っている」で通してしまう）。

**却下したもの**: `Accept: text/turtle` で `/audit` に内容交渉させる（JSON には `cursor` が要るが RDF では意味が薄く、**同じ URL が表現によって違うパラメータを取る**形になる）/ `prov:wasRevisionOf` を「版の連番」として出す（外部ツールは派生として読む。説明は機械に届かない）/ JSON-LD で返す（`@context` の公開先という新しい問題が生まれる。`P2A-17`）/ 監査イベントを PROV-O のまま PostgreSQL に保存する（ADR-0006 が既に答えている）。

**未解決**: **カーソルページングが無い**（大きな名前空間は `since` / `until` で期間を区切る）/ **IRI が `urn:` なので dereferenceable ではない**（外部ツールから「この版の詳細を引く」ことはできない）/ 主体の種別（`P2A-16`）/ JSON-LD（`P2A-17`）。派生関係は `P2A-15` で解決した（2026-09-12）。

テストは 655 → **709 件**。16 種類の変異で検出力を確認し、**すべて検出された**（`prov:used` を `wasDerivedFrom` にする・承認も版を生むことにする・主体を `SoftwareAgent` にする・切り詰めていないとき `truncated` を書かない・IRI の符号化をやめる・バージョンの検証をやめる・対象の前置きの検査をやめる・生の `subject` を出さない・未知の `action` に既定のクラスを当てる・束と行為の結び付きをやめる・理由が空でも `comment` を出す・名前空間名を検証しない・差分が無くても `diffSummary` を出す・切り詰めを件数と上限の比較で判定する・権限を確かめない・MIME 型を `application/json` にする）。

`P2A-14` の補足（**完了。2026-09-12**）: 設計は [ADR-0034](adr/0034-construct-describe.md)。ADR-0025 に補記を追加した。

ADR-0025 決定8 が **400 で断っていた**のを解除した。断っていた理由は「実装していないから」ではなく**通すと 502 になっていた**からである。

**実測して原因の所在を訂正した。**

```
Accept=text/turtle                     -> 200 text/turtle  len=254
Accept=application/sparql-results+json -> 200 text/turtle  len=254
Accept=*/*                             -> 200 text/turtle  len=254
```

**Fuseki は `CONSTRUCT` に対して `Accept` に関わらず Turtle を返す。** 502 の原因は「`Accept` の食い違い」ではなく **`query` が `_send_json`（`response.json()`）を通っていたこと**だった。ADR-0025 が挙げた「内容交渉が要る」は**必要条件ではなかった** — 要ったのは **JSON として解析しない経路**である。それでも `Accept: text/turtle` を送るのは**プロトコルとしての正しさ**で、持ち込みストアが `Accept` を尊重するかもしれない（**Fuseki だけを見て「不要」と結論しない**）。

**設計上の要点**:

- **クエリの形の判定を 1 か所に置く**（決定1）。`query_form` をガードとルータの両方が使う。**判定が 2 か所にあると、ガードが通した形と扱う形が食い違う** — ADR-0025 決定8 が直したのと同じ形の不具合になる。**未知の形は通さない**（推測して扱わない）
- **`SparqlStore.construct` を足す**（決定2）。`query` の `dict` 契約は変えない（ADR-0025 がストリーミングを却下した根拠である）。**抽象メソッドにする** — 既定実装で例外を投げると持ち込みストアで黙って 500 になる
- **同じエンドポイントで返す**（決定3）。それが SPARQL 1.1 Protocol の振る舞いで、ADR-0001 はそれをハード境界にすると決めている。**URL を分けるとクライアントがクエリを送る前に形を判定しなければならない**（ADR-0026 / ADR-0031 が別 URL を選んだのとは事情が違う — あちらは同じデータの 2 表現でパラメータも違った）
- **上限を超えたら切り詰めずに 413**（決定4）。**ADR-0025 決定5 とは意図的に違う判断**で、理由は 1 つで十分に強い — **RDF には封筒が無い**。`SELECT` の結果は JSON の封筒に「切り詰めた」と書けるが Turtle には書けず、トリプルを足せば利用者のグラフにこちらの主張を混ぜる。ヘッダに書いても**エージェントは見ない**（ADR-0017 決定3）。つまり**切り詰めた RDF は、切り詰めたと言えないまま完全な RDF として届く**
- **解析する費用を認める**（決定5）。バイト数はトリプル数の代理にならない（接頭辞と IRI の長さで 10 倍変わる）。**ADR-0025 が既に受け入れている未解決と同じもの**で、新しい未解決を作ったのではない。CPU バウンドなので `asyncio.to_thread` に逃がす
- **応答は解析し直した Turtle**（決定6）。どうせ解析するので形を 1 つに決める
- **行とトリプルを混ぜない**（決定7）。`returned_row_count` を NULL 可にし、`returned_triple_count` を足した。**`0` を書かない**。`ASK` の既存の振る舞いは変えていない（`P2B-22`）
- **MCP は `head` / `results` を偽造しない**（決定8）。`turtle` / `triple_count` / `form` を返す。空の `results` を付けるとエージェントは「0 行だった」と読む

**実物の Fuseki に対して検証した**（`test_construct_fuseki.py`）。**フェイクでは検証できない問題**だったので、`Accept: text/turtle` で実際に Turtle を受け取れること、既定グラフだけを見ること、上限の計数が実際の書き方に対して働くことを固定した。

**却下したもの**: 切り詰めて旗を立てる（**旗を立てる場所が無い**。加えて空白ノードの構造が割れる — RDF は単調なので偽にはならないが、`owl:Restriction` から `owl:onProperty` が落ちた断片は**無意味**である）/ 専用 URL / `query` の戻り値を `dict | str` に広げる（呼び出し側が毎回型を判定することになる）/ 応答をそのまま転送する（トリプル数を数えられない = 上限が無い状態に戻る）/ バイト数で上限をかける。

**未解決**: 応答全体を解析する（`arq:queryTimeout` が事実上の防波堤）/ 上限を超えると結果が得られない / 空白ノードのラベルが保たれない / **`CONSTRUCT` の結果に廃止済み用語の警告を出していない**（`deprecated_iris_in_results` は SPARQL Results JSON の形を前提にしている。**出せないものを出したふりをしない**。`P2B-23`）/ `DESCRIBE` の内容はストア依存（SPARQL 1.1 が規定していない）。

**副産物**: FastAPI は戻り値の注釈が `dict[str, Any] | Response` になると応答モデルを組み立てられず、**収集時に `FastAPIError` で全テストが落ちた**（実測）。`response_model=None` を付け、応答の形を `responses` で明示した。

テストは 829 → **883 件**。18 種類の変異で検出力を確認した。**うち 2 件が最初「生き残った / 当たらなかった」** — (1) `Accept` を JSON に変える変異が**実物の Fuseki では検出できなかった**（Fuseki が `Accept` を無視するため。これが上記の原因の訂正につながった）ので、`httpx.MockTransport` でヘッダを固定するテストを足した。(2) 413 の変異は整形後の文字列と食い違って適用できていなかったので、パターンを直して再実行した。最終的に 18/18 を検出した。

`P2A-15` の補足（**完了。2026-09-12**）: 設計は [ADR-0027](adr/0027-revision-lineage.md)。ADR-0026 に補記を追加した。

`publish` は `base_version` を受け取っていたが、**lost update の検出にだけ使って捨てていた**（`P1-13`）。検査が通れば「編集の基準は当時の最新版だった」と分かっているのに、その事実をどこにも書いていなかった。`ontology_versions` に保存し、`prov:wasDerivedFrom` を**測った事実として**出せるようにした。

**素直に 1 列足すだけでは足りなかった。** `NULL` に 2 つの意味が同居する。

| `edited_from_recorded` | `edited_from` | 意味 |
|---|---|---|
| `false` | `NULL` | **分からない**（宣言されなかった）。**既存の全行がこれ** |
| `true` | `NULL` | この名前空間に先行する版が無かった（最初の版） |
| `true` | `'1.0.0'` | 1.0.0 から編集した |

3 つ目を 2 つ目と同じ `NULL` にすると、**「宣言されなかった」が「派生していない」として読める**。

**設計上の要点**:

- **列を 2 本にする**（決定1）。文字列の番兵（`''` / `'-'`）で 3 状態を表す案は、**バージョン文字列として妥当な値と区別がつかなくなる**ので採らない（`_VERSION_PATTERN` は英数字 1 文字を許す）
- **最初の版は「先行版なし」として記録する**（決定2）。`publish` の時点で名前空間の行ロックを持っているので、`latest is None` は推測ではなく**測った事実**である
- **基準を渡さなかった版に「当時の最新版」を書かない**（決定2）。それは ADR-0026 決定2 が API の手前で拒否したもの（承認の順序を派生として主張する）を、**正本に書き込む**形である。**正本に書いた推測は、後から事実と区別できない**
- **名前は `edited_from`。`base_version` を使い回さない**（決定3）。API には既に `base_version` があり、**`diff` / `deprecation` の応答では「差分を取る相手」という別の意味**を持つ。名前の共有は概念の同一視として読まれる（ADR-0026 決定7 で「版のグラフ IRI を版そのものに流用しない」と決めたのと同じ話）
- **backfill しない**（決定4）。版の `id` 順に並べて直前の版を親とすればそれらしい系譜が作れるが、それは決定2 が拒否したものと同じである
- **PROV-O には「記録されているか」も出す**（決定5）。`ont:editedFromRecorded` は真偽どちらでも出す。ADR-0025 決定3 / ADR-0026 決定5 と**同じ原則の 7 例目**
- **版の行が引けなかったときは旗すら出さない**（決定5）。`false` は「行を見て、記録されていなかった」であり、**行を見られなかった**ことと混ぜない（`audit_events` には名前空間への外部キーが無いので、名前空間を削除して同名で作り直すと版の行が無い監査イベントが残りうる）
- **書き出しで引く版は、監査イベントが参照しているものだけにする**（決定6）。対象（`<名前空間>@<バージョン>`）の解析規則を `ontology_core.prov.referenced_versions` に閉じる — 2 か所にあると「実体は出るが系譜が出ない」という静かな不整合になる

**却下したもの**: `audit_events` に列を足す（`prov:wasDerivedFrom` は実体と実体の関係であり行為の属性ではない。そして `GET .../versions/{v}` が系譜を返せなくなる）/ 単一の nullable 列で「根または不明」とする（この作業が直している問題そのもの）/ `base_version` を必須にする（既存のクライアントを壊すうえ、**最初の版には渡せる値が無い**ので問題は解けない）/ 既存の行を版の順序から backfill する。

**未解決**: **`base_version` を渡さないクライアントには系譜が付かない**（最初の版を除いて `false` になる。README で渡すことを勧めた）/ 既存の版には系譜が無い / `edited_from` に外部キーを張っていない（書き込み時に検査しているので宙に浮く参照は生まれないが、制約としては表現していない）。

テストは 709 → **733 件**。14 種類の変異で検出力を確認し、**13 件を検出した**（最初の版も「分からない」にする・基準を渡さなければ当時の最新版を親として書く・系譜を `record` に渡さない・旗を無視して辺を出す・版の行が無いときも `false` を出す・旗を出さない・他の名前空間の行も系譜に使う・`wasDerivedFrom` を `wasRevisionOf` にする・親の実体を型付けしない・`referenced_versions` の行為の門を外す・対象の形を見ない・`get_many` が名前空間で絞らない・書き出しが系譜を引かない）。**1 件は等価変異だった** — `get_many` の空集合の早期 return を外しても、SQLAlchemy の空の `IN` が「常に偽」として同じ結果を返す（往復を 1 回省くためのものなので、出力からは観測できない）。

`P2A-16` の補足（**未着手**。`P2A-07` で発見）: `audit_events.actor` は Entra のオブジェクト ID だけで、人間かサービスプリンシパルかを区別していない。トークンの `idtyp` クレームで判別できるが、**記録の時点で保存する**必要がある — 書き出しの時点で Entra へ問い合わせると、**監査証跡の書き出しが Entra の可用性に依存する**（不変条件3 と同じ向きの判断）。

`P2A-17` の補足（**未着手**。`P2A-07` の代替案から分離）: JSON-LD なら `@context` を付けるだけで既存の JSON クライアントと両立できる。ただし **`@context` をどこで公開するか**という新しい問題が生まれる（IRI が `urn:` で dereferenceable ではない）。要求が出てから足す。

`P2A-08` の補足（**完了。2026-09-11**）: 設計は [ADR-0025](adr/0025-result-limit-enforcement.md)。

**「効いている」ように見えて効いていない設定だった。** `SPARQL_MAX_RESULTS`（既定 10,000）は `Settings` が保持し `infra/modules/api.bicep` が注入していたが、**どこも強制していなかった**。**不変条件11 が名指しした形そのもの**である（「強制していない」を「強制している」と誤認させる）。`config.py` のコメントと README は正直にそう書いてあったが、**正直な注記は強制の代わりにならない**。

**ストア側では止められないことを実測した。**

| 調べたもの | 結果 |
|---|---|
| ARQ のコンテキスト記号 | `queryTimeout` / `updateTimeout` / `httpQueryTimeout` のみ。**行数の上限は無い** |
| `fuseki:queryLimit`（語彙に**存在する**） | **効かない**。サーバ階層とサービス階層の両方、`xsd:integer` と `xsd:long` の両方で `queryLimit 5` を設定し、12 行のデータに `SELECT` を投げて **12 行返った** |
| `fuseki:queryLimit` の読み手 | `fuseki-server.jar` 内で `pQueryLimit` を参照するのは `FusekiVocab` / `FusekiVocabG`（語彙の定義）**だけ**。実装に読み手がいない |

**Fuseki 自身の語彙にも「定義はあるが効かない設定」があった。** 皮肉だが、この作業が直した問題とまったく同じ形である。`arq:queryTimeout` は効いているので**時間は止められるが行数は止められない**。

**設計上の要点**:

- **クエリに `LIMIT` を後付けしない**（決定1）。既存の `LIMIT` / `OFFSET`・副問い合わせ・集約との相互作用で意味が変わり、**`CONSTRUCT` の `LIMIT` は解の数であってトリプル数ではない**。任意の SPARQL を書き換える実装は ADR-0001 が名前空間の隔離で避けた形と同じ
- **API の境界で応答の行数を切る**（決定2）。`ontology_core.sparql.limits.cap_bindings`。**元の辞書を壊さない** — 呼び出し側が切り詰める前の行数を記録する必要があり、破壊的に書き換えると順序の制約が生まれる
- **切り詰めたことを必ず見せる**（決定3）。ADR-0016 決定5 / ADR-0020 決定3 / ADR-0021 決定1 / ADR-0022 決定5 と**同じ原則の 5 例目**
- **伝え方は層で分ける**（決定4）。Core API はヘッダ、MCP は本文。**エージェントはヘッダを見ない**（ADR-0017 決定3 の形を再利用。新しい流儀を増やさない）
- **エラーにしない**（決定5）。エージェントは「全部は取れなかった」ことを知って続けられるべきで、413 / 422 で拒否すると回答が作れない
- **アクセスログには切り詰める前の行数を記録する**（決定6）。「何行渡したか」を記録すると**上限に張り付いているクエリが上限ちょうどの行数として並ぶだけ**になり見つけられない
- **`SPARQL_MAX_RESULTS` は 1 以上に限る**（決定7）。「0 なら無制限」という解釈を作らない — それはこの作業が直した問題の別の形である

**副産物として別の問題を 1 つ直した。** 行数の上限を設計するために「何が返るのか」を実測したところ、**ガードは `CONSTRUCT` / `DESCRIBE` を許すのに経路は 502 で落ちる**ことが分かった（`FusekiStore.query` が `Accept: application/sparql-results+json` を送るのに Fuseki は Turtle を返すため JSON の解析に失敗する）。**エージェントから見るとクエリの誤りとサーバの障害が区別できない。** ガードで 400 にし、理由を添えた（決定8）。対応そのものは `P2A-14` に分離した（内容交渉・戻り値の型・トリプル数の上限という 3 つの設計が要る）。

**既存の間欠的な失敗を 1 つ直した。** 全テストを回したところ `test_audit_query.py::test_期間で絞り込める` が **3 回に 1 回落ちた**。原因は **Python の時計と PostgreSQL の時計を比べていた**こと（`occurred_at` はサーバ側の `now()`）。docker の中と外で時計が一致しないため、境界が 2 件の間に落ちないことがある。**境界を DB から読み戻した 2 件の中点にした**（隣のテストが既にその形だった）。6 回連続で安定を確認した。CLAUDE.md に記録した。

**却下したもの**: `LIMIT` を注入する / 413・422 で拒否する（エージェントが回答を作れない）/ ストア側で止める（**できない**。将来 Fuseki が対応したら多層防御として足す）/ 応答をストリーミングして途中で切る（**API のメモリも守れる唯一の案**だが、`SparqlStore.query` が `dict` を返す契約=不変条件4 を変える必要がある。上限の主目的はエージェントに渡す量を抑えることなので、境界の切り詰めで達成する）/ `CONSTRUCT` に対応する（`P2A-14`）。

**未解決**: **ストアの応答全体は一度メモリに載る**（上限は API のメモリを守らない。時間の上限が事実上の防波堤）/ ストアの仕事は減らない / `CONSTRUCT` / `DESCRIBE`（`P2A-14`）。

テストは 633 → **655 件**。8 種類の変異で検出力を確認した（切り詰めない・ヘッダを出さない・アクセスログに切り詰め後の行数を記録する・上限ちょうどで切る・`boolean` の早期 return を外す・判定できない応答を 0 行とする・`CONSTRUCT` を許す・元の辞書を破壊的に書き換える）。**うち 1 つは最初「生き残った」** — `boolean` の早期 return を外しても後続の判定が同じ結果を返すため。`boolean` と `results` の両方を持つ応答で優先順位を固定するテストを足して意味のある分岐にした。

`P2A-14` の補足（**未着手**。`P2A-08` の実測で発見）: `CONSTRUCT` / `DESCRIBE` はガードで 400 で断っている。対応するには 3 つの設計が要る — (1) `FusekiStore.query` の内容交渉（クエリの形に応じて `Accept` を変える）、(2) 戻り値の型（いまは `dict`。RDF を返すなら別の型か表現が要る）、(3) **トリプル数の上限**（行数の上限とは別の量である）。

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

`P2A-12` の補足（**完了**。2026-09-10。`P2B-03` で発見）: **緑のチェックが何も検証していない状態を作れる。**

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

`P2A-11` の補足（**完了。2026-09-11**。`P2B-11` で発見）: OpenAPI のパスパラメータ名が揺れていた。`namespaces.py` だけが `/namespaces/{name}`、他のルータは `/namespaces/{namespace}` を使っていた。

**直した**: `namespaces.py` の 5 つのハンドラ（取得・削除・ロールの一覧・付与・取り消し）を `{namespace}` に統一した。**OpenAPI のパスパラメータが `namespace` 31 / `version` 8 / `principal_id` 1 になり、`name` は 0 件**であることを確認した（`app.openapi()` を実際に読んで数えた）。TS 型を再生成して Web のビルドも通した。

**`POST /namespaces` の本文の `name` は変えていない。** パスパラメータではないし、名前空間そのものの属性名である。

**踏んだこと**: 機械的な置換で `get_namespace` のローカル変数（既に `namespace` という名前だった）とパスパラメータが衝突し、**404 のメッセージが `Namespace` オブジェクトの repr になる**状態を作った。mypy が `Incompatible types in assignment` で捕まえた。取得結果の変数を `found` に改名した。

- **なぜ問題か**: 生成される TypeScript の型（`just gen-api`）でパラメータ名が経路ごとに違い、呼び出し側が混乱する。ドキュメントの説明も一貫しない
- **なぜ直していないか**: **パスパラメータ名の変更は URL を変えないので後方互換だが、生成される型の名前が変わる**。Web（`P2A-03`）が型を使い始める前に揃えるのが安い。今揃えると、まだ誰も使っていない型を変えるだけで済む
- **どちらに揃えるか**: `{namespace}` が多数（5 経路 対 3 経路）で、意味も明確
- **出典**: `P2B-11` の実装後に OpenAPI の経路一覧を確認して発見（2026-09-10）

`P2A-13` の補足（**完了。2026-09-11**。`P2B-14` の検証中に発見）: **ポート衝突の回避手段が CLI に届いていない。**

CLAUDE.md は「ポート 3030 が別プロジェクトと衝突する場合は `FUSEKI_PORT=3131` を指定する。テストも同じ変数を読む」と書いているが、**`scripts/check-questions.py` は `FUSEKI_PORT` を読まない**。`Settings.sparql_query_endpoint`（`SPARQL_QUERY_ENDPOINT`、既定 `http://localhost:3030/{dataset}/sparql`）という完全な URL を使うためである。

**症状が分かりにくい。** 3030 を別プロジェクトの Fuseki が握っていると、そちらへクエリが飛んで **HTTP 405** になり、`0/11 が満たされていません` と表示される（実測。**質問が落ちたように見えるが、実際は違うサーバに当たっている**）。`SPARQL_QUERY_ENDPOINT` を明示すれば 11/11 になる。

**直した**（2026-09-11）: `Settings` に `fuseki_port`（`FUSEKI_PORT`）を足し、`model_validator(mode="after")` が **4 つのエンドポイント**（query / update / GSP / admin）の既定値の `:3030/` を差し替える。

- **4 つ全部に効かせる。** 1 つだけ直すと「一部だけ動く」という分かりにくい状態になる
- **明示的に渡されたエンドポイントは書き換えない。** `model_fields_set` で判定する。デプロイ環境では Bicep が内部 ingress の FQDN を注入するので、そちらが常に勝つ
- **既定値の文字列から `:3030/` を差し替える形にした。** URL の形を 2 か所に書くと片方だけ直す事故が起きる。代わりに「既定値に `:3030/` が入っていること」をテストで固定した（`_DEFAULT_FUSEKI_PORT` と既定値が揺れると `FUSEKI_PORT` が黙って効かなくなる）

**テストで踏んだこと**: 既定値のテストが**環境依存だった**。このリポジトリでは 3030 が衝突するとき `FUSEKI_PORT=3131` を設定して pytest を回すので、それが「既定は 3030」を壊した。`monkeypatch.delenv` で関連する 5 つの環境変数を消す `autouse` フィクスチャを置いた。

**出典**: `P2B-14` の最終検証で `just check-questions` 相当を回したときに踏んだ。

`P2A-10` の補足（**完了。2026-09-11**。`P2A-09` で発見）: `scripts/bootstrap-db.py` と `scripts/check-questions.py` が `print` で日本語を出しており、**Windows では cp932 のバイト列になる**。周りのシェルスクリプトの `echo` は UTF-8 を出すため、azd のフックのログに 2 つのエンコーディングが混ざる。cp932 に無い文字（絵文字・ダッシュ）を出そうとすると `UnicodeEncodeError` で**スクリプトごと落ちる**。`P2A-09` で作った `ontology_core.console` の `say` / `warn` に置き換えれば済む。**害は「ログが読みにくい」に留まる**ため優先度は低いが、`bootstrap-db.py` は権限分離の失敗を運用者に説明する経路なので、読めないと困る場面がある。

**直した**（2026-09-11）: `scripts/bootstrap-db.py` / `scripts/check-questions.py` / `scripts/init-local-storage.py` の `print` をすべて `say` / `warn` に置き換えた（`file=sys.stderr` の直書きも `warn` に寄せた）。

**戻ってこないように機械的な検査を足した。** `scripts/lint-shell.sh` が `scripts/*.py` の行頭の `print(` を検出する（検査に歯があることは変異で確認した）。同じ間違いを人の目で守り続けるのは無理である。

`P2A-08` の補足: 現在は値が保持され Bicep が注入しているが強制されていない。任意の SPARQL に LIMIT を後付けするのは副問い合わせや CONSTRUCT で壊れるため、安価で正しい手段が無い。README と `config.py` に未強制であることを明記済み。

---

## Phase 2 柱 B — 運用し続けられる

すべて [ADR-0009](adr/0009-ontology-operations.md) が根拠。決定番号を併記する。

| ID | 内容 | ADR-0009 | 優先 | 状態 |
|---|---|---|---|---|
| `P2B-01` | OWL 推論器（ELK）を CI へ前倒し | 決定 1 | 高 | **完了**（2026-09-11） |
| `P2B-02` | 保持ポリシーの実装（ストアに載せる版の制御） | 決定 2 | 高 | **完了**（2026-09-10） |
| `P2B-03` | 廃止のライフサイクル（`owl:deprecated` + 後継） | 決定 3 | 高 | **完了**（2026-09-10） |
| `P2B-04` | 名前空間と用語の責任者 | 決定 4 | 高 | **完了**（2026-09-10） |
| `P2B-05` | アクセスログの実装（ADR-0006 §4、健全性指標の原資料） | 決定 5 | 高 | **完了**（2026-09-10） |
| `P2B-06` | 健全性指標の集計と提示 | 決定 5 | 中 | **完了**（2026-09-10） |
| `P2B-07` | 想定質問を SPARQL テストとして CI で実行 | 決定 6 | 高 | **完了**（2026-09-09） |
| `P2B-08` | `reason` / `diff` を書き、参照時に返す | 決定 7 | 中 | **完了**（2026-09-09） |
| `P2B-09` | 意味的差分の計算と差分レビュー | 決定 7 | 中 | **計算は完了**（2026-09-10。**画面は `P2A-03`**） |
| `P2B-10` | 領域間マッピング（SKOS `closeMatch` 等） | 決定 8 | 中 | **完了**（2026-09-11） |
| `P2B-11` | 監査を読み出す API | 決定 7 | 中 | **完了**（2026-09-10） |
| `P2B-12` | 削除と公開の競合を閉じる（行ロック） | — | 中 | **完了**（2026-09-11） |
| `P2B-14` | 想定質問をデプロイ済みの名前空間に紐づける | 決定 6 | 中 | **完了**（2026-09-11） |
| `P2B-15` | 推論で導出された含意をストアに射影するか決める | 決定 1 | 低 | **完了**（2026-09-12。**射影しない**) |
| `P2B-16` | 質問集合にも四眼原則を掛けるか決める | 決定 6 | 低 | **完了**（2026-09-12。**質問集合には掛けず、`approve` が基準の出自を見る**） |
| `P2B-17` | 領域間マッピングをストアに射影するか決める | 決定 8 | 低 | **完了**（2026-09-12。**射影せず Turtle で書き出す**） |
| `P2B-18` | マッピングの先が廃止されたときに気づく | 決定 3・8 | 中 | **完了**（2026-09-12） |
| `P2B-19` | 公開済みオントロジーを含む名前空間をどう退役させるか | — | 中 | **完了**（2026-09-12） |
| `P2B-20` | 孤児の `_state.json` を検出する | — | 低 | **完了**（2026-09-12） |
| `P2B-21` | 廃止された先を指すマッピングの数を健全性指標に出す | — | 低 | 未着手 |
| `P2B-22` | `ASK` のアクセスログが `returned_row_count = 0` を記録する | — | 低 | 未着手 |
| `P2B-23` | `CONSTRUCT` / `DESCRIBE` の結果にも廃止済み用語の警告を出す | — | 低 | 未着手 |

`P2B-20` の補足（**完了。2026-09-12**）: 設計は [ADR-0033](adr/0033-orphan-manifests.md)。ADR-0013 に報告の欄を 2 つ増やした（退役の分を含む）。

`reconcile` の `orphan_blobs` は **`.ttl` だけ**を列挙するので（`list_versions` が `.endswith(".ttl")` で絞っている）、`versions/<ns>/_state.json` は視界に入っていなかった。害は小さい（誰も読まないファイルが 1 つ残る）が、**「検出できていない」ことは検出できていないままだった**。

**設計上の要点**:

- **報告して削除する**（決定1）。`orphan_blobs`（TTL）と扱いを分けるのは、**判断できる根拠が違う**から — TTL は**正本**（不変条件7）だが、マニフェストは PostgreSQL の状態の射影であって正本ではない（ADR-0010 決定7）。`graphs_removed` と同じ側である。**正本ではないものに対して保守的である理由が無い**
- **削除の口はマニフェストしか指せない形にする**（決定2）。`OntologyBlobStore` にはこれまで削除のメソッドが 1 つも無かった。足したのは `delete_manifest(namespace)` だけで、**引数は名前空間名でありパスではない** — パスは `manifest_path_for` が組み立てるので、**このメソッドで `.ttl` を消すことは構造的にできない**。**署名の狭さが強制である**（規約やレビューで守るより、呼べない形にするほうが強い）
- **消しても報告から消さない**（決定3、ADR-0013 決定5 と同じ判断）。毎回出るなら削除経路に取りこぼしがある
- **判定は PostgreSQL に置く**（決定4）。「マニフェストがあるのに名前空間が無い」。`.ttl` の有無は見ない
- **名前空間名として使えない段は自動削除しない。** `delete_manifest` は名前空間名を検証する契約なので、そこを回避して消しに行かない（不変条件5）。報告に残して運用者に委ねる

**却下したもの**: 報告するだけにする（`orphan_blobs` と揃える理由が無い — **性質の違うものを扱いが同じという理由だけで揃えると、「なぜ消さないのか」の答えが「他と同じだから」になる**）/ `list_versions` の `.ttl` の絞り込みを外して一般化する（**`orphan_blobs` の意味が変わる**。報告を読んだ運用者が「消してはいけない TTL」と「消してよいマニフェスト」を手で選別することになる）/ 一般的な `delete_blob(path)` を追加する（**TTL を消せる口を開ける**）。

テストは 818 → **829 件**。9 種類の変異で検出力を確認した。**うち 1 件は最初「生き残った」** — `list_manifests` が `.ttl` も返す変異が、TTL の無い名前空間しか作らないテストでは観測できなかった。**孤児の TTL とマニフェストが別の欄に出ること**（決定1 の本題）を固定するテストを足して 9/9 になった。

`P2B-19` の補足（**完了。2026-09-12**）: 設計は [ADR-0032](adr/0032-namespace-retirement.md)。ADR-0024 決定4 の約束を果たし、ADR-0013 に補記を追加した。

`DELETE /namespaces/{name}` の 409 が「公開済みオントロジーを含む名前空間の削除は Phase 2（監査経路）で対応します」と約束していた中身である。ADR-0024 決定4 が挙げた 3 つの選択肢のうち「**正本を残し、射影だけ止める**」を採った（同決定も「2 が筋が通っている」と書いていた）。

`POST /namespaces/{ns}/retire`（`owner`、`reason` 必須）と `POST .../unretire`。**どちらも監査に残る。**

**`P2B-C1` が設計を決めた。** この作業のためにローダを読んでいてマニフェストの schema の食い違いが見つかり、そこから得た規則が退役の表現を決めた — **安全に関わる指示を新しい schema にだけ載せてはいけない**（古いローダが無視すると事故になる）。

**設計上の要点**:

- **退役は状態であって削除ではない**（決定1）。`retired_at` が NULL かどうかが唯一の判定。文字列の状態機械にしない（名前空間の状態が他に無く、値を足すたびに移行が要る形を作らない）
- **射影の停止は、古いローダが既に従う欄で表現する**（決定2）。マニフェストの `current` を `null` にし、全版の `projection` を **`skip:retired`** にする。`projection` は schema 2 のローダがそのまま従う欄で `skip:*` も既に実装済みなので、**ローダを 1 行も変えずに退役が効く**。`retired: true` も載せるが**観測のためだけ**で、無視されても安全は崩れない
- **退役したらそのときに射影を止める**（決定3）。データセットを消し、全版の `projected_at` を `NULL` に戻す（`unretire` で `reconcile` が拾えるようにするため。不変条件10）。**データセット削除の失敗で退役は失敗させない**（不変条件3）— マニフェストが既に `skip:retired` なので次の再構築で必ず止まる
- **`reconcile` は退役した名前空間を再射影せず、残骸を消す**（決定4）。`projected_at` を NULL にした結果、**何もしないと即座に再射影してしまう**。残骸の削除は ADR-0013 の「観測された乖離を直す」そのものである
- **退役が止めるのは「内容を増やすこと」と「射影を通じて提供すること」だけ**（決定5）。publish / submit / approve / reject / SPARQL / 想定質問の改訂 / マッピングの宣言は **409**。版の一覧・決定記録・監査・PROV-O・健全性・差分は通る（**それを残すために退役している**）。ロール・用語の責任者・マッピングの取り消しも通る（**片付けはできるべき**）
- **SPARQL を 409 にするのが決定5 の要点である。** データセットを消した後にクエリを通すと**0 行が静かに返り**、エージェントは「該当なし」と読んで回答を作る。ADR-0025 決定3 以来ずっと避けている形である
- **`DELETE` の 409 は残し、メッセージを退役へ向ける**（決定6）。不変条件7 は変わっていない。**公開済み TTL が無い名前空間の `DELETE` はこれまでどおり通る**（作ってすぐ消す経路を塞がない）
- **一覧から隠さない**（決定7）。隠すと `unretire` できない
- **戻せる**（決定1）。**正本が無傷なのに回復手段が無いのは不合理である** — 誤操作の回復に DB を直接触らせるのは運用手順として最悪である

**却下したもの**: 恒久的に拒否する（409 のメッセージを直せば筋は通るが、使われない名前空間が一覧と射影に残り続ける）/ Blob も消す（不変条件7 に正面から反する）/ **`.ttl` を別の接頭辞へ移す**（ローダを変えずに済み古いローダにも確実に効くが、**移動は copy + delete で原子的ではなく**、途中で落ちると正本を失う。`blob_path` の書き換えも不変リビジョンの周辺を侵す）/ **`schema` を 3 に上げて `retired` を必須にする**（`P2B-C1` で踏んだ罠そのもの。古いローダは未知の schema を拒否して名前空間を丸ごとスキップする — 退役では偶然「安全」に見えるが、**逆向きの変更（解除）では解除が効かない**）/ 不可逆にする / 退役中も SPARQL を通す。

**未解決**: **`unretire` の直後はストアが空である**（`reconcile` を回すか次のレプリカ再作成を待つ。応答の `note` で伝えている）/ **退役した名前空間の名前は再利用できない**（意図した振る舞い — 同名で作り直せると監査証跡が別のオントロジーの記録と混ざる）/ 一覧の絞り込みは無い（要求が出てから）。

テストは 795 → **818 件**。20 種類の変異で検出力を確認した。**うち 1 件は最初「生き残った」** — `set_retired` の「`at=None` なら他の引数を無視して列を消す」という契約は、**呼び出し側（`unretire`）が `actor=None` を渡すのでサービス経由では観測できなかった**。リポジトリの契約として直接検査するテストを足して 20/20 になった。

`P2B-17` の補足（**完了。2026-09-12。射影せず書き出す**）: 設計は [ADR-0031](adr/0031-mapping-export.md)。ADR-0023 決定7 に決着を付けた。

**`P2B-15` と同じ問題ではなかった。** ADR-0028（含意を射影しない）の理由の 1 つは「ELK の結論が不完全なので載るのは中途半端な部分閉包になる」だったが、**マッピングは正本（PostgreSQL）にあり完全で、`owner` が `reason` 付きで宣言した確定した事実**である。同じ結論になるとは限らなかった。

**射影しない理由は 4 つで、うち 2 つは `P2B-18` を実装した後に見えた。**

| # | 理由 |
|---|---|
| a | **ローダは PostgreSQL を読まない。** 射影したマッピングはレプリカ再作成で消える。`reconcile` を拡張する道はある（マッピングは正本にあるので**再計算ではなく再射影**で足りる）が、**ローダに DB 依存を入れる**のは ADR-0002 の「ストアは Blob だけから作り直せる」を変える |
| b | **どの版にも属さないので置き場所が無い**（決定7 の元の理由）。専用グラフは**エージェントに `GRAPH` 句の知識を要求する** |
| c | **`P2B-18` で作った区別が射影の経路では全部消える。** 射影したグラフを引いた側には終点の生死が分からない。**「少しだけ正しい」情報を静かに置くことになる**（ADR-0028 決定2 が「少しだけ推論する」を拒否したのと同じ形） |
| d | **権限の粒度が合わない。** ADR-0030 決定1 は「相手を読めなければ生死を返さない」と決めたが、射影したグラフは名前空間のデータセット単位でしか権限を持てない |

**代わりに `GET /namespaces/{ns}/mappings/export` が `text/turtle` を返す**（決定2）。ADR-0026 の PROV-O 書き出しと同じ形で、**表現ごとに別の URL を持つ流儀も揃えた**（内容交渉にしない）。これで「SPARQL だけを使うクライアントからは見えない」が **「自分のストアへ読み込める」**に変わる。

**素の SKOS トリプルと記述ノードの両方を出す**（決定3）。素のトリプルはそのまま引ける（ADR-0023 決定1 が述語を SKOS に限ったのは論理的帰結を持たないからで、載せても推論器が制約を流し込まない）。記述ノードに `reason` と終点の生死を載せる — **標準に写すために情報を落とさない**（ADR-0026 決定4 と同じ判断）。

**記述ノードに IRI を与える（空白ノードにしない）。** 再取得したときに同じノードだと分からず、2 回の書き出しを差分比較できないためである。用語 IRI は百分率符号化する — このプロジェクトの用語 IRI はほぼ必ず `#` を含むので、**そのまま連結すると 1 つの IRI に `#` が 3 つ並び、RFC 3986 の意味で妥当な IRI ではなくなる**（rdflib は緩いので黙って通し、受け取った側で初めて壊れる）。

**`ont:targetStatus` は `unknown` でも出す**（決定4）。ADR-0016 決定5 / ADR-0020 決定3 / ADR-0021 決定1 / ADR-0022 決定5 / ADR-0025 決定3 / ADR-0026 決定5 / ADR-0027 決定5 / ADR-0030 決定1 と**同じ原則の 9 例目**。

**書き出しは「読んだ人の行為」であって、静かな事実ではない**（決定5）。素の SKOS トリプルだけを読む消費者は終点の生死を見落とすが、**書き出しは運用者が明示的に取得して自分のストアへ読み込む行為**で、射影は**エージェントが知らないまま引く静かな事実**になる。同じ情報の欠落でも**誰がそれを引き受けるかが違う**。

**却下したもの**: 専用の名前付きグラフへ射影する / 既定グラフへ混ぜる（**ADR-0010 決定6 を壊す**。`P1-C1` の Critical はまさにこれだった）/ 内容交渉にする（ADR-0026 が別の URL を選んでいる。流儀を 2 つ持たない）/ 素の SKOS トリプルだけを書き出す（**`reason` が落ちる**。ADR-0023 決定5 は `reason` を必須にした）/ 何もしない（**ADR-0023 が挙げた問題が 1 ミリも動かない**。決定だけ書いて問題を残すのは「宣言はあるが強制されていない」の裏返しである）。

**開け直す条件を 3 つとも満たしたときに限る**（決定6）。(1) ローダがマッピングを再射影できる、(2) 終点の生死が射影したグラフでも伝わる、(3) 権限の粒度が合う。

**副産物**: `TURTLE_MEDIA_TYPE` を `ontology_core.turtle` に移した。PROV-O とマッピングの 2 か所で値が割れないようにするためで、ルータからルータを import する形も避けられた。

テストは 769 → **792 件**。13 種類の変異で検出力を確認した。**うち 1 件は最初「生き残った」** — 用語 IRI の符号化をやめる変異が、**衝突を題材にしたテストでは検出できなかった**（全 IRI がスキームから始まるので、現実的な用語では衝突が作れない）。検査すべき性質は衝突ではなく**「ノード IRI が素片を持たずちょうど 3 段になる」**だったので、そちらを固定するテストに差し替えて 13/13 になった。**最初に書いた根拠（衝突）が実際には成り立たないことを、変異テストが教えた。**

`P2B-18` の補足（**完了。2026-09-12**）: 設計は [ADR-0030](adr/0030-mapping-target-lifecycle.md)。ADR-0023 の「受け入れるコスト」に決着を付け、ADR-0017 に補記を追加した。

**放置すると、エージェントが廃止された用語を根拠に回答を作る。** 経理が `finance:PremiumCustomer` を正しく縮めても（削除ではなく廃止 + 後継）、営業の `sales:GoldCustomer → finance:PremiumCustomer` はそのまま残る。ADR-0017 決定3 が `sparql_query` に廃止警告を付けて塞いだ穴が、**マッピングの経路では開いていた**。

**本当の論点は権限だった。** `sales` の分析者に `finance` の用語の生死を教えてよいか。教えないと営業は死んだ用語を指したままになるが、教えると `finance` を読めない主体に `finance` の内容が漏れる。

**設計上の要点**:

- **相手の名前空間を読む権限が無ければ `unknown` を返す**（決定1）。**`active` とは言わない。** 不変条件11（権限の既定は拒否）の素直な適用であり、同時に「調べられなかった」を「問題なし」と書かないという判断でもある（ADR-0021 決定1 / ADR-0025 決定3 / ADR-0026 決定5 / ADR-0027 決定5 と**同じ原則の 8 例目**）
- **状態は 4 つ。`active` と `absent` と `unknown` を混ぜない**（決定2）。`absent`（相手の現行版にその IRI が無い）は「何も指していないマッピング」であり、廃止された先を指すのと同じくらい直すべきものである。**そして `unknown` にも丸めない** — 調べた結果として「無い」と分かったことと、調べていないことは違う。`unknown` の理由は 5 つに分けて返す（外部語彙 / 権限が無い / 承認済みの版が無い / 読めなかった / 上限）
- **外部語彙を `absent` にしない**（決定2）。SKOS の用語は「存在しない」のではなく「**このシステムの管理外**」である
- **宣言のときには検査しない**（決定3、ADR-0023 決定6 を維持）。宣言が Blob と他の名前空間の可用性に依存するし（不変条件2・3）、外部語彙には効かないし、**肝心の事象（宣言の後に廃止される）には効かない**
- **名前空間の数に上限を置く**（決定4、`MAX_TARGET_NAMESPACES = 10`）。マッピングは任意の IRI を指せるので**対象の数に上界が無い**。超えた分は理由付きの `unknown`。**コストを決めるのはマッピングの件数ではなく対象の名前空間の数**なので、名前空間ごとに 1 回だけ TTL を読む
- **廃止する側にも見せる**（決定5）。`ProblemKind.MAPPED_BY_OTHERS`。`term_mappings` は PostgreSQL にあり、**自分の用語に対する `incoming`** なので権限の論点が生じない
- **しかし `approve` はブロックしない**（決定6）。**ブロックすると `N` が `M` を人質に取れる** — マッピングを張るだけで相手の廃止を封じられ、張られた側は相手の行を消せない（ADR-0023 決定3 により逆向きの書き込み口が無い）。ADR-0017 決定2 の「ブロックが妥当なのは従う正当な手段が常にあるときだけ」に反する
- **廃止された先を指すことは異常ではない**（決定7）。旧用語を指すマッピングを保持したまま後継を指す新しいマッピングを足すのが正しい移行である（ADR-0017 決定2 が同じ理由で報告のみに留めている）

**却下したもの**: 権限に関わらず生死を返す（**`active` と `absent` を区別すると存在の oracle になる**。区別を捨てれば oracle は狭まるが、今度は決定2 が禁じた丸めになる。**権限で切るのが、どちらも犠牲にしない唯一の線**）/ 廃止された先を指すマッピングを自動で取り消す（ADR-0023 決定3 の裏返しをこちらが行うことになる。そして決定7 のとおり正当にありうる）/ 廃止済みの先への宣言を拒否する（決定3 の理由）/ `M` の承認をブロックする（人質）/ 健全性指標に足す（**指標が他の名前空間の Blob の可用性に依存する**。`P2B-21` として記録）。

テストは 751 → **769 件**。12 種類の変異で検出力を確認し、**すべて検出された**。ただし**変異テストの運用で 1 つ失敗した** — 復元表を「ファイルの基底名」で作ったため `routers/mappings.py` と `repositories/mappings.py` のキーが衝突し、**片方が復元されないまま残った**（commit 直前に `diff` で気づいた）。CLAUDE.md に記録した。

`P2B-16` の補足（**完了。2026-09-12**）: 設計は [ADR-0029](adr/0029-criteria-authorship.md)。ADR-0022 に補記を追加し、CLAUDE.md の不変条件14 を更新した。

**不変条件14 は宣言されていたが強制されていなかった。** 「受け入れ基準を、その基準で審査される側が書き換えられてはならない」と書いてあるのに、ADR-0022 決定6 は「防止ではなく可視化」に留めていた。**不変条件11 が名指しした形**（「強制していない」を「強制している」と誤認させる）そのものである。

**四眼原則が塞いでいなかった穴を数えた。**

| # | 経路 | 四眼原則 | 新しい検査 |
|---|---|---|---|
| 1 | 著者が版を書き、基準を緩め、自分で承認 | **止める** | — |
| 2 | **著者が版を書き、基準を緩め、同僚が承認** | 止めない | **止める** |
| 3 | 基準を先に定めた責任者が、他人の版を承認 | 止めない | 止めない（**正常な統制**） |

**塞がっていないのは 2 だけ**で、しかも「一人が全部やる」形ではないので四眼原則の考え方では捉えられない — 問題は**「審査される成果物の著者が、その合格条件を書いた」**ことにある。そして **3 は止めてはいけない**（責任者が基準を定め、他人の成果物を審査するのは統制のあるべき姿）。**時刻の境界がこの 2 つを分ける。**

**設計上の要点**:

- **四眼原則を質問集合そのものには掛けない**（決定1）。publish → submit → approve のライフサイクルを付けると機構が 2 倍になり、最初の改訂を誰が承認するかが決まらず、**そして経路 2 を塞がない**（著者が改訂を出し同僚が承認し、そのあと同僚が版も承認すれば条件は満たされるのに実態は同じ）
- **境界は版の `created_at`（publish した時刻）**（決定2）。版の内容は publish で固定されるので（不変条件7）、著者はそこで合否を知っている。submit の時刻にすると publish から submit までの間の書き換えを見逃す
- **「緩くなったか」は判定しない**（決定2）。改訂前後で同じ版を評価して比べる実装は書けるが、(a) 評価は予算付きで未評価になりうる、(b) 「質問が減った」と「質問が緩くなった」は別物、(c) 判定できなかった場合の扱いを決めるとまた同じ問題に戻る。**測れないものを条件に入れない**
- **`require_two_person_approval` に従う**（決定3）。**新しいスイッチを作らない** — 別に作ると片方だけ切って「四眼原則を有効にしたつもり」になれる。ADR-0014 決定4 が四眼原則を名前空間ごとにした理由（運用者 1 人のデプロイが動かなくなる）がそのまま当てはまる
- **`platform-admin` も飛び越えられない**（決定4、不変条件12）
- **逃げ道は「別の主体が基準を再確認すること」**（決定5）。内容が同じ改訂でよい。**基準を消させるのではなく、基準に別の主体の署名を付けさせる**
- **事実の検出と、止めるかの判断を分ける**（決定6）。レビュー用の口（`POST .../versions/{v}/questions/run`）は `criteria_self_revised` を返し、**四眼原則が無効な名前空間でも埋まる**

**却下したもの**: 現状維持（**見えていることと、承認者が見ることは違う**。承認者は「全部合格」という結果を見るが、その基準がこの版のために書き換えられたことは監査を別に引かなければ分からない）/ 承認者側で判定する（経路 1 は四眼原則が既に塞いでいて、**塞ぐべき経路 2 に効かない**）/ 緩くなった改訂だけを止める/ `submitted` 時刻を境界にする/ 質問集合を版ごとに持たせる（ADR-0022 決定2 が既に却下）。

テストは 737 → **751 件**。8 種類の変異で検出力を確認した。**うち 1 件は最初「生き残った」** — 時刻の境界を `<` から `<=` に変えると**版と同時刻の改訂が通る**。`created_at` は `now()`（トランザクション開始時刻）なので、版と改訂を同一トランザクションで作ると同じ値になる。そのとき著者は両方を書いているので止めるのが正しい。境界を固定するテストを足して 8/8 になった。

`P2B-15` の補足（**完了。2026-09-12。射影しない**）: 設計は [ADR-0028](adr/0028-no-entailment-projection.md)。

**「やる/やらない」を決める項目だったので、決定と、決定によって失われるものの埋め合わせを実装した。**

**射影しない理由は 4 つあり、どれか 1 つでも致命的である**（決定1）。

| # | 理由 |
|---|---|
| a | **ELK の結論は実用的なオントロジーではほぼ常に不完全**（ADR-0021 の実測）。載るのは主張でも完全な演繹閉包でもない**中途半端な部分閉包**で、エージェントには「導出されなかった」と「計算されなかった」を区別する手段が無い |
| b | **射影は再構築可能でなければならない**（不変条件1）。ローダは Blob の `.ttl` だけを読み推論器を走らせないので、**導出トリプルはレプリカ再作成で静かに消える**。`reconcile`（ADR-0013）も含意は見ていないので気づけない |
| c | **主張と導出が区別できなくなる。** ADR-0006 の中核価値「誰が承認した定義に基づく答えかを説明できること」を直接壊す |
| d | **不変リビジョンの中身が推論器の版で変わる**（不変条件7）。ELK 0.6.0 → 0.7.0 で同じ版の意味が変わる |

**クエリ時推論（Jena の `ja:InfModel`）も有効にしない**（決定2）。最大の理由は **「少しだけ推論する」は「推論しない」より危険**だから — 一部の含意が返ると、エージェントは「このエンドポイントは推論する」と結論し、返らなかった含意を「成り立たない」と読む。何もしなければ少なくとも一貫して何もしない。

**代わりにプロパティパスを案内した**（決定3）。`rdfs:subClassOf+` / `rdf:type/rdfs:subClassOf*` / `skos:broader+`。パスは推論ではなく**グラフの到達可能性**なので、返ってきた経路はすべて誰かが承認した公理である（(c) の問題が無い）。MCP の `sparql_query` のツール説明・Core API の docstring・README に書いた（**エージェントはヘッダも ADR も読まない**ので、ツール説明に書かなければ届かない。ADR-0017 決定3 と同じ判断）。**パスで届かない含意があることも書いた** — 書かないと「パスを使えば推論と同じ」と読まれる。

**勧めた手段が実際に動くことを実物の Fuseki で確かめた**（`test_no_inference.py`）。`Premium ⊑ Customer ⊑ Party` を承認したうえで、(1) 直接の `?s rdfs:subClassOf ex:Party` が `Customer` しか返さないこと、(2) `rdfs:subClassOf+` が両方返すこと、(3) 個体の型も同様であることを固定した。**将来 `ja:InfModel` を入れると (1) が落ちる** ので、ADR-0028 決定5 の 3 条件を確認する手掛かりになる。

**却下したもの**: 導出トリプルを別の名前付きグラフに射影する（(c) は解けるが (a) と (b) が残り、「取りに行ったら空だった」が「含意が無い」と読める状態を作るだけ）/ `approve` のときに含意を計算して TTL に焼き込む（**不変条件15 に反する** — 承認時の検査は正本の TTL に対して行うと決めているが、これは検査ではなく正本の書き換えである。加えて ADR-0005 決定3 と (a)(c)）/ エージェント向けの推論用エンドポイント（ADR-0005 が既に却下、新しい事実は無い）/ プロパティパスを案内せず「推論しない」とだけ書く（**正確だが役に立たない**。エージェントは直接の `rdfs:subClassOf` で満足して不完全な答えを自信を持って返す）。

**開け直す条件を 3 つとも満たしたときに限る**（決定5）。(1) 完全な OWL 2 DL 推論器が配布物に入る、(2) ローダが起動時に含意を再計算できる、(3) 導出トリプルが別グラフに載り不完全性の印が付く。**1 つだけでは足りない** — 1 つを満たしただけで進めると残り 2 つの問題がそのまま本番に出る。

テストは 733 → **737 件**。

`P2B-10` の補足（**完了。2026-09-11**）: ADR-0009 決定8 の実装。設計は [ADR-0023](adr/0023-cross-domain-mappings.md)。

`PUT / GET / DELETE /namespaces/{ns}/mappings`（宣言と取り消しは `owner`、参照は `data-analyst`）+ MCP の `term_mappings` ツール。

**実測で ADR-0009 決定8 の選択肢を 1 つ却下した。** 同決定は SKOS の `*Match` と並べて `owl:equivalentClass` も候補に挙げていたが、**この 2 つは同じものではない**。`P2B-01` で入れた ELK で確かめた — 営業と経理の「優良顧客」を、互いに素なクラスの下に置いたまま結ぶ。

| 結んだ述語 | ELK の結論 |
|---|---|
| `owl:equivalentClass` | **充足不能クラス 2 件**（`a:GoodCustomer` と `b:GoodCustomer` の**両方**） |
| `skos:exactMatch` | 充足不能クラスなし |

**`equivalentClass` で結んだ瞬間に、両方のクラスがインスタンスを持てなくなった。** 片方が壊れるのではなく両方である。しかも**この壊れ方は承認では止まらない**（推論器は CI にしかいない。ADR-0021 決定5）。マッピングは領域が違うから張るもので、領域が違えば制約が食い違うのが普通なので、**論理的帰結を持つ述語は構造的に危ない**。

**設計上の要点**:

- **述語は SKOS の 5 つに限る**（`exactMatch` / `closeMatch` / `broadMatch` / `narrowMatch` / `relatedMatch`）。`owl:equivalentClass` と `owl:sameAs` は 422 で拒否し、**理由を添える**（ADR-0009 が候補に挙げていたので、使おうとする人が必ず現れる）
- **PostgreSQL に持ち、TTL には書かない**（決定2）。理由が 3 つある — (a) TTL に書くとマッピングが不変リビジョンに閉じ込められる、(b) レビューのライフサイクルがオントロジーの承認とは別、(c) **TTL に入れなければ推論器の視界に入らないので、論理的帰結を持つマッピングが物理的に作れない**
- **逆向きは自動で作らない**（決定3）。「A が B に exactMatch と言っている」と「B が A に exactMatch と言っている」は**別の事実**で、自動生成は**相手が宣言していない主張を相手の名前空間に作る**。代わりに `?direction=incoming` で両方向から見えるようにした
- **片側だけの主張は異常ではない**（`reciprocal=False`）。初期状態では片側だけが正常なので、警告にしない
- **述語が食い違ったら両方残して `disputed` で見せる**（決定4）。自動で片方に寄せる実装は、**相違を消す実装**である。ADR-0009 決定8 が守ろうとしているのは相違の記録そのもの
- **`broadMatch` の逆は `narrowMatch`。** 争いの判定を「同じ述語か」で書くと、**正しく宣言された非対称な対応がすべて争いになる**（変異テストで固定した）
- **取り消せるのは自分が張ったものだけ**（決定3）。消せてしまうと「相違が消されずに記録される」が成立しない
- **ターゲット用語の実在は検査しない**（決定6、ADR-0015 決定4 と同じ理由）。**外部語彙（SKOS、schema.org）へのマッピングが正当な主用途**なので、実在を要求するといちばん使いたい形が使えなくなる
- **健全性指標に `disputed_mapping_count` を足した**（8 項目目）。自動で寄せない代わりにここで可視化する
- **相手側の宣言は 1 クエリで引く。** 一覧の件数だけクエリが飛ぶと、マッピングが増えたときに一覧が使えなくなる

**却下したもの**: `owl:equivalentClass` を許す（上記の実測）/ マッピングを TTL に書く（不変リビジョンに閉じ込められる。推論器の視界に入る）/ 逆向きを自動生成する（相手が宣言していない主張を作る）/ **両側の `owner` の承認を要求する**（合意を強制すると、合意できない相違が記録されなくなる。ただし将来 `agreed` フラグとして足す価値がある）/ マッピング専用の名前空間を作る（名前空間は RBAC とデータセットの境界なので権限の設計が二重になる）/ 推移的閉包を計算する（`exactMatch` は推移的だが `closeMatch` は推移的でない。混ぜると誤った同一視を作る）。

**未解決**: **`P2B-17`**（ストアに射影するか。SPARQL だけのクライアントからは見えない）/ **`P2B-18`**（マッピングの先が廃止されても気づけない）/ 推移的閉包 / 片側だけの主張の集計（初期状態では正常なので雑音になる）。

テストは 575 → **618 件**。10 種類の変異で検出力を確認した（`equivalentClass` を受け付ける・逆向きを自動生成する・争いを「同じ述語か」で判定する・宣言なしでも `reciprocal` を真にする・宣言を `maintainer` に緩める・`incoming` に自分の宣言を混ぜる・取り消しで名前空間を見ない・無いマッピングの取り消しを成功にする・争い件数を常に 0 にする・始点と終点が同じマッピングを許す）。

`P2B-01` の補足（**完了。2026-09-11**）: ADR-0009 決定1 の「決定可能なものを機械が判定する層」の最後の 1 つ。設計は [ADR-0021](adr/0021-owl-reasoning-in-ci.md)。

`containers/reasoner`（Java / ELK 0.6.0 + OWL API 5.1.20）を CI で回す。`just check-reasoning` / `just test-reasoner`。

**実装して測ったら、ADR-0009 の前提が間違っていた。** 同 ADR は「推論器は充足不能クラスと矛盾を exact に検出する」と書いていたが、**ELK 0.6.0 はデータプロパティを扱えない**（`DataProperty` / `DataPropertyDomain` / `DataPropertyRange` / `FunctionalDataProperty`）。これらがあると整合性の判定そのものを諦める。**属性を 1 つも持たない業務用オントロジーは無い**ので、**結論が不完全になるのは例外ではなく通常の状態**である。同梱サンプル `samples/retail-core.ttl` も該当した（データプロパティ 2 個・ドメイン 2 個・レンジ 2 個・関数的 1 個）。ADR-0005 と ADR-0009 に補記を追加した。

| 入力 | OWL 2 EL の逸脱 | ELK の結論 | 結論は完全か |
|---|---|---|---|
| EL の中で整合 | 0 件 | 矛盾は検出されず | **完全** |
| **データプロパティ 1 個だけ** | **0 件** | 矛盾は検出されず | **不完全** |
| `rdfs:subClassOf` の左辺に `owl:unionOf` | **1 件** | 矛盾は検出されず | **完全** |
| `samples/retail-core.ttl`（公理 56 件） | 宣言漏れ 25 件 / 表現力 0 件 | 矛盾は検出されず | **不完全** |
| 互いに素な 2 クラスの共通部分 | 0 件 | **充足不能クラス 1 件** | 完全 |
| 個体が互いに素な 2 クラスに属する | 0 件 | **矛盾を検出** | 完全 |

**設計上の要点**:

- **「矛盾は検出されなかった」と「矛盾がない」を区別する。** フィールド名を `inconsistency_found` にし、`consistency_conclusive` と `incompleteness_reasons` を必ず併せて返す。要約も「矛盾は検出されませんでした（見逃しがありえます）」と書く。**ここで「矛盾なし」と書いたら、この作業は目的を失う**
- **OWLAPI の `isConsistent()` を使わない。** ELK の `IncompleteResult` を剥がして `boolean` だけを返すので、「完全に検査できた `false`」と「見逃しがあるかもしれない `false`」が区別できなくなる。ELK 拡張の `checkIsConsistent()` / `computeUnsatisfiableClasses()` を使う
- **プロファイル適合から不完全性は推測できない。両方向にずれる**（上の表）。データプロパティは OWL 2 EL に**含まれる**のに ELK は扱えず、`subClassOf` の左辺の `owl:unionOf` は逸脱なのに ELK は完全に扱える。両方向を `testdata/incomplete-datatype.ttl` と `beyond-el.ttl` で固定した
- **不完全さでは止めない。** データプロパティ 1 個で該当するため、止める設計にすると実用的なオントロジーが全部落ちる。止めるのは**読み込み失敗・矛盾・充足不能クラス**だけ。ELK は扱えない公理を無視するだけなので**誤検出しない**（見逃すだけ）。CI のゲートとしては偽陽性が無い方が価値が高い
- **`unsatisfiable_classes` は測っていないとき `null`**（`[]` は「測ったが 0 件」）。ADR-0020 の健全性指標と同じ扱い
- **プロファイル逸脱を「宣言漏れ」と「表現力」に分ける。** 同梱サンプルの 25 件はすべて `UseOfUndeclared*`（SKOS / SHACL の語彙を宣言せずに使っている）で、分けないと意味のある逸脱が埋もれる
- **`approve` の同期パスには入れない**（ADR-0005 決定3 を維持）。CI が検査するのはこのリポジトリの同梱物

**ライセンス**: ELK は Apache-2.0（`io.github.liveontologies:elk-owlapi:0.6.0`）。OWL API は Apache-2.0 / LGPL-3.0 のデュアルで **Apache-2.0 を選択**。shade jar の 67 個の依存を機械的に集計し、**コピーレフトが 1 つも無いこと**を確認した（`docs/third-party-licenses.md`）。**ROBOT は却下** — 配布 jar（82 MB）に `org/semanticweb/HermiT/` と `jfact` が入っていることを実際に確認したため（ADR-0005 決定4 に反する）。**HermiT / JFact が同梱されていないことは `reasoner-check.test.sh` が jar の中身を見て機械的に検査する**（依存を 1 つ足すと推移的に入りうるので、目視では守れない）。

**実装中に踏んだこと**:

1. **`owlapi-distribution:5.5.1` を足したら Turtle が読めなくなった。** ELK が引く `owlapi-rio:5.1.20` と uber-jar の rdf4j 5.0.2 が食い違い `NoSuchFieldError`。**OWL API は ELK が対応している版に揃える**（ADR-0021 決定4）
2. **`NoSuchFieldError` は `LinkageError` で `catch (Exception)` を素通りする。** プロセスが落ちて JSON が出なかった。依存の不整合は実際に起きる故障モードなので `Exception | LinkageError` を捕まえる。`OutOfMemoryError` はあえて捕まえない（JVM が壊れた後の結論は信用できない。落ちれば終了コードで CI が止まる）
3. **`json.load(sys.stdin)` は Windows で cp932 として読む。** 日本語ラベルを含む JSON が壊れた（CLAUDE.md の cp932 の罠の**入力側**）。`sys.stdin.buffer.read().decode("utf-8")` にした
4. **Git Bash の `-v "$(pwd):/work"` と `-w /work` は docker に届く前に壊れる。** `/work` が `W:/` に変換される。`MSYS_NO_PATHCONV=1` / `MSYS2_ARG_CONV_EXCL='*'` と `cygpath -m` が必要（CLAUDE.md に既にある罠だが、**同 ファイルの jq のコマンド例がその罠を踏んでいた**ので直した）
5. **シェルの単一引用符に囲まれた埋め込み Python のコメントにバッククォートを書くと shellcheck が SC2016 で落ちる**（コマンド置換と誤認する）
6. **shade は同名リソースを衝突させる。** `META-INF/NOTICE` がどれか 1 つで上書きされ、**Apache-2.0 §4d の NOTICE 継承が壊れていた**。`ApacheNoticeResourceTransformer` を入れたが、**既定のままだと「Copyright ... The Apache Software Foundation」が生成され、この成果物の著作権が ASF にあるかのように読める**。帰属表示を明示的に上書きした
7. **`getReasonerName()` が `null` を返す**（ELK 0.6.0）。jar 内の `pom.properties` から版を読む

**却下したもの**: ROBOT を使う（HermiT 同梱）/ OWL API を 5.5.1 に上げる（テストされていない組み合わせに結論の正しさを依存させる）/ プロファイル逸脱から不完全性を推測する（両方向にずれる）/ 不完全さでビルドを止める（実用的なオントロジーが全部落ちる）/ HermiT を既定にする（ADR-0005 決定4）/ `approve` に組み込む（ADR-0005 決定3）/ 導出された含意を射影する（`P2B-15` として分離）。

**未解決**: 利用者のオントロジーは CI の対象外（承認時は SHACL と廃止検査だけ）/ **結論がほぼ常に「不完全」なので運用者が読み流すようになりうる**（文言以外の対策は打っていない）/ ELK 0.6.0 と OWL API 5.1.20 に固定 / 依存全体のライセンス自動スキャンは Phase 4 のまま。

`P2B-06` の補足（**完了。2026-09-10**）: ADR-0009 決定5 の実装。設計は [ADR-0020](adr/0020-health-metrics.md)。同 ADR の言葉で言えば「**測っていないものは、致命的になるまで見えない**」。

`GET /namespaces/{ns}/health`（`data-analyst`）が 6 項目を返す。

| # | 項目 | 原資料 |
|---|---|---|
| 1 | `term_count` | **正本の TTL** |
| 2 | `unreferenced_count` / `unreferenced_ratio` | `term_access`（`P2B-05`） |
| 3 | `without_owner_count` | `term_owners`（`P2B-04`） |
| 4 | `approval_age_days` | `ontology_versions.approved_at` |
| 5 | `shacl_violation_count` | `ontology_core.shacl`（`P2A-05`） |
| 6 | `unprojected_version_count` | `ontology_versions.projected_at` |

**設計上の要点**:

- **用語の一覧は正本（Blob の TTL）から取る。ストアから取らない。** トリプルストアは再構築可能な射影であって正本ではないので（不変条件1）、**ストアが空のときにストアを数えると「用語数 0、未参照 0 件、責任者未設定 0 件」= 完全に健全という報告になる**。健全性指標として最悪の壊れ方であり、障害が起きているときに「健全」と言う
- **測れなかった項目は `null` にし、`0` にしない。** 加えて `unavailable` に理由を並べる。`P2A-05` で「検証できなかった」を「違反ゼロ」と混同しないと決めたのと、ADR-0016 決定5 で「計算できなかった」を「差分が無い」と混同しないと決めたのと、同じ形の判断である
- **「全部か無か」にもしない。** Blob へ到達できなくても、PostgreSQL だけで測れる項目（承認の古さ、未射影の版）はそのまま返す。**Blob の一時的な不調で未射影の版の数まで見えなくなってはいけない**
- **「一定期間再承認されていない用語」は版単位と解釈した。** このシステムの承認は版単位（ADR-0010 決定2）なので、用語単位の「再承認の古さ」は計算できない。**存在しない粒度をあるように見せない**（用語単位で測るには差分の連鎖を辿る必要があり、空白ノードの上限で穴があく。ADR-0016 決定5）
- **承認済み版が無ければ「用語 0」で正しい。** その名前空間はまだエージェントに何も渡していない。障害ではなく事実なので `unavailable` には入れず、`current_version` が `null` になることで区別できる
- **`data-analyst` で読める。** 件数だけを返すので個人を特定しない。**指標に強い権限を要求すると、指標が使われなくなる**（「測っていないものは見えない」という趣旨は、見られることが前提である）。用語 IRI の一覧は `include_terms=true` のオプトイン（応答を小さく保つためで、権限の話ではない）
- **総合スコアは出さない。** ADR-0009 が既に却下している（点数が下がった理由が行動に結びつかない）
- **SHACL 違反は構造上ほぼ常に 0 である**（`approve` がブロックするため）。`P2A-05` より前に承認された版と shape 自身の問題を拾うために残している

**却下したもの**: ストアから用語を数える（最悪の壊れ方）/ 指標を定期計算して保存する（まず「今の値が見える」ことが要る。古い値を今の値と誤解する危険が増える）/ 測れなかったら全体を 502 にする（DB だけで測れる項目まで見えなくなる）。

**変異テストで自分のテストの穴を 3 件見つけた。**

1. **外部語彙の除外を検証できていなかった。** `owl:Thing` を**目的語**にしか置いていなかったので、`iri_subjects` の側で落ちて `base_iri` の絞り込みが効いているか分からなかった。**主語に置く行**を足して固定した
2. **`term_iris_with_prefix` の単体テストが無かった。** `prefix` が空のときに全 IRI を返す変異が通ってしまった
3. **`draft` を未射影に数えない**ことを検証していなかった（テストに draft が無かった）

**実装中に踏んだこと**: `packages/core/tests/test_health.py` を作ったところ、既存の `packages/api/tests/test_health.py` と基底名が衝突し、**pytest の収集時に全体が止まった**（`import file mismatch`）。`tests/` に `__init__.py` が無いため基底名は 3 つのテストディレクトリ全体で一意でなければならない。`test_health_metrics.py` に改名し、CLAUDE.md に記録した。

**未解決**: 用語単位の「再承認の古さ」、指標の時系列（スナップショット）、しきい値と警告、名前空間をまたぐ集計。

テストは 477 → **531 件**。10 種類の変異（測れなかったとき 0 を返す・窓の境界を排他にする・ゼロ除算を防がない・切り捨てを黙って行う・一覧を既定で返す 等）と、上記の穴を埋めた後の 4 種類で検出力を確認した。

`P2B-05` の補足（**完了。2026-09-10**）: ADR-0006 決定4 の実装。設計は [ADR-0018](adr/0018-context-access-log.md)。

**ここには他の機能に無い制約があった。読み取りのホットパスに乗る。** 承認や公開は低頻度（人が判断する操作）だが、SPARQL クエリはエージェントが繰り返し叩く。`audit_events` と同じ「1 操作 1 行を永久に残す」設計をそのまま持ち込むと、テーブルが非有界に育つ。

**2 つのテーブルに分けた。分けた理由は保持期間である。**

| テーブル | 粒度 | 用途 | 性質 |
|---|---|---|---|
| `access_events` | クエリ 1 回 = 1 行 | 監査（ADR-0006 決定4） | **保持期間がある** |
| `term_access` | 用語 1 件 = 1 行（更新） | 健全性指標（ADR-0009 決定5） | **イベントより長生きする** |

**兼ねられない。** 指標は「90 日参照されていない用語」を知りたいので、保持期間を過ぎたイベントを消してもこの事実は残らなければならない。イベントだけを持つと、保持期間を 30 日にした瞬間に計算不能になる。集約だけを持つと監査が成立しない。

**設計上の要点**:

- **`access_events` には `DELETE` を与えた**（`audit_events` は ADR-0011 決定2 で剥奪している）。性質が違う — 人の決定の記録は緩やかに増えて消す理由が無いが、機械の参照の記録は無限に伸びる。**ただし「消せる」を「黙って消える」にしない**: 自動の削除ジョブは置かず、運用者が `before` と `reason` を明示して呼ぶ。そして**削除したこと自体を `audit_events` に記録する**ので、消えた事実は消せない場所に残る
- **記録の失敗はクエリを失敗させない。** 不変条件3（射影の失敗は正本への書き込みを失敗させない）と同じ向きの判断である。アクセスログは読み取りの副産物であって前提条件ではない。ただし**「記録できなかった」を黙って無かったことにはしない**（警告としてログに残す）
- **記録するのは SPARQL の経路だけ。** ADR-0006 決定4 の「コンテキスト」はエージェントが答えの材料として受け取る定義そのものである。`decisions` や `audit` の参照は記録しない（**`audit_events` を読む操作を `audit_events` に書くと再帰的になる**）。運用者がレビュー画面で眺めたことを参照に数えると「使われている用語」の意味がぼやける
- **イベントは `owner`、集約は `data-analyst`。** **`P2B-11` の「足し合わせれば見えるものを集約したときだけ隠すのは見せかけの制限」という論法はここには当てはまらない** — 監査照会は版単位の `decisions` が既に分析者に開いていたが、「誰がいつ何を問い合わせたか」は他のどの口からも導出できない。そしてこれは**同僚の行動の記録**である。集約は個人を特定しないので分析者に開く（指標に `owner` を要求すると使われなくなる）
- **「返した用語」はその名前空間が発行した IRI に限る。** 指標が答えたいのは「自分のオントロジーのどの用語が使われていないか」であり、`rdf:type` の参照回数は指標にならない。**責任者（ADR-0015 決定3）とは逆の判断で、理由も逆である** — 責任者は外部 IRI にも置けるが（マッピングの説明責任）、**使われていない外部 IRI を「縮める」ことはできない**。副作用として集約テーブルの行数に**その名前空間の用語数で上限が付く**
- **参照回数はクエリ 1 回で 1 回。** 同じ用語が結果に何行現れても 1 回とする（行数を数えると `LIMIT` の違いで指標が動く）
- **`GRAPH` 句の有無を記録する。** 明示したクエリは他の版を読みうるので、版の記録が不完全であることを `used_graph_clause` で伝える。**解析器を持ち込むより「不完全であることを記録する」ほうが正直である**
- **クエリは切り詰めて保存し、全文のハッシュを別に持つ。** 切り詰めた文字列が一致しても元のクエリが同じとは限らないので、同じクエリをまとめるにはハッシュが要る。**切り詰めたことは `query_truncated` で明示する**

**却下したもの**: Log Analytics に出す（健全性指標が SQL で結合できなくなる。正本は PostgreSQL という方針に反する）/ 非同期タスクに投げる（セッションがリクエストスコープ）/ 設定で無効化できるようにする（「いつのまにか無効」で指標が空のまま誰も気づかない状態を作る）/ イベントに返した用語の一覧を載せる（行が非有界に育つ。オントロジーは不変リビジョンなので再実行できる）。

**実装中に踏んだこと**: `server_default` は挿入時にしか効かない。`INSERT ... ON CONFLICT DO UPDATE` の更新側で `last_accessed_at` に触らないと、「最後の参照」が最初の参照のまま止まる。**しかも最初に書いたテストは `>=` を使っていたため、変異テストでこれを見逃した**（等号を許すと更新していない実装でも通る）。`>` に直して固定した。CLAUDE.md に記録した。

**未解決**: 保持期間の既定値と運用ガイド、`GRAPH` 句を使ったクエリの版の解決、`term_access` の再構築の口、名前空間をまたぐアクセスの照会。

テストは 416 → **459 件**。9 種類の変異（記録しない・`last_accessed_at` を更新しない・削除で集約も消す・削除を監査に残さない・要求ロールの取り違え・記録の失敗を伝播させる 等）で検出力を確認した。

`P2B-02` の補足（**完了。2026-09-10**）: ADR-0006 の補記が「Phase 2 の必須項目」としていたもの。設計は [ADR-0019](adr/0019-retention-policy.md)。

**実装を読んで、具体的な問題が 2 つ見つかった。**

1. **`SUPERSEDED_RETAIN` は名前が「N 版保持」なのに、実装は真偽値だった。** ローダは `0` 以外なら**全部載せていた**。`SUPERSEDED_RETAIN=2` と書いた運用者は「直近 2 版を残す」と読むので、**エラーにならず静かに期待と違う結果**になっていた
2. **「ストアに載せるか」の決定がローダと API で食い違いうる状態だった。** `approve` は前の版の名前付きグラフを外さないが、ローダは再構築時に（既定では）`superseded` を読み込まない。そのため**ストアの内容が「再構築したかどうか」で変わっていた**。`reconcile` はこの食い違いを避けるため `superseded` の在否を**どちらも不問**にしており、結果として**保持ポリシーを誰も強制していなかった**

**判断を正本側（Python）に集め、マニフェストで運ぶことで両方を直した。**

```json
{ "schema": 2, "current": "3.0.0", "retain_superseded": 1,
  "versions": [
    { "version": "3.0.0", "status": "approved",   "projection": "named default" },
    { "version": "2.0.0", "status": "superseded", "projection": "named" },
    { "version": "1.0.0", "status": "superseded", "projection": "skip:superseded-beyond-retain" } ] }
```

**設計上の要点**:

- **ローダは判断しない。** マニフェストの `projection` をそのまま解釈する。既存のコメント「**判断が二箇所に存在すると、片方だけ直して食い違う**」（`validate.sh`）を素直に適用した。`reconcile` が在否を不問にしたのは、その原則を守るために**判断を持たない側に降りた**結果だった。**判断を 1 箇所に集めれば、降りる必要が無くなる**
- **正本側に置く理由は「ポリシーの入力が正本にしかない」こと。** 「直近 N 版」の順序は `approved_at`（PostgreSQL）で決まり、マニフェストには載っていない
- **順序はバージョン文字列で決めない。** `validate_version` は英字と記号を広く許す（`1.0.0-rc1`、`2026-09`）ので、文字列順は「直近」を意味しない。**`approved_at` が NULL の版は最も古いものとして扱う** — NULL を「新しい」と解釈すると、**古い版が残って新しい版が落ちる**という最も分かりにくい壊れ方になる
- **負の `retain` は 0 として扱う。** 設定の誤りで「全部載る」に倒れない。**安全側は載せない側**である
- **ポリシーの外に出た版を外すのは `reconcile`。`approve` はやらない。** 不変条件3 により、正本への書き込みの成否を射影の操作に依存させない。回収する仕組みがあるなら最初からそこに任せる（[ADR-0013](adr/0013-reconcile-repairs-observed-divergence.md) が決めた形そのもの）
- **`reconcile` の報告に `retention_removed` を分けた。** 既存の `graphs_removed`（正本に無い残留）とは意味が違う — 前者は「正本にあるが載せない版」、後者は「正本に無い版」である
- **古いマニフェスト（schema 1）でも動く。** `projection` が無ければ従来の状態ベースの判断に落ち、**そのことを警告としてログに出す**（黙って落ちない）。`reconcile` を 1 回回せば全名前空間のマニフェストが更新される
- **`SUPERSEDED_RETAIN` は API 側の設定にした。** マニフェストを作るのは API なので、値を知る必要があるのは API だけである。Bicep から注入するのも API だけ（**値を 2 箇所に置くと食い違う**）。ローダ側の同名の環境変数は後方互換のためだけに残る

**既存のテストを 6 件更新した**（仕様変更の帰結）。うち 1 件（`test_reconcile_keeps_a_present_superseded_graph`）は**期待を逆にした** — ADR-0019 が変えた挙動そのものなので、「保持ポリシーの外なら削除する」テストと「保持範囲内なら残す」テストに分けた。後者は**以前の実装では区別が付かなかった**（0 以外なら全部載っていたため）。

**実装中に踏んだこと**: Bicep のパラメータを `int` で宣言したが、azd の `main.parameters.json` の置換は**文字列**を渡すので ARM が型エラーになる。既存のパラメータが全て `string` なのはこの理由だった（`fusekiCpu` が `'0.5'` なのも同じ）。CLAUDE.md に記録した。

**未解決**: `SUPERSEDED_RETAIN` の既定値をいくつにすべきか（ADR-0010 が未決のまま。この ADR は「N が正しく効く」ことだけを決めた）、「参照中のバージョン」の保持（アクセスログが `GRAPH` 句の版を解決できるようになってから）、名前空間ごとにポリシーを変えられるようにするか。

テストは 459 → **477 件**（保持ポリシーの純粋関数 17 件 + シェル側 6 件、既存の更新を含む）。8 種類の変異（retain を真偽値として扱う・バージョン文字列で並べる・NULL を最新として扱う・負の値をそのまま使う・昇順で残す 等）と、シェル側 2 種類で検出力を確認した。

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

`P2B-14` の補足（**完了。2026-09-11**）: 設計は [ADR-0022](adr/0022-competency-question-sets.md)。`P2B-07` が意図的に残した 3 つの未決を全部決めた。

| 未決だったこと | 決めたこと |
|---|---|
| **どこに置くか** | PostgreSQL の `competency_question_sets`。**名前空間ごとの不変改訂**（決定1・2） |
| **`approve` をブロックするか** | **する**（422）。ただし**評価は正本の TTL に対して rdflib でインメモリに行い、ストアには問い合わせない**（決定3） |
| **誰が書くか** | **`owner`**。読み取りは `data-analyst`（決定6） |

`POST /namespaces/{ns}/questions`（`owner`）/ `GET .../questions` / `GET .../questions/revisions` / `POST .../versions/{v}/questions/run`（すべて `data-analyst`）。

**設計上の要点**:

- **受け入れ基準を書き換えられるなら、通ったことは保証にならない。** これが中心である。版ごとに持つと新しい版が自分の合格条件を自分で書き換えられる（四眼原則と同じ論点。ADR-0014 決定4）。だから**名前空間ごとに 1 系列で、改訂は不変**にした
- **承認時にストアへ問い合わせない**（決定3）。**その時点でその版はまだ射影されていない**し、承認をストアの可用性に依存させるのは不変条件3 が守ろうとしているものの逆である。評価対象がその版に閉じるという利点もある。`_NullStore`（`query` を呼ぶと `AssertionError`）でテストが通ることが、この性質の証拠になっている
- **行数は数えない**（決定4）。判定に必要なのは存在の有無だけである。実測で 216,000 行の直積を全部 materialize すると 9.68 秒、先頭 1 行なら 0.000 秒だった。報告の文言も「1 行以上」にして、**数えたふりをしない**
- **「評価していない」を合格に丸めない**（決定5）。予算（30 秒）を超えた質問は `not_evaluated` に入り、`conforms` は偽になる。承認は 502 で止まる（基準を満たしていない 422 とは別物）
- **質問集合が無い名前空間は承認をブロックしない**（決定7）。「基準を定めていない」は「基準を満たしていない」ではない。ブロックすると**この機能を入れた瞬間に既存の全名前空間が承認不能になる**。マイグレーションで既定の質問集合を発明することもできない（何を要求すべきかはその名前空間の持ち主しか知らない）
- **定めていないことは健全性指標に出す。** `competency_question_count` を足した（7 項目目）。**`0` に意味がある唯一の項目である**
- **どの改訂で通ったかを監査に残す**（決定8）。基準は改訂されうるので、版だけでは「何を満たして承認されたのか」を復元できない
- **`owner` は基準を書き換えて自分で承認できる**（ロールは階層なので）。**防止ではなく可視化**に留めた — 改訂は不変、`reason` は必須、**減った質問の id が監査に出る**。四眼原則を掛けるかは `P2B-16` に分離した

**実測**（ADR-0022 に表で残した）:

| 測ったもの | 結果 |
|---|---|
| 同梱サンプル 11 件を rdflib で評価 | **Fuseki と同じ 11/11** / 0.63 秒 |
| 質問 200 件（上限）の評価 | 1.24 秒 |
| 3 重の直積を全行取得 / 先頭 1 行 | 9.68 秒 / **0.000 秒** |
| 0 行になる直積（全走査が必要） | **7.69 秒**（打ち切れない） |

**実装中に踏んだこと**:

1. **`expect: empty` に ASK を書いた質問が「1 行以上」と判定されていた。** 自分のテストが捕まえた。`expect` を見て分岐していたのが原因で、**応答の種類（`rdflib.query.Result.type`）で分岐する**必要があった。ストア経路が JSON の `boolean` / `results.bindings` のどちらが来たかで分岐しているのと同じ形に揃えた
2. **変異テストで穴を 1 つ見つけた。** 「予算超過でも承認を通す」変異が生き残った（決定5 の中心なのに検査していなかった）。ゲートを直接固定するテストを足した
3. **`git checkout -- <ディレクトリ>` で未コミットの実装を消した。** 変異テストの復元に使ったところ、変異と無関係な変更まで巻き戻った（新規ファイルは untracked なので残り、既存ファイルの変更だけが消えるので**被害が分かりにくい**）。1 ファイル単位で退避・復元する手順に変えた。CLAUDE.md に記録した

**却下したもの**: 質問集合を Blob に置く（版ごとになる／孤児 Blob の故障モードを作る）/ 承認をブロックせず報告に留める（ADR-0009 決定1 が「合意済みの規約はブロッキング」と決めている）/ 承認時に Fuseki の一時グラフへ射影して評価する（不変条件5 に新しい入口を作る）/ `GRAPH` 句を禁じる（ストア経路と仕様が分岐する）/ 別プロセスで評価してタイムアウトで殺す（`owner` しか書けない前提に対してコストが見合わない。`P2B-16` を進めるなら見直す）/ MCP でエージェントに見せる（要求が出てから）。

**未解決**: `owner` の自己承認（`P2B-16`）/ **同じ質問を 2 つのエンジン（rdflib と Fuseki）で評価するので判定が食い違う余地が残る** / 1 件の病的なクエリは打ち切れない / `expect: empty` は空振りでも通る（SHACL の「制約名のタイプミスを検出できない」と同じ family の既知の限界）。

テストは 531 → **575 件**。13 種類の変異で検出力を確認した（承認時に検査しない・`conforms` が未評価を無視する・書き込みを `maintainer` に緩める・改訂の並び順を昇順にする・壊れた質問集合を「基準なし」に丸める・監査に改訂を常に書く・減った質問を書かない・件数を常に 0 にする・保存前の検証をしない・応答の種類の検査を外す・全行を数える・予算超過でも通す・422 を 502 にする）。**うち 1 つは等価変異だった**（`result.type == "ASK"` を `expect` 判定に置き換えても、後続の `type != "SELECT"` が同じ結果を出す）。

`P2B-12` の補足（**完了。2026-09-11**）: 設計は [ADR-0024](adr/0024-namespace-delete-locking.md)。

**競合の形**（`DELETE` の Blob 判定と PG 削除の間に並行 publish が入る）:

| 時刻 | DELETE | PUBLISH |
|---|---|---|
| t1 | Blob を確認 → **空** | |
| t2 | | **Blob に `.ttl` を書く** |
| t3 | | PostgreSQL に版を書く |
| t4 | PostgreSQL の名前空間を削除 → commit | |

結果は「Blob に TTL があって PostgreSQL には何も無い」。**ローダは PostgreSQL を一切見ず Blob だけを見て再構築する**ので、次のレプリカ再作成で**削除したはずの名前空間が復活し、認証済みの呼び出し元に返る**。手順 t1 の Blob 検査（`P1-C1` で入れたもの）が防ごうとしていた失敗そのものが、並行実行下で成立していた。

**直し方**: `publish` と `DELETE` の両方が、**名前空間の行を `SELECT ... FOR UPDATE` でロックする**（ADR-0024 決定1）。どちらが先にロックを取っても食い違いが残らない。

| 先にロックを取った側 | 結果 |
|---|---|
| DELETE | PUBLISH はロック解放後に**行が無い**ので 404。**Blob には何も書かれない** |
| PUBLISH | DELETE はロック解放後に**その TTL を見る**ので 409 |

**設計上の要点**:

- **位置が本質。** 削除側は **Blob の検査より前**に、publish 側は **Blob への書き込みより前**にロックを取る。`DELETE` 文が暗黙に取る行ロックでは**遅すぎる**（t4 で待っても t2 の Blob 書き込みは終わっている）
- **「後から Blob を再検査する」では閉じない**（決定2）。publish が Blob を書く前に削除が commit してしまう順序が残り、その場合 publish の版行は外部キー違反で入らないが Blob は残る。**検査の回数を増やしても窓は閉じない**
- **ロックを取るのは `.ttl` を書く経路だけ**（決定3）。`approve` の `_state.json` の書き込みには取らない（`_state.json` は名前空間を復活させない。ローダは `.ttl` だけを読む）。残る孤児 `_state.json` は `P2B-20`
- **公開済みオントロジーを含む名前空間の削除は切り離した**（決定4、`P2B-19`）。不変条件7 との関係を決める必要があり、競合の修正とは別の作業である

**却下したもの**: 削除の commit 直前に Blob を再検査する（窓が閉じない）/ アドバイザリロック（名前空間の行という自然な排他対象があるのに人工のロック空間を持ち込む理由が無い）/ `SERIALIZABLE` に上げる（すべての経路が直列化失敗の再試行を扱うことになり影響範囲が釣り合わない）/ Blob に「削除中」マーカーを置く（正本に排他制御の状態を置く。マーカーが残ったときの回復がもう 1 つの問題になる）/ 削除を非同期ジョブにする（ジョブキューを足した上で同じ排他の問題を解くことになる）。

**変異テストで穴を 1 つ見つけて埋めた。** 「削除側でロックを取らない」変異が最初は**生き残った** — `DELETE` 文が暗黙に行ロックを取るので「publish が先」の順序では結局待たされ、テストが通ってしまう。**危険な順序（Blob 確認の直後に publish が割り込む）を作れていなかった。** `list_versions` を包んでその中から publish を起動するテストを足して固定した。

**テストの設計で踏んだこと（既存の脆弱性を露出させた）**: 行ロックを外す変異を当てたところ、**スイート全体が固まった**。テストが失敗してトランザクションが開いたまま残ると、次のテストの `drop_all` の `DROP TABLE` が**無期限に待つ**。conftest に `SET lock_timeout = '15s'` を置き、両方のセッションのフィクスチャで必ず `rollback` するようにした。**「固まる」を「落ちる」に変えた**ので、次に同じことが起きても原因が分かる。

**未解決**: `P2B-19`（退役の設計）/ `P2B-20`（孤児 `_state.json`）/ ロック待ちのタイムアウトを本番の経路には設定していない（PostgreSQL の既定では待ち続ける）/ **SQLite では `FOR UPDATE` が出ないのでこの排他は成立しない**（テストも本番も PostgreSQL なので実害は無い）。

テストは 618 → **625 件**。4 種類の変異で検出力を確認した（publish 側のロックを外す・削除側のロックを外す・`FOR UPDATE` を外す・削除側のロックを Blob 確認の後に動かす）。

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
