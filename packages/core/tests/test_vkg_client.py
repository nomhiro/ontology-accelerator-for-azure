"""仮想グラフへの照会クライアント(ADR-0046、`P3-01`)。

**このファイルでいちばん重要なのは `test_相手のエラー本文を応答に載せない`
である。** 実測で、Ontop は参照する関係が無いとき
`Cannot find relation ... (available choices: [...])` を返し、**接続ユーザから
見える全関係を列挙する**(こちらの制御平面の表も含まれていた)。
本文をそのまま通すと、それが照会者に届く。

実物の Ontop に対する検証は `containers/ontop/ontop-check.test.sh` が行う。
ここは**こちら側の HTTP の扱い**を固定する。

ここで固定するのは 4 つである。

1. **相手のエラー本文を応答に載せない**(決定10)
2. **「設定されていない」を「データが無い」と混ぜない**(決定11)。
   未設定は専用の例外であり、空の結果ではない
3. **`update` の口を持たない**(決定3)。実物が 415 で拒否することは
   確かめたうえで、こちら側にも経路を置かない
4. **JSON でない応答を SPARQL Results として通さない**
"""

from __future__ import annotations

import httpx
import pytest

from ontology_core.vkg import (
    VirtualGraphClient,
    VirtualGraphError,
    VirtualGraphNotConfiguredError,
    resolve_endpoint,
)

_ENDPOINT = "http://ontop.internal:8080/sparql"

#: 実測した Ontop のエラー本文(短縮版)。**関係の一覧が入っている。**
_LEAKING_BODY = (
    "it.unibz.inf.ontop.exception.InvalidMappingSourceQueriesException: "
    "Error: Cannot find relation "
    '"vkg"."nope" (available choices: ["public"."namespaces", "public"."scan_sources", '
    '"public"."audit_events", "vkg"."customer"])'
)


def _client(handler: object) -> VirtualGraphClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return VirtualGraphClient(client=httpx.AsyncClient(transport=transport))


# ------------------------------------- エンドポイントの解決(決定11)


def test_未設定なら専用の例外になる() -> None:
    """**空の結果を返さない。** 空だと「該当する行が無い」と区別できない。"""
    with pytest.raises(VirtualGraphNotConfiguredError, match="設定されていません"):
        resolve_endpoint(None, namespace="ns", source="src")


def test_空文字列も未設定として扱う() -> None:
    with pytest.raises(VirtualGraphNotConfiguredError):
        resolve_endpoint("   ", namespace="ns", source="src")


def test_未設定の例外は_VirtualGraphError_の一種である() -> None:
    """呼び出し側が 1 つの `except` で受けられるようにしておく。

    **ただし型で区別できる** — ルータは 503 と 502 を分ける。
    """
    assert issubclass(VirtualGraphNotConfiguredError, VirtualGraphError)


def test_名前空間とソースを差し替える() -> None:
    resolved = resolve_endpoint(
        "http://ontop-{namespace}-{source}:8080/sparql", namespace="retail", source="crm"
    )
    assert resolved == "http://ontop-retail-crm:8080/sparql"


def test_プレースホルダが無いテンプレートは単一インスタンス構成として扱う() -> None:
    """ローカル開発がこれである(`FusekiStore._resolve` と同じ形)。"""
    assert resolve_endpoint(_ENDPOINT, namespace="retail", source="crm") == _ENDPOINT


# ------------------------------------- 読み取り


async def test_SELECT_の結果を返す() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers["Content-Type"]
        captured["accept"] = request.headers["Accept"]
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"head": {"vars": ["s"]}, "results": {"bindings": []}})

    async with _client(handler) as client:
        result = await client.query("SELECT ?s WHERE { ?s ?p ?o }", endpoint=_ENDPOINT)

    assert result["head"]["vars"] == ["s"]
    assert captured["accept"] == "application/sparql-results+json"
    assert captured["content_type"].startswith("application/sparql-query")
    assert captured["body"] == "SELECT ?s WHERE { ?s ?p ?o }"


async def test_CONSTRUCT_は_Turtle_を返す() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"] == "text/turtle"
        return httpx.Response(200, text="<urn:a> <urn:b> <urn:c> .")

    async with _client(handler) as client:
        turtle = await client.construct(
            "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", endpoint=_ENDPOINT
        )

    assert turtle == "<urn:a> <urn:b> <urn:c> ."


# ------------------------------------- 失敗の扱い(決定10)


async def test_相手のエラー本文を応答に載せない() -> None:
    """**これがこのファイルのいちばん重要なテストである**(決定10)。

    `FusekiStore._raise_for_status` は本文を 500 文字まで含める。**あちらは
    自分の射影先**なので漏れるのは自分が書いた内容である。ここは相手が違う
    — 顧客 DB のスキーマが漏れる。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=_LEAKING_BODY)

    async with _client(handler) as client:
        with pytest.raises(VirtualGraphError) as caught:
            await client.query("SELECT * WHERE { ?s ?p ?o }", endpoint=_ENDPOINT)

    message = str(caught.value)
    assert "500" in message
    # **関係名が 1 つも漏れていないこと。**
    assert "available choices" not in message
    assert "scan_sources" not in message
    assert "namespaces" not in message
    assert "customer" not in message


async def test_到達できないときも本文を作らない() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    async with _client(handler) as client:
        with pytest.raises(VirtualGraphError, match="到達できませんでした"):
            await client.query("SELECT * WHERE { ?s ?p ?o }", endpoint=_ENDPOINT)


async def test_JSON_でない_200_を_SPARQL_Results_として通さない() -> None:
    """**「読めなかった」を「0 件だった」にしない。**"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>portal</html>")

    async with _client(handler) as client:
        with pytest.raises(VirtualGraphError, match="解釈できませんでした"):
            await client.query("SELECT * WHERE { ?s ?p ?o }", endpoint=_ENDPOINT)


async def test_JSON_の配列を_SPARQL_Results_として通さない() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    async with _client(handler) as client:
        with pytest.raises(VirtualGraphError, match="SPARQL Results JSON ではありません"):
            await client.query("SELECT * WHERE { ?s ?p ?o }", endpoint=_ENDPOINT)


# ------------------------------------- 書き込みの口が無い(決定3)


def test_update_の口を持たない() -> None:
    """**実物が 415 で拒否することを確かめたうえで、こちらにも置かない。**

    `SparqlStore` の抽象に押し込めると `update` / `put_graph` /
    `create_dataset` を「例外を投げる実装」で埋めることになり、
    **契約に嘘を書く**。だから別の型にしてある。
    """
    for name in ("update", "put_graph", "put_default_graph", "delete_graph", "create_dataset"):
        assert not hasattr(VirtualGraphClient, name), f"{name} を足してはいけない"
