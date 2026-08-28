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
