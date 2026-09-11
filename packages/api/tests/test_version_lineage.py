"""何から編集したかの記録(ADR-0027、`P2A-15`)。

`publish` は `base_version` を受け取っていたが、**lost update の検出にだけ
使って捨てていた**(`P1-13`)。検査が通れば「編集の基準は当時の最新版だった」
と分かっているのに、その事実をどこにも書いていなかった。

ここで固定するのは **3 状態の区別**である(ADR-0027 決定1)。

| `edited_from_recorded` | `edited_from` | 意味 |
|---|---|---|
| `False` | `None` | **分からない**(宣言されなかった) |
| `True` | `None` | この名前空間に先行する版が無かった(最初の版) |
| `True` | `"1.0.0"` | 1.0.0 から編集した |

**1 本の列にすると「宣言されなかった」が「派生していない」として読める。**
そして 3 つ目の場合に「当時の最新版」を書いてはいけない — それは ADR-0026
決定2 が API の手前で拒否したもの(承認の順序を派生として主張する)を、
**正本に書き込む**形である。
"""

from __future__ import annotations

from typing import Any

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import PROV, RDF
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_api.routers.audit import export_provenance
from ontology_api.services.projection import ProjectionService
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.models import NamespaceRole, PlatformRole
from ontology_core.prov import ONT, REVISION_BASE
from ontology_core.sparql.client import SparqlStore

_NS = "lineage-ns"
_BASE_IRI = "https://e.example/#"
_GRAPH_BASE = "urn:ontology:graph"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")


def _ttl(term: str) -> str:
    return f"@prefix e: <{_BASE_IRI}> .\ne:{term} a <http://www.w3.org/2002/07/owl#Class> .\n"


class _NullStore(SparqlStore):
    """射影を行わない代役。系譜は正本側の話なのでストアは関係しない。"""

    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
        return {"results": {"bindings": []}}

    async def construct(self, sparql: str, *, dataset: str) -> str:
        return ""

    async def update(self, sparql: str, *, dataset: str) -> None: ...
    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None: ...
    async def put_default_graph(self, turtle: str, *, dataset: str) -> None: ...
    async def delete_graph(self, graph_iri: str, *, dataset: str) -> None: ...
    async def list_graphs(self, dataset: str) -> list[str]:
        return []

    async def has_default_graph_content(self, dataset: str) -> bool:
        return True

    async def list_datasets(self) -> list[str]:
        return []

    async def create_dataset(self, dataset: str) -> None: ...
    async def delete_dataset(self, dataset: str) -> None: ...


async def _setup(session: AsyncSession, blob: OntologyBlobStore) -> ProjectionService:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri=_BASE_IRI,
        created_by=_ADMIN.object_id,
        require_two_person_approval=False,
    )
    await RoleRepository(session).grant(
        namespace=_NS,
        principal_id=_ANALYST.object_id,
        role=NamespaceRole.DATA_ANALYST,
        granted_by=_ADMIN.object_id,
    )
    await session.commit()
    return ProjectionService(
        session=session,
        blob=blob,
        store=_NullStore(),
        graph_iri_base=_GRAPH_BASE,
    )


# ------------------------------------------------------------ 3 つの状態


@pytest.mark.integration
async def test_最初の版は先行版が無かったことを記録する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**推測ではなく測った事実である**(ADR-0027 決定2)。

    この publish の時点で名前空間の行ロックを持っているので、
    `latest is None` は「この名前空間に版が存在しない」という事実である。
    """
    service = await _setup(session, blob_store)
    published = await service.publish(
        namespace=_NS, turtle=_ttl("A"), actor=_ADMIN.object_id, version="1.0.0"
    )
    assert published.edited_from_recorded is True
    assert published.edited_from is None


@pytest.mark.integration
async def test_基準を渡せばそれを記録する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    service = await _setup(session, blob_store)
    await service.publish(namespace=_NS, turtle=_ttl("A"), actor=_ADMIN.object_id, version="1.0.0")
    second = await service.publish(
        namespace=_NS,
        turtle=_ttl("B"),
        actor=_ADMIN.object_id,
        version="2.0.0",
        base_version="1.0.0",
    )
    assert second.edited_from_recorded is True
    assert second.edited_from == "1.0.0"


@pytest.mark.integration
async def test_基準を渡さなければ分からないままにする(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**「当時の最新版」を書いてはいけない**(ADR-0027 決定2)。

    それは ADR-0026 決定2 が API の手前で拒否したもの(承認の順序を派生として
    主張する)を正本に書き込む形である。**正本に書いた推測は、後から事実と
    区別できない。**
    """
    service = await _setup(session, blob_store)
    await service.publish(namespace=_NS, turtle=_ttl("A"), actor=_ADMIN.object_id, version="1.0.0")
    second = await service.publish(
        namespace=_NS, turtle=_ttl("B"), actor=_ADMIN.object_id, version="2.0.0"
    )
    assert second.edited_from_recorded is False
    assert second.edited_from is None, "当時の最新版(1.0.0)を親として書いてはいけない"


