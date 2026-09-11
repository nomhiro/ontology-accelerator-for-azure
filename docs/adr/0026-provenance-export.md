# ADR-0026: 監査証跡を PROV-O で書き出す — 記録していない派生関係は主張しない

- ステータス: 承認済み
- 日付: 2026-09-12

## コンテキスト

[ADR-0006](0006-ontology-versioning-and-audit.md) 決定3 は「監査証跡を
PostgreSQL に記録し、**PROV-O で表現する**」と決めた。同 ADR は
「`prov:Entity`(リビジョン)、`prov:Activity`(提案・承認)、`prov:Agent`
(人間・LLM)、**`prov:wasDerivedFrom`(リビジョン間の派生)**といった標準語彙に
対応させる」と具体名まで挙げ、根拠の節ではこう書いている。

> 監査証跡のスキーマを自分で発明する必要はない。(中略)**相互運用性がある。**
> PROV-O を理解する外部ツールがそのまま使える

**記録は実装されているが、PROV-O での表現は未実装だった**(`P2A-07`)。
`audit_events` に「誰が・いつ・何を・なぜ」が入り、`GET /namespaces/{ns}/audit`
で JSON として読めるが、**独自のスキーマのままである**。W3C 標準忠実を掲げる
プロジェクトが自身のメタデータだけ独自スキーマで出しているのは、ADR-0006 が
却下した案(独自スキーマ)と実質同じ状態である。

### 記録していないもの

PROV-O へ写そうとして、**記録していない事実が 1 つあることが分かった。**

`audit_events` には「どの版から編集したか」が無い。`publish` は `base_version`
を受け取るが、**lost update の検出にだけ使って保存していない**(`P1-13`)。
[ADR-0016](0016-semantic-diff.md) 決定2 は差分の基準を「この承認によって
`superseded` になる版」と決めているが、それは**実務上の基準**であって
**著者が実際に何から編集したか**ではない。

つまり「2.0.0 は 1.0.0 の次に承認された」は記録されているが、
**「2.0.0 は 1.0.0 から派生した」は記録されていない。**

## 決定

### 1. `GET /namespaces/{ns}/provenance` が PROV-O の Turtle を返す

`audit` と同じ絞り込み(`action` / `actor` / `subject` / `since` / `until` /
`limit`)を受け、`text/turtle` を返す。権限も同じ `data-analyst` である。

**同じ情報を別の語彙で出すだけなので、権限を変えない。** ここだけ厳しくすると
「足し合わせれば見えるものを、集約したときだけ隠す」形になる(`audit` の
docstring が既に同じ判断を書いている)。

**`cursor` は受けない。** RDF は順序を持たないので、カーソルで切り出した
断片を RDF として渡す意味が薄い。代わりに決定5 の形で「切り詰めた」ことを
RDF の中に書く。

### 2. `prov:wasDerivedFrom` と `prov:wasRevisionOf` は**出さない**

**ADR-0006 が名前を挙げていた語彙を、意図的に出さない。**

`prov:wasDerivedFrom` は「ある実体の内容が別の実体から得られた」ことを主張する。
`prov:wasRevisionOf` はその下位で、より強く「改訂である」と主張する。
**どちらも外部の PROV ツールは著作の系譜として読む。**

このシステムが知っているのは**承認の順序**だけである。承認の順序は派生では
ない — 2.0.0 が 1.0.0 より後に承認されたことと、2.0.0 が 1.0.0 を元に書かれた
ことは別の事実である。1.0.0 が承認される前に書かれた 2.0.0 もありうる。

**承認の順序から派生を出力するのは、測っていないことを標準語彙で主張する
ことである。** このリポジトリが繰り返し避けてきた形そのもので、しかも
**相互運用性があるぶん害が大きい** — 外部ツールが「派生の系譜」としてそれを
表示し、誰も疑わない。

代わりに**起きた行為をそのまま出す**。「承認が前の版を `superseded` にした」は
記録された事実なので、その活動として出る。

**派生を出したいなら `base_version` を保存する。** `P2A-15` として記録する。

### 3. 主体は `prov:Agent` のままにする。`prov:Person` / `prov:SoftwareAgent` に分けない

