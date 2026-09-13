# Ontop VKG(仮想グラフ)

**実装済み**(`P3-01`、2026-09-13。設計は
[ADR-0046](../../docs/adr/0046-virtual-knowledge-graph.md))。
**Container Apps へ載せるのは `P3-07`**(要デプロイ窓)。

## 役割

Ontop は R2RML マッピングを介して、リレーショナルデータベースを RDF として
**実体化せずに** SPARQL で参照できるようにする(Virtual Knowledge Graph)。

これは 2 つの意味で本設計の要になる。

1. 顧客の実データをコピーせずに済む。データ移動を伴わないことは導入判断の障壁を大きく下げる
2. 巨大な実データを Fuseki に載せないため、「トリプルストアは再構築可能な射影」という
   設計([ADR-0002](../../docs/adr/0002-triple-store-as-rebuildable-projection.md))が成立する。
   Fuseki が抱えるのはオントロジーと語彙だけなので、起動時の再構築が現実的な時間で終わる

## ファイル

| ファイル | 何か |
|---|---|
| `Dockerfile` | 公式イメージ + JDBC ドライバ 2 つ。**ドライバは TLS を検証できる段で落とす** |
| `jdbc-licenses/` | 足したドライバのライセンス全文(イメージの `/opt/ontop/copyright/` へ入る) |
| `ontop-check.test.sh` | **`P3-01` の実機確認。** 使い捨ての PostgreSQL に対して 6 項目を確かめる |
| `testdata/` | その題材(スキーマ、TBox、R2RML マッピング 2 種、プロパティ) |

```bash
just test-vkg   # 実機確認(要: docker、uv、curl。約 2 分)
just up-vkg     # ローカルで起動する(`just up` では起動しない)
```

## 実測して分かったこと(5.3.0)

**設計の前に実物を測った。** 全体は ADR-0046 にあるが、ここを触るときに
まず知っておくべきものを挙げる。

- **公式イメージに JDBC ドライバは 1 つも無い**(`/opt/ontop/jdbc` すら無い)。
  それでいて起動スクリプトはクラスパスに `/opt/ontop/jdbc/*` を含める。
  つまり**ドライバは利用者が置く前提**である(`P1-S3` の結論のとおり)
- **CA 証書が 1 枚も無い**(`/etc/ssl/certs` が空)。イメージの中から HTTPS を
  取ると終了コード 5 になる。`curl` も入っていない(`wget` はある)
- **TBox のトリプルはデータとして返らない。** `?s a owl:Class` は 0 件。
  一方で **TBox の推論は効く**(上位クラスでインスタンスが返る)
- **SPARQL Update を受け付けない。** `/sparql` は 415、`/update` 系は 404
- **不正なマッピングのエラー本文は、接続ユーザから見える全関係を列挙する。**
  だから Core API はその本文を捨ててログに出す(ADR-0046 決定10)
- **即時初期化ではコンテナが起動しない**(不正なマッピングのとき)。
  イメージの既定を `ONTOP_LAZY_INIT=true` にしてある

## 設計上の約束

- **Container Apps の internal ingress にのみ公開する。** 外部から到達させない。
  Ontop の `/sparql` に認証は無い
- **連邦クエリを Fuseki の `SERVICE` 句で行わない。** Fuseki 側では `SERVICE` を
  無効化しており(SSRF 対策)、**宛先は URL で分ける**(ADR-0046 決定1)
- **`rr:sqlQuery` を受け付けない。** 任意の SQL が安全かを判定する手段が無い
  (ADR-0046 決定4)。**実データに対する本当の境界は DB ユーザの権限である** —
  最小権限のユーザを使うこと
- **同梱するドライバは pgjdbc(BSD-2-Clause)と mssql-jdbc(MIT)だけ。**
  MySQL Connector/J と Oracle JDBC は同梱しない。`/opt/ontop/jdbc` は
  マウント可能なまま残してあるので、利用者が実行時に置ける
  ([`docs/third-party-licenses.md`](../../docs/third-party-licenses.md))
