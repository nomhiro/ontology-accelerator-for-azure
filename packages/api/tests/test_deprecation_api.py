"""廃止の API 経路のテスト(P2B-03、ADR-0017)。

- `GET .../deprecations` が承認前に問題を見せる
- `approve` が **`blocking` の問題だけ**で 422 になる
- SPARQL の応答が廃止済み用語を**ヘッダ**に載せる(本文は標準のまま)
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.routers.sparql import (
    DEPRECATED_TERMS_HEADER,
    SparqlQueryRequest,
    run_query,
)
from ontology_api.routers.versions import (
    PublishRequest,
    approve_version,
    check_version_deprecations,
    publish_version,
)
from ontology_api.services.projection import ProjectionService
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.models import NamespaceRole, OntologyVersion, PlatformRole
from ontology_core.sparql.client import SparqlStore

_NS = "dep-api-ns"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_STEWARD = Principal(subject="steward", object_id="steward-oid")
_MAINTAINER = Principal(subject="maintainer", object_id="maintainer-oid")
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")

_HEAD = """
@prefix ex: <https://e.example/#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
"""
_V1 = _HEAD + "ex:Product a owl:Class .\nex:Customer a owl:Class .\n"
# 削除(ブロックされる)
_V_REMOVED = _HEAD + "ex:Product a owl:Class .\n"
# 廃止だが後継も理由も無い(ブロックされる)
_V_BARE = _HEAD + "ex:Product a owl:Class .\nex:Customer a owl:Class ; owl:deprecated true .\n"
# 正しい廃止 + 生きている用語からの参照(参照は報告のみ)
_V_OK = (
    _HEAD
    + "ex:Product a owl:Class ; rdfs:subClassOf ex:Customer .\n"
    + "ex:Customer a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:Product .\n"
)


class _NullStore(SparqlStore):
    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
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


async def _setup(session: AsyncSession) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri="https://e.example/#",
        created_by=_ADMIN.object_id,
        require_two_person_approval=False,
    )
    repo = RoleRepository(session)
    for principal, role in (
        (_STEWARD, NamespaceRole.DATA_STEWARD),
        (_MAINTAINER, NamespaceRole.MAINTAINER),
        (_ANALYST, NamespaceRole.DATA_ANALYST),
    ):
        await repo.grant(
            namespace=_NS,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await session.commit()


async def _publish(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    settings: Settings,
    *,
    version: str,
    turtle: str,
) -> OntologyVersion:
    return await publish_version(
        namespace=_NS,
        payload=PublishRequest(version=version, turtle=turtle),
        principal=_STEWARD,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
        response=Response(),
    )


async def _approve_via_router(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings, *, version: str
) -> OntologyVersion:
    service = ProjectionService(
        session=session, blob=blob_store, store=_NullStore(), graph_iri_base="urn:ontology:graph"
    )
    await service.submit(namespace=_NS, version=version, actor=_STEWARD.object_id)
    return await approve_version(
        namespace=_NS,
        version=version,
        principal=_MAINTAINER,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
    )


async def _deprecations(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    settings: Settings,
    *,
    version: str,
    principal: Principal = _ANALYST,
) -> dict[str, Any]:
    return await check_version_deprecations(
        namespace=_NS,
        version=version,
        principal=principal,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
    )


# ---------------------------------------------------------------- 承認のブロック


@pytest.mark.integration
async def test_削除した版は_422_で拒否される(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """ADR-0017 決定2・4。**ADR-0016 決定6 が送った判断をここで実行している。**"""
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve_via_router(session, blob_store, settings, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V_REMOVED)

    with pytest.raises(HTTPException) as exc:
        await _approve_via_router(session, blob_store, settings, version="2.0.0")
    assert exc.value.status_code == 422
    assert "削除" in str(exc.value.detail)
    assert "owl:deprecated" in str(exc.value.detail), "従う手段を示さなければ直せない"


@pytest.mark.integration
async def test_後継も理由も無い廃止は_422_で拒否される(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve_via_router(session, blob_store, settings, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V_BARE)

    with pytest.raises(HTTPException) as exc:
        await _approve_via_router(session, blob_store, settings, version="2.0.0")
    assert exc.value.status_code == 422
    assert "後継" in str(exc.value.detail)


@pytest.mark.integration
async def test_参照だけなら承認できる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**報告に留める問題で承認を止めない**(ADR-0017 決定2)。

    SHACL の形状やマッピングが廃止された用語を正当に参照するため、ここを
    ブロックすると移行のための記述が書けなくなる。
    """
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve_via_router(session, blob_store, settings, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V_OK)

    approved = await _approve_via_router(session, blob_store, settings, version="2.0.0")
    assert approved.version == "2.0.0"


@pytest.mark.integration
async def test_最初の承認は削除の検査を受けない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """基準が無いので削除もありえない(ADR-0017 決定4)。"""
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    approved = await _approve_via_router(session, blob_store, settings, version="1.0.0")
    assert approved.version == "1.0.0"


# ---------------------------------------------------------------- 承認前の報告


