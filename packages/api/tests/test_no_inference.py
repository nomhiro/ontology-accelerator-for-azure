"""ストアは推論しない。プロパティパスは辿れる(ADR-0028、`P2B-15`)。

**実物の Fuseki に対して検証する。** ここで固定するのは 2 つの事実で、
どちらも**フェイクでは意味を持たない**(フェイクは「推論しない」ことを
証明しない — 何も実装していないだけである)。

1. **含意は返らない。** `A ⊑ B ⊑ C` を主張しても `?s rdfs:subClassOf C` は
   `A` を返さない
2. **プロパティパスなら辿れる。** `rdfs:subClassOf+` は返す

2 つ目は README と MCP のツール説明が**エージェントに勧めている手段**である。
勧めたものが実際に動くことを、ここで確かめている(ADR-0028 決定3)。

将来 `ja:InfModel` を入れたり導出トリプルを射影したりすると **1 つ目が落ちる**。
そのときは ADR-0028 決定5 の 3 条件を満たしているか確認すること。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.services.projection import ProjectionService
from ontology_core.blob import OntologyBlobStore
from ontology_core.sparql.client import FusekiStore

pytestmark = pytest.mark.integration

_PORT = os.environ.get("FUSEKI_PORT", "3030")
_BASE = f"http://localhost:{_PORT}"
_NS = "no-inference"
_EX = "https://e.example/#"

# `Premium ⊑ Customer ⊑ Party`。**推移的な含意は書いていない。**
# 個体 `alice` は `Premium` にだけ属する。
TTL = (
    f"@prefix ex: <{_EX}> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "ex:Party a owl:Class .\n"
    "ex:Customer a owl:Class ; rdfs:subClassOf ex:Party .\n"
    "ex:Premium a owl:Class ; rdfs:subClassOf ex:Customer .\n"
    "ex:alice a ex:Premium .\n"
)


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


@pytest.fixture
async def approved(
    session: AsyncSession, blob_store: OntologyBlobStore, store: FusekiStore
) -> FusekiStore:
    """承認済みの 1 版が既定グラフに載った状態を作る。

    **データセットは毎回作り直す。** Fuseki 上のデータは PostgreSQL の
    `session` フィクスチャとは独立して残るため、前のテストの内容が漏れる
    (`test_state_projection.py` が同じ理由で同じことをしている)。
    """
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
    return store


async def _subjects(store: FusekiStore, where: str) -> list[str]:
    result = await store.query(f"SELECT ?s WHERE {{ {where} }}", dataset=_NS)
    return sorted(b["s"]["value"] for b in result["results"]["bindings"])


# --------------------------------------------------------- 推論しないこと


async def test_下位クラスの推移は返らない(approved: FusekiStore) -> None:
    """**主張されていないトリプルは返らない**(ADR-0028 決定1・2)。

    `Premium ⊑ Customer ⊑ Party` を主張しても、直接の
    `?s rdfs:subClassOf ex:Party` は `Customer` しか返さない。

    **エージェントはこれを「Party の部分クラスは Customer だけ」と読む。**
    だからこそツール説明でプロパティパスを案内している。
    """
    direct = await _subjects(
        approved, f"?s <http://www.w3.org/2000/01/rdf-schema#subClassOf> <{_EX}Party>"
    )
    assert direct == [f"{_EX}Customer"], (
        "推論が有効になっている。ADR-0028 決定5 の 3 条件を満たしたか確認すること"
    )


async def test_個体の型の推移も返らない(approved: FusekiStore) -> None:
    """`alice a Premium` と `Premium ⊑ Customer` から `alice a Customer` は出ない。"""
    found = await _subjects(
        approved, f"?s <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <{_EX}Party>"
    )
    assert found == []


# ------------------------------------------------- プロパティパスは辿れる


async def test_プロパティパスなら下位クラスを辿れる(approved: FusekiStore) -> None:
    """**ツール説明が勧めている手段が実際に動くことを確かめる**(決定3)。

    プロパティパスは推論ではなく**グラフの到達可能性**なので、
    返ってきた経路はすべて誰かが承認した公理である(主張と導出が混ざらない)。
    """
    found = await _subjects(
        approved, f"?s <http://www.w3.org/2000/01/rdf-schema#subClassOf>+ <{_EX}Party>"
    )
    assert found == [f"{_EX}Customer", f"{_EX}Premium"]


async def test_プロパティパスなら個体の型を辿れる(approved: FusekiStore) -> None:
    """`rdf:type/rdfs:subClassOf*` で個体の上位クラスまで届く。"""
    result = await approved.query(
        "SELECT ?c WHERE { "
        f"<{_EX}alice> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
        "/<http://www.w3.org/2000/01/rdf-schema#subClassOf>* ?c }",
        dataset=_NS,
    )
    classes = sorted(b["c"]["value"] for b in result["results"]["bindings"])
    assert classes == [f"{_EX}Customer", f"{_EX}Party", f"{_EX}Premium"]
