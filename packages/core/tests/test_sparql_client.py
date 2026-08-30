"""FusekiStore のトランスポート例外の扱い。"""

from __future__ import annotations

import httpx
import pytest

from ontology_core.sparql.client import FusekiStore, SparqlStoreError

# 誰も listen していないポート。接続がすぐに拒否されるためテストが遅くならない。
_UNREACHABLE = "http://localhost:59999"


def _store() -> FusekiStore:
    return FusekiStore(
        query_endpoint=f"{_UNREACHABLE}/{{dataset}}/sparql",
        update_endpoint=f"{_UNREACHABLE}/{{dataset}}/update",
        gsp_endpoint=f"{_UNREACHABLE}/{{dataset}}/data",
        admin_endpoint=f"{_UNREACHABLE}/$/",
        timeout_seconds=2,
    )


async def test_transport_errors_are_wrapped_in_query() -> None:
    """到達不能なストアでも SparqlStoreError で来ること。

    呼び出し側は「ストアの問題は SparqlStoreError」という契約に依拠して
    トランザクションの境界を決める。httpx の例外が漏れるとその判断が壊れる。
    """
    store = _store()
    try:
        with pytest.raises(SparqlStoreError):
            await store.query("SELECT * WHERE { ?s ?p ?o }", dataset="ds")
    finally:
        await store.aclose()


async def test_transport_errors_are_wrapped_in_put_graph() -> None:
    store = _store()
    try:
        with pytest.raises(SparqlStoreError):
            await store.put_graph("urn:g", "<urn:a> <urn:b> <urn:c> .", dataset="ds")
    finally:
        await store.aclose()


async def test_transport_errors_are_wrapped_in_create_dataset() -> None:
    store = _store()
    try:
        with pytest.raises(SparqlStoreError):
            await store.create_dataset("ds")
    finally:
        await store.aclose()


async def test_wrapped_error_is_not_a_bare_httpx_error() -> None:
    """SparqlStoreError 以外(生の httpx 例外)は外に漏れないこと。"""
    store = _store()
    try:
        with pytest.raises(SparqlStoreError):
            try:
                await store.query("SELECT * WHERE { ?s ?p ?o }", dataset="ds")
            except httpx.HTTPError as exc:
                pytest.fail(f"httpx の例外が生のまま漏れた: {exc!r}")
    finally:
        await store.aclose()


def _store_with_mock_response(body: str) -> tuple[FusekiStore, httpx.AsyncClient]:
    """HTTP 200 で任意の本文を返すだけのモックトランスポートを使うストア。

    `_send` は httpx の接続例外(HTTPError)しか `SparqlStoreError` に包まない。
    「HTTP 200 だが本文が期待した形でない」経路を再現するには、実際に 200 を
    返すトランスポートが必要(到達不能ポートでは httpx.HTTPError にしかならない)。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = FusekiStore(
        query_endpoint=f"{_UNREACHABLE}/{{dataset}}/sparql",
        update_endpoint=f"{_UNREACHABLE}/{{dataset}}/update",
        gsp_endpoint=f"{_UNREACHABLE}/{{dataset}}/data",
        admin_endpoint=f"{_UNREACHABLE}/$/",
        client=client,
    )
    return store, client


async def test_non_json_body_is_wrapped_in_query() -> None:
    """M-6: HTTP 200 + 非 JSON 本文は `json.JSONDecodeError`(`ValueError` の
    サブクラス)のまま `_send` の外に漏れず、`SparqlStoreError` に包まれること。

    契約が破れると `routers/sparql.py` の `except SparqlStoreError` をすり抜けて
    502 ではなく未捕捉の 500 になる。
    """
    store, client = _store_with_mock_response("<html>not json</html>")
    try:
        with pytest.raises(SparqlStoreError):
            await store.query("SELECT * WHERE { ?s ?p ?o }", dataset="ds")
    finally:
        await store.aclose()
        await client.aclose()


async def test_non_json_body_is_wrapped_in_list_datasets() -> None:
    """`list_datasets` も同じ経路(HTTP 200 + 非 JSON 本文)を持つ。

    契約が破れると `ProjectionService.reconcile()` の
    `except (SparqlStoreError, BlobStoreError)` をすり抜けて per-item の失敗
    として記録されず、ループ全体が中断する。
    """
    store, client = _store_with_mock_response("not json at all")
    try:
        with pytest.raises(SparqlStoreError):
            await store.list_datasets()
    finally:
        await store.aclose()
        await client.aclose()


async def test_unexpected_json_shape_is_wrapped_in_list_datasets() -> None:
    """`list_datasets` は JSON としては正しくても `entry["ds.name"]` の
    辞書アクセスで `KeyError` の余地がある(応答の形が想定と違う場合)。
    これも `SparqlStoreError` に包まれること。
    """
    store, client = _store_with_mock_response('{"datasets": [{"unexpected": "shape"}]}')
    try:
        with pytest.raises(SparqlStoreError):
            await store.list_datasets()
    finally:
        await store.aclose()
        await client.aclose()
