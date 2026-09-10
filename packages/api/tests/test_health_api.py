"""健全性指標の API 経路のテスト(P2B-06、ADR-0020)。

**最重要は「測れなかった」を「問題ゼロ」と報告しないこと**(決定3)。
正本の TTL に到達できないときに `0` を返すと、**障害時に「完全に健全」と
報告する**ことになる。

もう 1 つは**用語を正本から数える**こと(決定1)。ストアから数えると、
ストアが空のときに同じ壊れ方をする。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.access import AccessRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.term_owners import TermOwnerRepository
from ontology_api.routers.health import measure_health
from ontology_api.routers.versions import PublishRequest, publish_version
from ontology_api.services.projection import ProjectionService
from ontology_core.access import AccessRecord, query_fingerprint
from ontology_core.auth.entra import Principal
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.health import UNREFERENCED_WINDOW_DAYS
from ontology_core.models import NamespaceRole, PlatformRole
from ontology_core.sparql.client import SparqlStore

_NS = "health-ns"
_BASE = "https://e.example/#"

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
# 用語 3 つ + **外部語彙を主語に持つ行**(用語として数えない)。
#
# **主語に置くのが要点。** 目的語にしか現れない外部 IRI は `iri_subjects` の
# 側で落ちるので、`base_iri` の絞り込みを検証できない(変異テストで見逃した)。
_TTL = (
    _HEAD
    + 'ex:Product a owl:Class ; rdfs:label "商品" ; rdfs:subClassOf owl:Thing .\n'
    + 'ex:Customer a owl:Class ; rdfs:label "顧客" .\n'
    + 'ex:Order a owl:Class ; rdfs:label "注文" .\n'
    + 'owl:Thing rdfs:label "すべてのもの" .\n'
)


class _NullStore(SparqlStore):
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
        base_iri=_BASE,
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


async def _publish_and_approve(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings, *, turtle: str = _TTL
) -> None:
    await publish_version(
        namespace=_NS,
        payload=PublishRequest(version="1.0.0", turtle=turtle),
        principal=_STEWARD,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
        response=Response(),
    )
    service = ProjectionService(
        session=session, blob=blob_store, store=_NullStore(), graph_iri_base="urn:ontology:graph"
    )
    await service.submit(namespace=_NS, version="1.0.0", actor=_STEWARD.object_id)
    await service.approve(namespace=_NS, version="1.0.0", actor=_MAINTAINER.object_id)
    await session.commit()


async def _health(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    *,
    principal: Principal = _ANALYST,
    include_terms: bool = False,
) -> dict[str, Any]:
    return await measure_health(
        namespace=_NS,
        principal=principal,
        session=session,
        blob=blob_store,
        include_terms=include_terms,
    )


# ---------------------------------------------------------------- 用語数


@pytest.mark.integration
async def test_用語は正本の_TTL_から数える(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**ストアからは数えない**(ADR-0020 決定1)。

    ここで渡している `_NullStore` は空のストアである。ストアから数える実装
    なら「用語 0」になるが、正本から数えるので 3 になる。**ストアが空でも
    正しく報告できることの実証**である。
    """
    await _setup(session)
    await _publish_and_approve(session, blob_store, settings)

    report = await _health(session, blob_store)
    assert report["term_count"] == 3, "ストアが空でも正本から数える"
    assert report["current_version"] == "1.0.0"
    assert report["unavailable"] == []


@pytest.mark.integration
async def test_外部語彙の_IRI_は用語に数えない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """`owl:Thing` を**主語に持つ行**があるが、`base_iri` 配下ではないので
    数えない(ADR-0020 決定1)。

    健全性指標が答えたいのは「自分のオントロジーのどの用語が使われて
    いないか」であり、`owl:Thing` の参照回数は指標にならない。
    """
    await _setup(session)
    await _publish_and_approve(session, blob_store, settings)
    report = await _health(session, blob_store, include_terms=True)
    assert report["term_count"] == 3, "owl:Thing は用語に数えない"
    assert all(t.startswith(_BASE) for t in report["without_owner_terms"])
    assert all(t.startswith(_BASE) for t in report["unreferenced_terms"])


