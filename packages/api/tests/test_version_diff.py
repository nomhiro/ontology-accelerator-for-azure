"""承認時の差分の記録と、差分エンドポイントのテスト(P2B-09、ADR-0016)。

**中心にあるのは 2 つ。**

1. **基準は「この承認によって `superseded` になる版」である**(決定2)。
   publish 時点の「現行」ではない
2. **差分の計算に失敗しても承認は失敗しない**(決定「失敗させる」を却下)。
   差分は記述的なメタデータであって承認の前提条件ではない
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.routers.versions import (
    PublishRequest,
    diff_version,
    publish_version,
)
from ontology_api.services.projection import ProjectionService
from ontology_core.auth.entra import Principal
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.models import NamespaceRole, OntologyVersion, PlatformRole
from ontology_core.sparql.client import SparqlStore

_NS = "diff-ns"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_STEWARD = Principal(subject="steward", object_id="steward-oid")
_MAINTAINER = Principal(subject="maintainer", object_id="maintainer-oid")
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")

_HEAD = """
@prefix ex: <https://e.example/#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
"""
_V1 = _HEAD + 'ex:Product a owl:Class ; rdfs:label "商品" .\nex:Customer a owl:Class .\n'
_V2 = _HEAD + 'ex:Product a owl:Class ; rdfs:label "製品" .\nex:Customer a owl:Class .\n'
_V3 = _HEAD + 'ex:Product a owl:Class ; rdfs:label "製品" .\n'  # Customer を削除


class _NullStore(SparqlStore):
    """射影を行わない代役。"""

    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        return {"results": {"bindings": []}}

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


def _service(session: AsyncSession, blob: OntologyBlobStore) -> ProjectionService:
    return ProjectionService(
        session=session,
        blob=blob,
        store=_NullStore(),
        graph_iri_base="urn:ontology:graph",
    )


async def _publish(
    session: AsyncSession,
    blob: OntologyBlobStore,
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
        blob=blob,
        store=_NullStore(),
        settings=settings,
        response=Response(),
    )


async def _approve(
    session: AsyncSession, blob: OntologyBlobStore, *, version: str
) -> OntologyVersion:
    service = _service(session, blob)
    await service.submit(namespace=_NS, version=version, actor=_STEWARD.object_id)
    return await service.approve(
        namespace=_NS, version=version, actor=_MAINTAINER.object_id, reason="テスト"
    )


async def _diff_of_approval(session: AsyncSession, version: str) -> dict[str, Any] | None:
    """`approved` の監査記録に載った差分の要約を返す。"""
    events = await AuditRepository(session).list_for_subject(_NS, f"{_NS}@{version}")
    approved = [e for e in events if e.action == "approved"]
    assert approved, f"approved の記録が無い: {[e.action for e in events]}"
    raw = approved[-1].diff
    return None if raw is None else dict(json.loads(raw))


# ---------------------------------------------------------------- 承認時の記録


@pytest.mark.integration
async def test_最初の承認では差分を記録しない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**「何も無かったところに全部追加された」という差分は情報量が無い**
    (ADR-0016 決定2)。"""
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    assert await _diff_of_approval(session, "1.0.0") is None


@pytest.mark.integration
async def test_2_回目の承認で前の版との差分を記録する(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V2)
    await _approve(session, blob_store, version="2.0.0")

    diff = await _diff_of_approval(session, "2.0.0")
    assert diff is not None
    assert diff["base_version"] == "1.0.0", "基準は superseded になる版でなければならない"
    assert diff["modified_terms"] == ["https://e.example/#Product"]
    assert diff["added_terms"] == []
    assert diff["removed_terms"] == []
    assert diff["empty"] is False


