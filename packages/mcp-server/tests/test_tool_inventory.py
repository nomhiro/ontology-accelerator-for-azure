"""エージェントに出しているツールを数え上げる。

## なぜこのファイルが要るのか

**MCP はこの製品の境界の外側に向いた口である。** ここにツールを足すことは、
「名前空間の権限が効く API」から「エージェントの文脈に入る材料」へ何かを
移す判断である。**足すのは 1 行なので、判断を伴わずに増えうる。**

このファイルは**足した瞬間に落ちる**。落ちたら、そのツールを出してよい理由を
確かめてから集合に加える(`test_actor_type_api.py` の網羅の検査と同じ形)。

**出していないことを固定する価値があるものの例**:

- **ソース DB のカタログ**(`P2A-01`、[ADR-0041](../../../docs/adr/0041-source-schema-scan.md))。
  カタログは*観測*であって、レビューも承認も受けていない。エージェントに
  出すのは `approved` の版だけである(ADR-0010)。出すと**審査を通っていない
  語彙がエージェントの文脈に入る経路**ができる。表と列の名前それ自体が
  機微であることも理由である
- **名前空間全体の監査照会**(ADR-0026)。エージェントが必要とするのは
  「この定義の根拠」であって「名前空間の全履歴」ではない
"""

from __future__ import annotations

from ontology_mcp.server import mcp

#: エージェントに出しているツール。**増やすときは理由を確かめること。**
EXPECTED_TOOLS = {
    "list_namespaces",
    "sparql_query",
    "version_decisions",
    "term_owner",
    "term_mappings",
}


async def test_出しているツールをすべて数え上げている() -> None:
    """**ツールを足したらここが落ちる。**

    落ちたら、そのツールが

    1. **承認を経た材料しか返さないか**(不変条件7・ADR-0010)
    2. 名前空間の権限を Core API 側で通しているか
    3. エージェントが必要とする粒度か(全履歴ではなく「この定義の根拠」)

    を確かめてから集合に加える。
    """
    names = {tool.name for tool in await mcp.list_tools()}
    assert names == EXPECTED_TOOLS, (
        f"ツールの一覧が変わっている。増えた: {sorted(names - EXPECTED_TOOLS)} / "
        f"減った: {sorted(EXPECTED_TOOLS - names)}"
    )


async def test_ソース_DB_のカタログを出していない() -> None:
    """**意図した判断である**(ADR-0041)。

    「まだ出していないだけ」に見えないよう、明示的に固定する。カタログの
    読み手は `P2A-02` であり、**人間が承認した結果**がエージェントに届く。
    """
    names = {tool.name for tool in await mcp.list_tools()}
    for banned in ("catalog", "scan", "schema", "source"):
        assert not any(banned in name for name in names), (
            f"ソース DB のカタログを出している可能性がある('{banned}' を含む): {sorted(names)}"
        )


async def test_書き込みのツールが無い() -> None:
    """**MCP は読み取り専用である**(設計原則4)。

    Fuseki への書き込み口は Core API だけである。**変更は必ず「正本に書く →
    ストアへ射影」の順**で、エージェントがその順序を飛ばせてはならない。
    """
    names = {tool.name for tool in await mcp.list_tools()}
    for banned in ("create", "update", "delete", "publish", "approve", "revoke", "purge"):
        assert not any(banned in name for name in names), (
            f"書き込みに見えるツールがある('{banned}' を含む): {sorted(names)}"
        )