@pytest.mark.integration
async def test_承認前に問題を見られる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**状態を変えずに報告する。** レビュー画面が承認前に呼ぶための口である。"""
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve_via_router(session, blob_store, settings, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V_REMOVED)

    report = await _deprecations(session, blob_store, settings, version="2.0.0")
    assert report["base_version"] == "1.0.0"
    assert report["blocking"] is True
    assert [p["kind"] for p in report["problems"]] == ["removed"]
    assert report["problems"][0]["term"] == "https://e.example/#Customer"

    # 報告を見ただけで状態は変わらない(まだ承認できる状態のまま)。
    again = await _deprecations(session, blob_store, settings, version="2.0.0")
    assert again == report


@pytest.mark.integration
async def test_報告のみの問題は_blocking_が_false(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve_via_router(session, blob_store, settings, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V_OK)

    report = await _deprecations(session, blob_store, settings, version="2.0.0")
    assert report["blocking"] is False
    kinds = [p["kind"] for p in report["problems"]]
    assert kinds == ["references-deprecated"]
    assert report["problems"][0]["referenced"] == "https://e.example/#Customer"


@pytest.mark.integration
async def test_報告には_data_analyst_が必要(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    stranger = Principal(subject="stranger", object_id="stranger-oid")
    with pytest.raises(HTTPException) as exc:
        await _deprecations(session, blob_store, settings, version="1.0.0", principal=stranger)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_存在しない版は_404(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _deprecations(session, blob_store, settings, version="9.9.9")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------- SPARQL の警告


class _DeprecationStore(_NullStore):
    """廃止済み用語を返し、クエリの結果にもそれを含めるストアの代役。"""

    def __init__(self, *, deprecated: list[str], results: list[str]) -> None:
        self._deprecated = deprecated
        self._results = results
        self.queries: list[str] = []

    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        self.queries.append(sparql)
        values = self._deprecated if "owl#deprecated" in sparql else self._results
        return {
            "head": {"vars": ["s"]},
            "results": {"bindings": [{"s": {"type": "uri", "value": v}} for v in values]},
        }


class _FailingDeprecationStore(_NullStore):
    """廃止済み用語の取得だけが失敗するストアの代役。"""

    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        if "owl#deprecated" in sparql:
            from ontology_core.sparql.client import SparqlStoreError

            raise SparqlStoreError("到達できません")
        return {"head": {"vars": ["s"]}, "results": {"bindings": []}}


async def _run(
    session: AsyncSession, settings: Settings, store: SparqlStore, response: Response
) -> dict[str, Any]:
    result = await run_query(
        namespace=_NS,
        payload=SparqlQueryRequest(query="SELECT ?s WHERE { ?s ?p ?o }"),
        principal=_ANALYST,
        session=session,
        settings=settings,
        store=store,
        response=response,
    )
    # **`run_query` の戻り値は `dict | Response` である**(ADR-0034 決定3)。
    # ここは `SELECT` の経路なので `dict` に絞る。
    assert isinstance(result, dict), "SELECT の経路が Response を返している"
    return result


@pytest.mark.integration
async def test_廃止済み用語がヘッダに載る(session: AsyncSession, settings: Settings) -> None:
    """**本文は標準の SPARQL Results JSON のまま**(ADR-0017 決定3)。

    ADR-0001 の「SPARQL 1.1 Protocol をハード境界にする」を守るため、本文に
    独自のキーを混ぜない。
    """
    await _setup(session)
    store = _DeprecationStore(
        deprecated=["https://e.example/#Old"],
        results=["https://e.example/#Old", "https://e.example/#Live"],
    )
    response = Response()
    results = await _run(session, settings, store, response)

    assert response.headers[DEPRECATED_TERMS_HEADER] == "https://e.example/#Old"
    assert set(results) == {"head", "results"}, "本文に独自のキーを混ぜてはいけない"


@pytest.mark.integration
async def test_結果に現れなければヘッダを付けない(
    session: AsyncSession, settings: Settings
) -> None:
    await _setup(session)
    store = _DeprecationStore(
        deprecated=["https://e.example/#Old"], results=["https://e.example/#Live"]
    )
    response = Response()
    await _run(session, settings, store, response)
    assert DEPRECATED_TERMS_HEADER not in response.headers


@pytest.mark.integration
async def test_廃止が無ければ余計な走査をしない(session: AsyncSession, settings: Settings) -> None:
    await _setup(session)
    store = _DeprecationStore(deprecated=[], results=["https://e.example/#Live"])
    response = Response()
    await _run(session, settings, store, response)
    assert DEPRECATED_TERMS_HEADER not in response.headers


@pytest.mark.integration
async def test_廃止の取得が失敗してもクエリは成功する(
    session: AsyncSession, settings: Settings
) -> None:
    """**警告のために本来の応答を壊してはいけない**(ADR-0017)。

    廃止の警告が出ないのは劣化だが、クエリそのものが失敗するのは回帰である。
    """
    await _setup(session)
    response = Response()
    results = await _run(session, settings, _FailingDeprecationStore(), response)
    assert results["results"]["bindings"] == []
    assert DEPRECATED_TERMS_HEADER not in response.headers