ADR-0006 は「`prov:Agent`(人間・LLM)」と書いていたが、**`audit_events.actor`
は Entra のオブジェクト ID だけで、人間かサービスプリンシパルかを区別して
いない。**

**区別できないものを区別して出さない。** `prov:SoftwareAgent` と書けば
外部ツールは「これは自動生成だ」と読む。人間の承認を自動化された行為として
見せるのは、四眼原則を記録する監査証跡としては最悪の誤りである。

トークンの `idtyp` クレームや Entra のディレクトリ照会で判別する道はあるが、
**監査証跡の書き出しのために Entra へ問い合わせるのは、書き出しを Entra の
可用性に依存させる**(不変条件3 と同じ向きの判断)。`P2A-16` として記録する。

### 4. 行為の種類は独自の下位クラスで保つ。`prov:Activity` に潰さない

`published` / `submitted` / `approved` / `rejected` / `superseded` /
`questions-revised` / `mapping-declared` / `mapping-revoked` /
`access-log-purged` を、`ont:Publish` のような下位クラスにする
(`rdfs:subClassOf prov:Activity`)。

**`prov:Activity` だけにすると「何をしたか」が消える。** PROV-O は
「誰が・いつ・何に」を標準化するが、**行為の種類はドメインの語彙**である。
標準に写すために情報を落とすのは本末転倒である。

| 監査の `action` | 出力する型 | 版との関係 |
|---|---|---|
| `published` | `ont:Publish` | **`prov:generated`**(公開が版を生む) |
| `submitted` / `approved` / `rejected` / `superseded` | `ont:Submit` / `ont:Approve` / `ont:Reject` / `ont:Supersede` | `prov:used` |
| `questions-revised` / `mapping-declared` / `mapping-revoked` / `access-log-purged` | `ont:ReviseQuestions` / `ont:DeclareMapping` / `ont:RevokeMapping` / `ont:PurgeAccessLog` | 版ではないので `prov:Entity` を作らない |

**`published` だけが `prov:generated` である。** 版を生む行為はそれだけで、
他は既にある版に対する行為である。ここを全部 `generated` にすると
「1 つの実体が 5 回生成された」という読めない記録になる。

**`ont:action` に生の文字列も必ず書く。** 対応表が新しい `action` に追いつか
なくても、記録された値そのものは失われない。**対応表に無い `action` は
`prov:Activity` のままにする** — 知らない行為を既知のどれかに丸めると、
未知の行為が既知の行為として集計される。

**対象は `ont:subject` に生の文字列としても必ず書く。** 版の形
(`<名前空間>@<バージョン>`)でなければ `prov:Entity` は作らないが、記録された
文字列は残す。ここを落とすと「情報が無い」と「形が違った」が区別できない。
差分の要約(`ADR-0016` 決定7 の JSON)も `ont:diffSummary` にそのまま載せる
— **RDF に展開しない**。全トリプルを展開すると書き出しが非有界に育つ。

### 5. 切り詰めたことを RDF の中に書く

書き出しは `prov:Bundle` のノードを 1 つ持ち、そこに件数と「切り詰めたか」を
書く。

```turtle
<urn:ontology:provenance/retail-core> a prov:Bundle ;
    ont:eventCount 50 ;
    ont:truncated true ;
    ont:exportedAt "2026-09-12T…"^^xsd:dateTime .
```

[ADR-0016](0016-semantic-diff.md) 決定5 / [ADR-0020](0020-health-metrics.md)
決定3 / [ADR-0021](0021-owl-reasoning-in-ci.md) 決定1 /
[ADR-0022](0022-competency-question-sets.md) 決定5 /
[ADR-0025](0025-result-limit-enforcement.md) 決定3 と**同じ原則の 6 例目**。

**RDF は「無い」と「返していない」を区別できない。** 切り詰めたことを書かないと、
受け取った側は「この名前空間ではこれだけしか起きていない」と読む。
**`ont:truncated` は偽でも明示的に書く** — 省略すると「全部だ」とも
「言っていない」とも読める。

**`ont:includes` で各行為を束に結び付ける。** 旗だけでは使えない — 複数の
書き出しを混ぜた後に「どの行為が切り詰められた束から来たか」を辿れなければ、
`ont:truncated` は読み手にとって意味を持たない。

