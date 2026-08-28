# ontology-core

Core API と MCP サーバーが共有するライブラリ。

| モジュール | 役割 |
| --- | --- |
| `config.py` | 環境変数から設定を読み込む。デプロイ時の環境変数名の**正本** |
| `models.py` | ドメインモデル(名前空間・ロール・オントロジーのバージョン・監査イベント) |
| `sparql/client.py` | SPARQL 1.1 Protocol を境界とするストア抽象 (`SparqlStore`) と Fuseki 実装 |
| `sparql/guards.py` | エージェントに公開するクエリのガード(読み取り専用・`SERVICE` 拒否) |
| `auth/entra.py` | Microsoft Entra ID が発行した JWT の検証 |

設計上の約束は 2 つある。

1. アプリコードは `SparqlStore` 越しにしかトリプルストアを触らない。ストアの差し替え可能性を保つため
2. `SparqlStore` への書き込みは Core API だけが行う。ストアは正本ではなく再構築可能な射影である
   (`docs/adr/0002-triple-store-as-rebuildable-projection.md`)