@pytest.mark.integration
async def test_承認済み版が無ければ用語_0(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**障害ではなく事実である**(決定1)。`current_version` が `null` になる。"""
    await _setup(session)
    report = await _health(session, blob_store)
    assert report["term_count"] == 0
    assert report["current_version"] is None
    assert report["unavailable"] == []


# ---------------------------------------------------------------- 未参照


@pytest.mark.integration
async def test_一度も参照されていない用語を数える(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish_and_approve(session, blob_store, settings)
    report = await _health(session, blob_store)
    assert report["unreferenced_count"] == 3
    assert report["unreferenced_ratio"] == 1.0
    assert report["unreferenced_window_days"] == UNREFERENCED_WINDOW_DAYS


@pytest.mark.integration
async def test_参照された用語は未参照から外れる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """`P2B-05` のアクセスログが原資料である(ADR-0018 決定1)。"""
    await _setup(session)
    await _publish_and_approve(session, blob_store, settings)
    await AccessRepository(session).record(
        AccessRecord(
            namespace=_NS,
            actor="agent-oid",
            query=query_fingerprint("SELECT ?s WHERE { ?s ?p ?o }"),
            default_graph_version="1.0.0",
            used_graph_clause=False,
            returned_row_count=1,
            terms=(_BASE + "Product",),
        )
    )
    await session.commit()

    report = await _health(session, blob_store)
    assert report["unreferenced_count"] == 2
    assert report["unreferenced_ratio"] == 2 / 3


# ---------------------------------------------------------------- 責任者


@pytest.mark.integration
async def test_責任者が未設定の用語を数える(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish_and_approve(session, blob_store, settings)
    await TermOwnerRepository(session).assign(
        namespace=_NS,
        term_iri=_BASE + "Product",
        principal_id="expert-oid",
        assigned_by=_MAINTAINER.object_id,
    )
    await session.commit()

    report = await _health(session, blob_store)
    assert report["without_owner_count"] == 2


# ---------------------------------------------------------------- 版に関する項目


@pytest.mark.integration
async def test_承認からの経過日数を返す(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**版単位である**(決定2)。このシステムの承認は版単位なので、用語単位の
    「再承認の古さ」は計算できない。存在しない粒度をあるように見せない。
    """
    await _setup(session)
    await _publish_and_approve(session, blob_store, settings)
    report = await _health(session, blob_store)
    assert report["approval_age_days"] == 0


@pytest.mark.integration
async def test_SHACL_違反は承認済み版では_0(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**構造上ほぼ常に 0 である**(`approve` が違反をブロックするため)。

    それでも項目として残すのは、`P2A-05` より前に承認された版と shape 自身の
    問題を拾うためである。
    """
    await _setup(session)
    await _publish_and_approve(session, blob_store, settings)
    report = await _health(session, blob_store)
    assert report["shacl_violation_count"] == 0


@pytest.mark.integration
async def test_draft_は未射影に数えない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**`draft` は射影しないことが正常である**(ADR-0010 決定5)。

    `projected_at IS NULL` は `draft` にとっての通常の姿なので、これを
    「未射影」に数えると**常に警告が出て指標が意味を失う**
    (`VersionRepository.unprojected()` が `draft` を除外しているのと同じ理由)。
    """
    await _setup(session)
    await _publish_and_approve(session, blob_store, settings)

    # 承認しない draft を 1 つ足す。
    await publish_version(
        namespace=_NS,
        payload=PublishRequest(version="2.0.0", turtle=_TTL + "ex:Extra a owl:Class .\n"),
        principal=_STEWARD,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
        response=Response(),
    )
    await session.commit()

    report = await _health(session, blob_store)
    assert report["unprojected_version_count"] == 0, "draft を未射影に数えてはいけない"


# ---------------------------------------------------------------- 測れなかった


@pytest.mark.integration
async def test_正本に到達できなければ関連項目は_null(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**ここが本題**(ADR-0020 決定3)。

    `0` を返すと**障害時に「完全に健全」と報告する**ことになる。
    """
    await _setup(session)
    await _publish_and_approve(session, blob_store, settings)

    original = blob_store.get_version

    async def boom(path: str) -> str:
        raise BlobStoreError("到達できません")

    object.__setattr__(blob_store, "get_version", boom)
    try:
        report = await _health(session, blob_store)
    finally:
        object.__setattr__(blob_store, "get_version", original)

    assert report["term_count"] is None
    assert report["unreferenced_count"] is None
    assert report["unreferenced_ratio"] is None
    assert report["without_owner_count"] is None
    assert report["shacl_violation_count"] is None
    assert len(report["unavailable"]) == 1
    assert "取得できない" in report["unavailable"][0]


@pytest.mark.integration
async def test_正本に到達できなくても_DB_だけで測れる項目は返る(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**「全部か無か」にしない**(決定3)。

    Blob の一時的な不調で未射影の版の数まで見えなくなってはいけない。
    """
    await _setup(session)
    await _publish_and_approve(session, blob_store, settings)

    original = blob_store.get_version

    async def boom(path: str) -> str:
        raise BlobStoreError("到達できません")

    object.__setattr__(blob_store, "get_version", boom)
    try:
        report = await _health(session, blob_store)
    finally:
        object.__setattr__(blob_store, "get_version", original)

    assert report["approval_age_days"] == 0, "PostgreSQL だけで測れる項目は返る"
    assert report["unprojected_version_count"] == 0
    assert report["current_version"] == "1.0.0"


@pytest.mark.integration
async def test_測れなかったときに一覧を返さない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**空のリストを返さない。** 「未参照の用語は無い」と誤解させる。"""
    await _setup(session)
    await _publish_and_approve(session, blob_store, settings)

    original = blob_store.get_version

    async def boom(path: str) -> str:
        raise BlobStoreError("到達できません")

    object.__setattr__(blob_store, "get_version", boom)
    try:
        report = await _health(session, blob_store, include_terms=True)
    finally:
        object.__setattr__(blob_store, "get_version", original)

    assert report["unreferenced_terms"] is None
    assert report["without_owner_terms"] is None


# ---------------------------------------------------------------- 一覧と権限


@pytest.mark.integration
async def test_用語の一覧はオプトイン(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish_and_approve(session, blob_store, settings)

    without = await _health(session, blob_store)
    assert "unreferenced_terms" not in without

    with_terms = await _health(session, blob_store, include_terms=True)
    assert sorted(with_terms["unreferenced_terms"]) == [
        _BASE + "Customer",
        _BASE + "Order",
        _BASE + "Product",
    ]


@pytest.mark.integration
async def test_data_analyst_で読める(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**指標に強い権限を要求すると、指標が使われなくなる**(決定4)。"""
    await _setup(session)
    report = await _health(session, blob_store, principal=_ANALYST)
    assert report["namespace"] == _NS


@pytest.mark.integration
async def test_ロールを持たない主体は読めない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _health(session, blob_store, principal=_STRANGER)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_存在しない名前空間は_404(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await measure_health(
            namespace="no-such-ns",
            principal=_ADMIN,
            session=session,
            blob=blob_store,
            include_terms=False,
        )
    assert exc.value.status_code == 404


@pytest.mark.integration
async def test_不正な名前空間名は_400(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await measure_health(
            namespace="../ds",
            principal=_ADMIN,
            session=session,
            blob=blob_store,
            include_terms=False,
        )
    assert exc.value.status_code == 400
