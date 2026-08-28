# Ontop VKG (Phase 3)

このディレクトリは **Phase 3 で実装する**。現時点では意図の記録のみ。

## 役割

Ontop は R2RML マッピングを介して、リレーショナルデータベースを RDF として
**実体化せずに** SPARQL で参照できるようにする(Virtual Knowledge Graph)。

これは 2 つの意味で本設計の要になる。

1. 顧客の実データをコピーせずに済む。データ移動を伴わないことは導入判断の障壁を大きく下げる
2. 巨大な実データを Fuseki に載せないため、「トリプルストアは再構築可能な射影」という
   設計(`docs/adr/0002-triple-store-as-rebuildable-projection.md`)が成立する。
   Fuseki が抱えるのはオントロジーと語彙だけなので、起動時の再構築が現実的な時間で終わる

## 実装時の注意

- Ontop のコアは Apache-2.0 だが、**配布イメージに同梱される JDBC ドライバは別ライセンス**の
  可能性がある。公式イメージをそのまま使わず、必要なドライバだけを自前の Dockerfile で
  追加する方針とする(`docs/third-party-licenses.md`)
- Container Apps の internal ingress にのみ公開する。外部から到達させない
- 連邦クエリを Fuseki の `SERVICE` 句で行わないこと。Fuseki 側では `SERVICE` を
  無効化しており(SSRF 対策)、連邦は Ontop 経由に限定する
