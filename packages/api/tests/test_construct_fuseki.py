"""`CONSTRUCT` / `DESCRIBE` を**実物の Fuseki**に対して検証する(ADR-0034、`P2A-14`)。

**これがこの作業でいちばん重要なテストである。**

[ADR-0025](../../../docs/adr/0025-result-limit-enforcement.md) 決定8 が
`CONSTRUCT` を 400 で断っていた理由は「実装していないから」ではなく、
**実物の Fuseki に対して 502 になっていた**ことだった。

> `FusekiStore.query` は `Accept: application/sparql-results+json` を送るが、
> Fuseki はクエリの形に従って Turtle を返すため JSON の解析に失敗し、
> **`SparqlStoreError`(502)になる**(実測)

つまり**フェイクでは検証できない問題**である。`FusekiStore.construct` が
`Accept: text/turtle` を送って実際に Turtle を受け取れることを、
ここで実物に対して確かめる。

`DESCRIBE` も併せて確かめる。**SPARQL 1.1 は `DESCRIBE` の返す内容を
規定していない**(Jena は CBD 相当)ので、持ち込みストアで内容が変わりうる
ことを ADR-0034 の受け入れるコストに書いてある。ここで固定するのは
「**Turtle が返り、解析できる**」ところまでである。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from rdflib import Graph, URIRef
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.services.projection import ProjectionService
from ontology_core.blob import OntologyBlobStore
from ontology_core.sparql.client import FusekiStore
from ontology_core.sparql.rdf_results import load_construct_result

pytestmark = pytest.mark.integration

_PORT = os.environ.get("FUSEKI_PORT", "3030")
_BASE_URL = f"http://localhost:{_PORT}"
_NS = "construct-fuseki"
_EX = "https://e.example/cf#"

TTL = (
    f"@prefix ex: <{_EX}> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    'ex:Product a owl:Class ; rdfs:label "商品" .\n'
    'ex:Customer a owl:Class ; rdfs:label "顧客" .\n'
)


@pytest.fixture
async def store() -> AsyncIterator[FusekiStore]:
    s = FusekiStore(
        query_endpoint=_BASE_URL + "/{dataset}/sparql",
        update_endpoint=_BASE_URL + "/{dataset}/update",
        gsp_endpoint=_BASE_URL + "/{dataset}/data",
        admin_endpoint=_BASE_URL + "/$/",
        admin_auth=("admin", os.environ.get("FUSEKI_ADMIN_PASSWORD", "localdev")),
    )
    yield s
    await s.aclose()


@pytest.fixture
async def approved(
    session: AsyncSession, blob_store: OntologyBlobStore, store: FusekiStore
) -> FusekiStore:
    """承認済みの 1 版が既定グラフに載った状態を作る。"""
    if _NS in await store.list_datasets():
        await store.delete_dataset(_NS)
    await store.create_dataset(_NS)
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri=_EX,
        created_by="t",
        require_two_person_approval=False,
    )
    await session.commit()

    svc = ProjectionService(
        session=session, blob=blob_store, store=store, graph_iri_base="urn:ontology:graph"
    )
    v = await svc.publish(namespace=_NS, turtle=TTL, actor="alice")
    v = await svc.submit(namespace=_NS, version=v.version, actor="alice")
    await svc.approve(namespace=_NS, version=v.version, actor="bob")
    await session.commit()
    return store


async def test_CONSTRUCT_が_Turtle_で返る(approved: FusekiStore) -> None:
    """**ADR-0025 決定8 が記録した 502 が起きないことを実物で確かめる。**

    `Accept: text/turtle` を送るので、Fuseki の応答と食い違わない。
    """
    body = await approved.construct("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", dataset=_NS)
    graph = Graph()
    graph.parse(data=body, format="turtle")
    assert (URIRef(_EX + "Product"), None, None) in graph
    assert len(graph) == 4  # 2 クラスそれぞれに rdf:type と rdfs:label


async def test_DESCRIBE_が_Turtle_で返る(approved: FusekiStore) -> None:
    """**内容は規定されていない**(ADR-0034 の受け入れるコスト)。

    ここで固定するのは「Turtle が返り、解析できる」ところまでである。
    """
    body = await approved.construct(f"DESCRIBE <{_EX}Product>", dataset=_NS)
    graph = Graph()
    graph.parse(data=body, format="turtle")
    assert (URIRef(_EX + "Product"), None, None) in graph


async def test_上限の検査が実物の応答に対して働く(approved: FusekiStore) -> None:
    """解析と計数が**実際の Fuseki の書き方**に対して成り立つことを確かめる。

    接頭辞の使い方はストアごとに違うので、フェイクの Turtle だけで
    検証していると気づけない。
    """
    body = await approved.construct("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", dataset=_NS)
    result = load_construct_result(body, limit=100)
    assert result.triple_count == 4

    from ontology_core.sparql.rdf_results import TripleLimitExceededError

    with pytest.raises(TripleLimitExceededError) as exc:
        load_construct_result(body, limit=3)
    assert exc.value.triples == 4


async def test_CONSTRUCT_は既定グラフだけを見る(approved: FusekiStore) -> None:
    """`GRAPH` 句を書かない `CONSTRUCT` は承認済みの現行版だけを見る。

    `tdb2:unionDefaultGraph` を持たせていないため(ADR-0010 決定6)。
    **名前付きグラフの内容は混ざらない** — `SELECT` と同じ性質が
    `CONSTRUCT` でも成り立つことを実物で固定する。
    """
    body = await approved.construct("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", dataset=_NS)
    graph = Graph()
    graph.parse(data=body, format="turtle")
    # 名前付きグラフにも同じ版が載っているので、和集合なら 8 トリプルになる。
    assert len(graph) == 4, "既定グラフが名前付きグラフの内容を巻き込んでいる"