なお、この束自身の記述を同じ文書に含めている。**厳密な PROV の束の意味論では
束の記述は別の束に属する**が、自己記述にしないと切り詰めが伝わらないので
こちらを採る(`prov:Bundle` は `prov:Entity` の下位クラスなので、束を解さない
ツールも実体として読める)。

### 6. トリプルストアには射影しない

[ADR-0023](0023-cross-domain-mappings.md) 決定7 と同じ理由である。
出自の記録は**どの版にも属さない**ので版の名前付きグラフに混ぜられず、
既定グラフを単一の承認済み版に保つ決定([ADR-0010](0010-approval-and-projection.md)
決定6)とも衝突する。API から書き出す。

### 7. IRI の空間を分ける

| もの | IRI |
|---|---|
| 版(`prov:Entity`) | `urn:ontology:revision/<ns>/<version>` |
| 行為(`prov:Activity`) | `urn:ontology:activity/<監査イベントの id>` |
| 主体(`prov:Agent`) | `urn:ontology:agent/<オブジェクト ID>` |
| 書き出し(`prov:Bundle`) | `urn:ontology:provenance/<ns>` |
| 独自語彙 | `urn:ontology:prov#` |

**版のグラフ IRI(`urn:ontology:graph/<ns>/<version>`)を流用しない。**
あれは「その版のトリプルが載るグラフ」の識別子で、**版そのものではない**。
同じ IRI にすると、グラフと版が同一視され、「グラフに対する操作」と
「版に対する操作」が混ざる。

行為の IRI に監査イベントの `id` を使うので、`AuditEvent` に `id` を足す。
**`id` は既に外へ出ている** — `AuditPage.next_cursor` がその値である。

## 検討した代替案

### `Accept: text/turtle` で `GET /namespaces/{ns}/audit` に内容交渉させる

**却下。** REST としては素直で、URL が増えない。

却下理由: **ページング(`cursor`)の意味が表現によって変わる。** JSON では
カーソルが要るが、RDF では決定1 のとおり意味が薄い。**同じ URL が表現によって
違うパラメータを取る**のは、OpenAPI から型を生成する側にとって扱いにくい
(`P2A-11` でパスパラメータ名を揃えたのと同じ、契約の一貫性の話である)。

### `prov:wasRevisionOf` を「版の連番」として出す

**却下(決定2)。** 「同じ名前空間の連続する承認済み版なら改訂と呼んでよい」
という解釈は成り立つ余地があるが、**外部ツールは派生として読む**。
説明を添えても、RDF を機械が読む場面に説明は届かない。

### JSON-LD で返す

**却下(今は)。** `@context` を付ければ JSON のままで RDF になるので、
既存の JSON クライアントと両立できるという強い利点がある。

却下理由: **`@context` をどこで公開するかという新しい問題が生まれる**
(IRI は `urn:` で dereferenceable ではない)。Turtle なら接頭辞定義が
本文に入る。要求が出てから足す(`P2A-17`)。

### 監査イベントを直接 PROV-O で PostgreSQL に保存する

**却下。** ADR-0006 が既に答えている — 「PROV-O は表現の語彙であり、保存媒体を
指定しない」。トランザクション・外部キー・時系列検索のために保存は
PostgreSQL のままにする。

## 結果(トレードオフ・影響)

### 受け入れるコスト

- **ADR-0006 が名前を挙げた `prov:wasDerivedFrom` を出さない**(決定2)。
  約束の一部を**意図的に果たさない**。果たすには `base_version` の保存が要る
  (`P2A-15`)
- **主体が人間か機械か分からない**(決定3、`P2A-16`)
- **カーソルページングが無い**(決定1)。大きな名前空間では `since` / `until` で
  期間を区切って取る
- **独自の下位クラスを含む**(決定4)。純粋な PROV-O だけを理解するツールは
  行為の種類を読み飛ばす(`prov:Activity` としては読める)
- **IRI が `urn:` なので dereferenceable ではない。** 外部ツールから
  「この版の詳細を引く」ことはできない。HTTP IRI にするには公開 URL の設計が
  要る

### 得られるもの

- **W3C 標準忠実の看板が自身のメタデータにも適用された。** ADR-0006 が却下した
  「独自スキーマ」の状態を終わらせた
