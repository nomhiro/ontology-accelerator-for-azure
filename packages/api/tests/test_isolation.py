"""名前空間の隔離を実物の Fuseki に対して検証する。

ADR-0001 は「データセット単位の物理分離は検証可能」と述べている。
その主張をここで実際に確かめる。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest

from ontology_core.sparql.client import FusekiStore, SparqlStoreError

pytestmark = pytest.mark.integration

_PORT = os.environ.get("FUSEKI_PORT", "3030")
_BASE = f"http://localhost:{_PORT}"


@pytest.fixture
async def store() -> AsyncIterator[FusekiStore]:
    s = FusekiStore(
        query_endpoint=_BASE + "/{dataset}/sparql",
        update_endpoint=_BASE + "/{dataset}/update",
        gsp_endpoint=_BASE + "/{dataset}/data",
        admin_endpoint=_BASE + "/$/",
        admin_auth=("admin", os.environ.get("FUSEKI_ADMIN_PASSWORD", "localdev")),
    )
    yield s
    await s.aclose()


async def _count(store: FusekiStore, dataset: str, *, graph: str | None = None) -> int:
    """`graph` を指定しなければ既定グラフを数える。

    ADR-0010 決定6で動的に作るデータセットのテンプレート
    (containers/fuseki/templates/config-tdb2)から `tdb2:unionDefaultGraph` を
    外したため、既定グラフはもはや名前付きグラフの和集合ではない
    (P1-C1 の Critical への対処)。名前付きグラフに置いた内容の存在を確認する
    アサーションは、この変更後は明示的に `GRAPH` 句で数える必要がある。
    """
    query = (
        "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"
        if graph is None
        else f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{graph}> {{ ?s ?p ?o }} }}"
    )
    result = await store.query(query, dataset=dataset)
    return int(result["results"]["bindings"][0]["n"]["value"])


async def test_data_in_one_namespace_is_invisible_from_another(store: FusekiStore) -> None:
    for ns in ("iso-alpha", "iso-beta"):
        if ns not in await store.list_datasets():
            await store.create_dataset(ns)

    graph_iri = "urn:ontology:graph/iso-alpha/1.0.0"
    await store.put_graph(
        graph_iri,
        "@prefix ex: <https://e.example/#> .\nex:Secret a ex:Class .\n",
        dataset="iso-alpha",
    )

    assert await _count(store, "iso-alpha", graph=graph_iri) == 1
    # beta 側からは(既定グラフからも、同名グラフを名指ししても)1 件も見えないこと。
    assert await _count(store, "iso-beta") == 0

    # beta で alpha のグラフを名指ししても取れないこと。
    result = await store.query(
        "SELECT ?s WHERE { GRAPH <urn:ontology:graph/iso-alpha/1.0.0> { ?s ?p ?o } }",
        dataset="iso-beta",
    )
    assert result["results"]["bindings"] == []

    await store.delete_dataset("iso-alpha")
    await store.delete_dataset("iso-beta")


async def test_service_clause_is_blocked_on_dynamic_dataset(store: FusekiStore) -> None:
    """動的に作ったデータセットでも SERVICE がブロックされること。

    SERVICE の無効化はサーバーレベルの設定なので、admin API で作った
    データセットにも効くはず。効いていなければ SSRF の穴になる。
    """
    dataset = "iso-service"
    if dataset not in await store.list_datasets():
        await store.create_dataset(dataset)

    with pytest.raises(SparqlStoreError) as exc:
        await store.query(
            "SELECT ?s WHERE { SERVICE <http://169.254.169.254/> { ?s ?p ?o } }",
            dataset=dataset,
        )
    assert "422" in str(exc.value) or "disabled" in str(exc.value)

    await store.delete_dataset(dataset)