@pytest.mark.integration
async def test_IRI_の削除が差分に現れる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**ADR-0009 決定3 の規律違反はここで見える。**

    ただし**承認はブロックしない**(ADR-0016 決定6)。廃止の仕組み(`P2B-03`)が
    無い状態で削除をブロックすると、縮める正当な手段が 1 つも無くなる。
    """
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V3)
    approved = await _approve(session, blob_store, version="2.0.0")

    assert approved.version == "2.0.0", "削除があっても承認は通る(報告するだけ)"
    diff = await _diff_of_approval(session, "2.0.0")
    assert diff is not None
    assert diff["removed_terms"] == ["https://e.example/#Customer"]
    assert diff["has_removed_terms"] is True


@pytest.mark.integration
async def test_差分の計算に失敗しても承認は成功する(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**不変条件3 と同じ向きの判断**(ADR-0016)。

    差分は説明のための記述的なメタデータであって承認の前提条件ではない。
    Blob へ到達できないだけで承認が止まってはいけない。
    """
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V2)

    service = _service(session, blob_store)
    calls = {"n": 0}
    original = blob_store.get_version

    async def flaky(path: str) -> str:
        # SHACL 検証(1 回目)は通し、差分の取得(2 回目以降)で落とす。
        calls["n"] += 1
        if calls["n"] >= 2:
            raise BlobStoreError("到達できません")
        return await original(path)

    await service.submit(namespace=_NS, version="2.0.0", actor=_STEWARD.object_id)
    object.__setattr__(blob_store, "get_version", flaky)
    try:
        approved = await service.approve(
            namespace=_NS, version="2.0.0", actor=_MAINTAINER.object_id, reason="テスト"
        )
    finally:
        object.__setattr__(blob_store, "get_version", original)

    assert approved.version == "2.0.0", "差分の失敗で承認が止まってはいけない"
    assert await _diff_of_approval(session, "2.0.0") is None, "差分は null のまま残る"


# ---------------------------------------------------------------- 差分エンドポイント


@pytest.mark.integration
async def test_基準を省略すると現在の_approved_版と比べる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V2)

    result = await diff_version(
        namespace=_NS,
        version="2.0.0",
        principal=_ANALYST,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
    )
    assert result["base_version"] == "1.0.0"
    assert result["diff"]["modified_terms"] == ["https://e.example/#Product"]


@pytest.mark.integration
async def test_基準を明示できる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V3)

    result = await diff_version(
        namespace=_NS,
        version="2.0.0",
        principal=_ANALYST,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
        base="1.0.0",
    )
    assert result["base_version"] == "1.0.0"
    assert result["diff"]["removed_terms"] == ["https://e.example/#Customer"]


@pytest.mark.integration
async def test_基準が無ければ_diff_は_null(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**404 にしない。** 「基準が無い」はエラーではなく答えるべき事実である
    (最初の版では必ずこうなる)。"""
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)

    result = await diff_version(
        namespace=_NS,
        version="1.0.0",
        principal=_ANALYST,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
    )
    assert result["base_version"] is None
    assert result["diff"] is None


@pytest.mark.integration
async def test_存在しない基準を指定すると_404(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**`diff: null` で返さない。** 指定の誤りを「基準が無い」と混同させない。"""
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)

    with pytest.raises(HTTPException) as exc:
        await diff_version(
            namespace=_NS,
            version="1.0.0",
            principal=_ANALYST,
            session=session,
            blob=blob_store,
            store=_NullStore(),
            settings=settings,
            base="9.9.9",
        )
    assert exc.value.status_code == 404


@pytest.mark.integration
async def test_存在しない版は_404(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await diff_version(
            namespace=_NS,
            version="9.9.9",
            principal=_ANALYST,
            session=session,
            blob=blob_store,
            store=_NullStore(),
            settings=settings,
        )
    assert exc.value.status_code == 404


@pytest.mark.integration
async def test_差分の参照には_data_analyst_が必要(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    with pytest.raises(HTTPException) as exc:
        await diff_version(
            namespace=_NS,
            version="1.0.0",
            principal=_STRANGER,
            session=session,
            blob=blob_store,
            store=_NullStore(),
            settings=settings,
        )
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_差分の参照は状態を変えない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**その場で決定するための道具**であって、決定そのものではない。"""
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V2)

    before = await AuditRepository(session).list_for_subject(_NS, f"{_NS}@2.0.0")
    await diff_version(
        namespace=_NS,
        version="2.0.0",
        principal=_ANALYST,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
    )
    after = await AuditRepository(session).list_for_subject(_NS, f"{_NS}@2.0.0")
    assert len(before) == len(after), "差分を見ただけで監査記録が増えてはいけない"