- **測っていない派生関係を主張しない。** 相互運用性がある形式で嘘をつくのは、
  独自形式で嘘をつくより害が大きい
- **切り詰めを RDF の中で伝える**(決定5)

### 影響を受ける他の決定

- [ADR-0035](0035-actor-type.md) — 決定3 に補記を追加した(条件付きで
  `prov:Person` / `prov:SoftwareAgent` を出す)
- [ADR-0006](0006-ontology-versioning-and-audit.md) 決定3 — 補記を追加した
  (`prov:wasDerivedFrom` と `prov:Person` / `prov:SoftwareAgent` を出さない理由)
- [ADR-0016](0016-semantic-diff.md) 決定2 — 差分の基準が「著者が編集した版」では
  ないことが、ここで問題として表面化した
- [ADR-0023](0023-cross-domain-mappings.md) 決定7 — 射影しない判断を同じ理由で
  再利用した

---

## 補記: 系譜を記録したので `prov:wasDerivedFrom` を出すようになった (2026-09-12、`P2A-15`)

決定2 は `prov:wasDerivedFrom` を出さないと決めたが、その理由は
**「記録していないから」**だった。[ADR-0027](0027-revision-lineage.md) で
`publish` の `base_version` を `ontology_versions.edited_from` に保存したので、
**記録されている版にだけ辺を出す**ようになった。

決定2 の判断は変わっていない。**承認の順序から派生を出さない**。
`base_version` を渡さずに publish された版には辺が出ず、代わりに
`ont:editedFromRecorded false` が出る。

`prov:wasRevisionOf` は依然として使わない。`wasDerivedFrom` の下位で
「改訂である」とより強く主張するが、記録しているのは「この版を編集するとき
基準にした版」であって、両者が改訂の関係にあるとまでは言えない。

**3 段の「分からなさ」を区別する**(ADR-0027 決定5)。

| 状況 | 出力 |
|---|---|
| 記録あり・親あり | `ont:editedFromRecorded true` + `prov:wasDerivedFrom` |
| 記録あり・親なし | `ont:editedFromRecorded true` のみ(この名前空間の根) |
| 記録なし | `ont:editedFromRecorded false` |
| **版の行が引けなかった** | **何も出さない** |

最後の行が本質である。`false` は「行を見て、記録されていなかった」であり、
**行を見られなかった**ことと混ぜない(`audit_events` には名前空間への外部キーが
無いので、名前空間を削除して同名で作り直すと版の行が無い監査イベントが残りうる)。

JSON-LD(`P2A-17`)は未着手のままである。主体の種別(決定3)は下の補記で
解決した。

## 補記: 種別を記録したので `prov:Person` / `prov:SoftwareAgent` を出すようになった (2026-09-12、`P2A-16`)

決定3 は主体を `prov:Agent` のままにすると決めたが、その理由は決定2 と同じく
**「記録していないから」**だった。[ADR-0035](0035-actor-type.md) で
`audit_events.actor_type` を足したので、**条件付きで下位クラスを出す**ように
なった。

決定3 の判断は変わっていない。**区別できないものを区別して出さない。**
変わったのは「区別できる場合がある」ことである。

**種別は行為ごとに出し、主体のクラスは一致するときだけ出す**(ADR-0035
決定4)。主体の IRI は主体ごとなので、種別を主体に付けると 1 つの IRI に
複数の値がぶら下がる(`idtyp` を任意クレームとして設定する前と後の行為が
同じ書き出しに混ざると実際に起きる)。

| その主体の行為の記録 | 出力 |
|---|---|
| すべて `user` | `prov:Agent`, **`prov:Person`** |
| すべて `service-principal` | `prov:Agent`, **`prov:SoftwareAgent`** |
| `unknown` を含む / 食い違う / `NULL` を含む | `prov:Agent` **のみ** |

行為には `ont:actorType` を出す。**`NULL`(この機能より前に書かれた行)には
出さない** — `"unknown"` は「問うて、分からなかった」であり、
「問うていない」と混ぜない(ADR-0035 決定5。上の系譜の表と同じ規則)。

決定3 が挙げた「Entra へ問い合わせる」道は**依然として却下**である。記録の
時点で保存したので、書き出しは Entra の可用性に依存しない。
