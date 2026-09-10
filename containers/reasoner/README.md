# OWL 推論コンテナ (ELK)

オントロジーの**論理的整合性**と**充足不能クラス**を検査します。CI で使います
([ADR-0021](../../docs/adr/0021-owl-reasoning-in-ci.md))。

```bash
just check-reasoning                       # samples/ をすべて検査する
just check-reasoning samples/retail-core.ttl
just test-reasoner                         # この検査自身の振る舞いをテストする
```

## 読むときに必ず押さえること

**この検査は「矛盾がない」とは言いません。「矛盾は検出されなかった」と言います。**

ELK は OWL 2 EL の推論器で、**扱えない公理を無視します**。無視すると制約が減るので
導出も減ります。つまり:

- **検出したものは本物**です(誤検出しない = 健全)
- **見逃しがあります**(完全ではない)

そして **ELK 0.6.0 はデータプロパティを扱えません**。`DataProperty` /
`DataPropertyDomain` / `DataPropertyRange` / `FunctionalDataProperty` があると
整合性の判定そのものを諦めます。**属性を 1 つも持たない業務用オントロジーは無い**ので、
**結論が不完全になるのは例外ではなく通常の状態**です。同梱サンプル
`samples/retail-core.ttl` も該当します。

だから出力は必ず 2 つ返します。

```
矛盾は検出されませんでした (見逃しがありえます)
```

**`consistency_conclusive` を読まずに `inconsistency_found` だけを見てはいけません。**

## 何が CI を止めるか

| 事象 | 止めるか |
|---|---|
| 読み込めなかった | **止める** |
| 矛盾が検出された | **止める** |
| 充足不能クラスが検出された | **止める** |
| 結論が不完全だった | 止めない(報告する) |
| OWL 2 EL プロファイルの逸脱 | 止めない(報告する) |

不完全さで止めない理由は上記の通りです。**止める設計にすると実用的な
オントロジーが全部落ちます。** 詳細と却下した代替案は ADR-0021 にあります。

## 出力

標準出力に JSON、ログは標準エラーに出ます。`scripts/check-reasoning.sh` は
人が読める要約を出しつつ、生の JSON を `.reasoner-report.json` に残します。

```json
{ "blocking": false, "reasoner": "ELK 0.6.0", "profile": "OWL 2 EL",
  "results": [ {
    "file": "samples/retail-core.ttl", "loaded": true, "error": null,
    "axiom_count": 56,
    "inconsistency_found": false, "consistency_conclusive": false,
    "unsatisfiable_classes": [], "unsatisfiable_class_count": 0,
    "unsatisfiable_classes_conclusive": false,
    "incompleteness_reasons": ["Potential incompleteness due to occurrences of DataProperty", "..."],
    "expressivity_violations": [], "expressivity_violation_count": 0,
    "declaration_violations": ["..."], "declaration_violation_count": 25,
    "blocking": false } ] }
```

`unsatisfiable_classes` が **`null` のときは「測っていない」** です(`[]` は
「測ったが 0 件だった」)。矛盾が検出されたときは全クラスが充足不能になるので
列挙しません。[ADR-0020](../../docs/adr/0020-health-metrics.md) の健全性指標と同じ扱いです。

プロファイル逸脱を 2 種類に分けているのは、**宣言漏れ**(SKOS や SHACL の語彙を
宣言せずに使うと大量に出る。同梱サンプルで 25 件)に**表現力の逸脱**が埋もれるのを
防ぐためです。

## 検証との棲み分け

| 対象 | 手段 | 言語 | 実行タイミング |
| --- | --- | --- | --- |
| データ形状の検証 (SHACL) | pyshacl | Python | **`approve` の同期パス**(`P2A-05`) |
| 廃止ライフサイクル | rdflib | Python | **`approve` の同期パス**([ADR-0017](../../docs/adr/0017-deprecation-lifecycle.md)) |
| OWL の整合性検査・充足不能クラス | ELK + OWL API | Java | **CI のみ**([ADR-0005](../../docs/adr/0005-reasoner-boundary.md) 決定3) |

**推論は Core API の同期パスに入れません。** 実行時間が予測しづらいためです
(ADR-0005 決定3)。CI が検査するのは**このリポジトリが同梱するオントロジー**です。

## ライセンス

- **ELK は Apache-2.0**(`io.github.liveontologies:elk-owlapi:0.6.0`)
- **OWL API は Apache-2.0 / LGPL-3.0 のデュアル**。**Apache-2.0 の方を選択**します
  (NOTICE に明記)。版は **ELK が対応している 5.1.20 に揃えます** —
  `owlapi-distribution:5.5.1` を足すと同じクラスが二重に載って Turtle が
  読めなくなりました(ADR-0021 決定4)
- **HermiT (LGPL-3.0) と JFact (LGPL-3.0) は同梱しません**(ADR-0005 決定4)。
  `reasoner-check.test.sh` が jar の中身を見て機械的に検査します
- **ROBOT は使いません。** 配布 jar が HermiT と JFact を同梱しているためです
  (82 MB の jar を落として実際に確認しました)

shade jar に入る 67 個の依存の棚卸しは
[`docs/third-party-licenses.md`](../../docs/third-party-licenses.md) にあります。