@pytest.mark.integration
async def test_冪等な再投入は系譜を書き換えない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**公開済みの版は書き換えない**(不変条件7)。

    同じ本文の再送(タイムアウト後の再試行など)で既存の行を返すとき、
    系譜を上書きすると「最初に記録した事実」が消える。
    """
    service = await _setup(session, blob_store)
    await service.publish(namespace=_NS, turtle=_ttl("A"), actor=_ADMIN.object_id, version="1.0.0")
    second = await service.publish(
        namespace=_NS,
        turtle=_ttl("B"),
        actor=_ADMIN.object_id,
        version="2.0.0",
        base_version="1.0.0",
    )
    # 同じ本文を、基準を渡さずに再投入する。
    again = await service.publish(namespace=_NS, turtle=_ttl("B"), actor=_ADMIN.object_id)
    assert again.version == second.version
    assert again.edited_from == "1.0.0"
    assert again.edited_from_recorded is True


# ------------------------------------------------------------ 参照の絞り込み


@pytest.mark.integration
async def test_指定した版だけを引く(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    """**名前空間の全版を引かない**(ADR-0027 決定6)。"""
    service = await _setup(session, blob_store)
    for i, term in enumerate("ABC"):
        await service.publish(
            namespace=_NS, turtle=_ttl(term), actor=_ADMIN.object_id, version=f"1.{i}.0"
        )
    rows = await VersionRepository(session).get_many(_NS, {"1.0.0", "1.2.0"})
    assert {r.version for r in rows} == {"1.0.0", "1.2.0"}


@pytest.mark.integration
async def test_空の集合では問い合わせない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    service = await _setup(session, blob_store)
    await service.publish(namespace=_NS, turtle=_ttl("A"), actor=_ADMIN.object_id, version="1.0.0")
    assert await VersionRepository(session).get_many(_NS, set()) == []


@pytest.mark.integration
async def test_他の名前空間の版は引かない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """名前空間は隔離の境界である(不変条件5)。"""
    service = await _setup(session, blob_store)
    await service.publish(namespace=_NS, turtle=_ttl("A"), actor=_ADMIN.object_id, version="1.0.0")
    assert await VersionRepository(session).get_many("other-ns", {"1.0.0"}) == []


# --------------------------------------------------------- PROV-O への反映


@pytest.mark.integration
async def test_書き出しに_prov_wasDerivedFrom_が出る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """ADR-0006 決定3 が名前を挙げていた語彙を、**測った事実として**出す。"""
    service = await _setup(session, blob_store)
    await service.publish(namespace=_NS, turtle=_ttl("A"), actor=_ADMIN.object_id, version="1.0.0")
    await service.publish(
        namespace=_NS,
        turtle=_ttl("B"),
        actor=_ADMIN.object_id,
        version="2.0.0",
        base_version="1.0.0",
    )
    await session.commit()

    response = await export_provenance(namespace=_NS, principal=_ANALYST, session=session)
    graph = Graph()
    graph.parse(data=bytes(response.body).decode("utf-8"), format="turtle")

    child = URIRef(f"{REVISION_BASE}{_NS}/2.0.0")
    parent = URIRef(f"{REVISION_BASE}{_NS}/1.0.0")
    assert (child, PROV.wasDerivedFrom, parent) in graph
    assert (child, ONT.editedFromRecorded, Literal(True)) in graph
    assert (parent, RDF.type, PROV.Entity) in graph
    # 最初の版は「先行版が無かった」と記録されているが、辺は出ない。
    assert (parent, ONT.editedFromRecorded, Literal(True)) in graph
    assert not list(graph.triples((parent, PROV.wasDerivedFrom, None)))


@pytest.mark.integration
async def test_基準を渡していない版は記録なしとして出る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**無言の欠落にしない**(ADR-0027 決定5)。"""
    service = await _setup(session, blob_store)
    await service.publish(namespace=_NS, turtle=_ttl("A"), actor=_ADMIN.object_id, version="1.0.0")
    await service.publish(namespace=_NS, turtle=_ttl("B"), actor=_ADMIN.object_id, version="2.0.0")
    await session.commit()

    response = await export_provenance(namespace=_NS, principal=_ANALYST, session=session)
    graph = Graph()
    graph.parse(data=bytes(response.body).decode("utf-8"), format="turtle")

    child = URIRef(f"{REVISION_BASE}{_NS}/2.0.0")
    assert (child, ONT.editedFromRecorded, Literal(False)) in graph
    assert not list(graph.triples((child, PROV.wasDerivedFrom, None)))
