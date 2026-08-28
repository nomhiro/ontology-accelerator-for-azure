# ontology-mcp

AI エージェントにオントロジーのコンテキストを提供する MCP サーバー。Streamable HTTP で
公開し、Microsoft Foundry Agent Service や開発ツール(VS Code など)から接続できる。

## 公開するツール

| ツール | Phase | 説明 |
| --- | --- | --- |
| `list_namespaces` | 0(スタブ) | 参照できる名前空間を列挙する |
| `sparql_query` | 0(スタブ) | 読み取り専用の SPARQL クエリを実行する |
| `search_context` | 3 | ベクトル / ハイブリッド検索でコンテキストを引く |

## 設計上の約束

このサーバーは**読み取り専用**である(`MCP_READ_ONLY=true`)。更新操作と `SERVICE` 句は
`ontology_core.sparql.guards` で弾き、権威ある制御としてストア側でも `SERVICE` の実行を
無効化している。エージェントからの入力は信頼できないという前提で扱う。
