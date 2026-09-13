"""定義と実データの乖離の測定(ADR-0047、`P3-05`)。

3 つの正本(承認済み版の TTL / R2RML マッピング / 仮想グラフ)を
突き合わせる部分を固定する。**仮想グラフは `httpx.MockTransport` で
差し替える** — 実物の Ontop に対する検証は
`containers/ontop/ontop-check.test.sh` が行う(そちらでしか
「その SPARQL が本当に動くか」は確かめられない)。

ここで固定するのは 6 つである。

1. **測れないときは空の報告を返さない**(決定6)。承認済み版が無い、
   マッピングが無い、正本が読めない — どれも例外になる
2. **マッピングが作っていないクラスは `unmapped`** で、**乖離として
   数えない**
3. **マッピングはあるのに 0 件なら `empty`**(これが乖離)
4. **仮想グラフに聞けなければ `unknown`** で、`conclusive` が偽になる
5. **クラスに実データが無ければプロパティの探りを投げない**
   (無駄な顧客 DB へのクエリを打たない)
6. **形が 1 つも無いことを「乖離が無い」と混ぜない**
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.scan import ScanRepository
from ontology_api.routers.scan import ScanSourceRegister, register_scan_source, run_scan
from ontology_api.routers.vkg import VkgMappingRevise, measure_source_divergence, revise_vkg_mapping
from ontology_api.services.divergence import (
    DivergenceUnavailableError,
    divergence_messages,
    measure_divergence,
)
from ontology_api.services.projection import ProjectionService
from ontology_core.auth.entra import Principal
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.config import AuthMode, Settings
from ontology_core.divergence import DivergenceStatus
from ontology_core.models import NamespaceRole, PlatformRole
from ontology_core.sparql.client import SparqlStore
from ontology_core.vkg import VirtualGraphClient

_NS = "div-ns"
_BASE_IRI = "https://e.example/div#"
_DATA_PREFIX = "https://e.example/div/data/"
_SOURCE = "self"
_ENDPOINT = "http://ontop.internal:8080/sparql"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_OWNER = Principal(subject="owner", object_id="owner-oid")
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")

_HOST = os.environ.get("POSTGRES_HOST", "localhost")
_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
_DATABASE = os.environ.get("POSTGRES_DATABASE", "ontology")
_USER = os.environ.get("POSTGRES_USER", "ontology")
_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "localdev")

#: 承認する版。**`Namespace` と `Orphan` の 2 つに形を付ける。**
#: マッピングは `Namespace` だけを作るので、`Orphan` は `unmapped` になる。
_ONTOLOGY = (
    f"@prefix d: <{_BASE_IRI}> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
    "d:Namespace a owl:Class .\n"
    "d:Orphan a owl:Class .\n"
    "d:displayName a owl:DatatypeProperty .\n"
    "d:baseIri a owl:DatatypeProperty .\n"
    "d:NamespaceShape a sh:NodeShape ;\n"
    "  sh:targetClass d:Namespace ;\n"
    "  sh:property [ sh:path d:displayName ; sh:minCount 1 ] ;\n"
    "  sh:property [ sh:path d:baseIri ; sh:minCount 1 ] .\n"
    "d:OrphanShape a sh:NodeShape ;\n"
    "  sh:targetClass d:Orphan ;\n"
    "  sh:property [ sh:path d:displayName ; sh:minCount 1 ] .\n"
)

#: 形を 1 つも持たない版。
_NO_SHAPES = (
    f"@prefix d: <{_BASE_IRI}> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "d:Namespace a owl:Class .\n"
    "d:displayName a owl:DatatypeProperty .\n"
)

#: マッピング。`d:Namespace` と `d:displayName` だけを作る。
#: **`d:baseIri` は作らない** — 述語が `unmapped` になる経路を通すため。
_MAPPING = (
    "@prefix rr: <http://www.w3.org/ns/r2rml#> .\n"
    f"@prefix d: <{_BASE_IRI}> .\n"
    "<#M> a rr:TriplesMap ;\n"
    '  rr:logicalTable [ rr:tableName "\\"public\\".\\"namespaces\\"" ] ;\n'
    "  rr:subjectMap [\n"
    f'    rr:template "{_DATA_PREFIX}namespace/{{name}}" ;\n'
    "    rr:class d:Namespace\n"
    "  ] ;\n"
    "  rr:predicateObjectMap [\n"
    "    rr:predicate d:displayName ;\n"
    '    rr:objectMap [ rr:column "display_name" ]\n'
    "  ] .\n"
)


def _settings(**kwargs: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        AUTH_MODE=AuthMode.DISABLED,
        SCAN_ALLOWED_HOSTS=_HOST,
        **kwargs,
    )


async def _local_password(source: Any, settings: Settings) -> str | None:
    return _PASSWORD


class _NullStore(SparqlStore):
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


class _ReadFails(OntologyBlobStore):
    """`get_version` だけを失敗させる代役。"""

    def __init__(self, real: OntologyBlobStore) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    async def get_version(self, blob_path: str) -> str:
        raise BlobStoreError("Blob を読めませんでした(検証用)")


def _ask_response(answer: bool) -> httpx.Response:
    return httpx.Response(200, json={"head": {}, "boolean": answer})


def _count_response(value: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "head": {"vars": ["missing"]},
            "results": {"bindings": [{"missing": {"type": "literal", "value": str(value)}}]},
        },
    )


class _Recorder:
    """探りを記録しつつ、決めた答えを返す代役。

    **投げた探りを数えられるようにしてある。** 「クラスに実データが
    無ければプロパティの探りを投げない」を確かめるため。
    """

    def __init__(self, *, has_data: bool = True, missing: dict[str, int] | None = None) -> None:
        self.queries: list[str] = []
        self._has_data = has_data
        self._missing = missing or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = request.content.decode("utf-8")
        self.queries.append(query)
        if query.startswith("ASK"):
            return _ask_response(self._has_data)
        for path, value in self._missing.items():
            if path in query:
                return _count_response(value)
        return _count_response(0)

    def client(self) -> VirtualGraphClient:
        return VirtualGraphClient(client=httpx.AsyncClient(transport=httpx.MockTransport(self)))


class _Unreachable:
    """常に到達できない代役。"""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    def client(self) -> VirtualGraphClient:
        return VirtualGraphClient(client=httpx.AsyncClient(transport=httpx.MockTransport(self)))


async def _setup(session: AsyncSession) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri=_BASE_IRI,
        created_by=_ADMIN.object_id,
        require_two_person_approval=False,
    )
    roles = RoleRepository(session)
    for principal, role in ((_OWNER, NamespaceRole.OWNER), (_ANALYST, NamespaceRole.DATA_ANALYST)):
        await roles.grant(
            namespace=_NS,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await session.commit()


async def _register_and_scan(session: AsyncSession) -> int:
    await register_scan_source(
        namespace=_NS,
        payload=ScanSourceRegister(
            name=_SOURCE,
            driver="postgresql",
            host=_HOST,
            port=_PORT,
            database=_DATABASE,
            username=_USER,
            auth_mode="entra",
        ),
        principal=_OWNER,
        session=session,
        settings=_settings(),
    )
    await run_scan(
        namespace=_NS,
        name=_SOURCE,
        principal=_OWNER,
        session=session,
        settings=_settings(),
        secret_resolver=_local_password,
    )
    row = await ScanRepository(session).get_source(namespace=_NS, name=_SOURCE)
    assert row is not None
    return row.id


async def _approve(
    session: AsyncSession, blob: OntologyBlobStore, *, turtle: str = _ONTOLOGY
) -> None:
    svc = ProjectionService(
        session=session, blob=blob, store=_NullStore(), graph_iri_base="urn:ontology:graph"
    )
    await svc.publish(namespace=_NS, turtle=turtle, actor=_OWNER.object_id, version="1.0.0")
    await svc.submit(namespace=_NS, version="1.0.0", actor=_OWNER.object_id)
    await svc.approve(namespace=_NS, version="1.0.0", actor=_OWNER.object_id)
    await session.commit()


async def _register_mapping(session: AsyncSession, blob: OntologyBlobStore) -> None:
    await revise_vkg_mapping(
        namespace=_NS,
        source=_SOURCE,
        payload=VkgMappingRevise(content=_MAPPING, reason="乖離の測定のため"),
        principal=_OWNER,
        session=session,
        blob=blob,
    )


async def _measure(
    session: AsyncSession,
    blob: OntologyBlobStore,
    *,
    source_id: int,
    recorder: _Recorder | _Unreachable,
    limit: int = 100,
) -> Any:
    async with recorder.client() as client:
        return await measure_divergence(
            session,
            blob=blob,
            client=client,
            endpoint=_ENDPOINT,
            namespace=_NS,
            source=_SOURCE,
            source_id=source_id,
            limit=limit,
        )


# ------------------------------------- 測れないときは断る(決定6)


@pytest.mark.integration
async def test_承認済み版が無ければ測らない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**空の報告を返さない。** 測る基準が無い。"""
    await _setup(session)
    source_id = await _register_and_scan(session)

    with pytest.raises(DivergenceUnavailableError, match="承認済みの版がありません"):
        await _measure(session, blob_store, source_id=source_id, recorder=_Recorder())


@pytest.mark.integration
async def test_マッピングが無ければ測らない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**空の報告を返さない。** 実データへの入口が無い。"""
    await _setup(session)
    source_id = await _register_and_scan(session)
    await _approve(session, blob_store)

    with pytest.raises(DivergenceUnavailableError, match="マッピングが登録されていません"):
        await _measure(session, blob_store, source_id=source_id, recorder=_Recorder())


@pytest.mark.integration
async def test_正本が読めなければ測らない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**「読めなかった」を「形が無かった」にしない。**"""
    await _setup(session)
    source_id = await _register_and_scan(session)
    await _approve(session, blob_store)
    await _register_mapping(session, blob_store)

    with pytest.raises(DivergenceUnavailableError, match="読めませんでした"):
        await _measure(session, _ReadFails(blob_store), source_id=source_id, recorder=_Recorder())


@pytest.mark.integration
async def test_形が無い版は_no_shapes_で返る_乖離なしとは別物(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**「形が無かった」を「乖離が無かった」と混ぜない**(決定6)。"""
    await _setup(session)
    source_id = await _register_and_scan(session)
    await _approve(session, blob_store, turtle=_NO_SHAPES)
    await _register_mapping(session, blob_store)

    recorder = _Recorder()
    report = await _measure(session, blob_store, source_id=source_id, recorder=recorder)
    assert report.no_shapes is True
    assert report.classes == ()
    assert report.diverged == ()
    # **探りを 1 本も投げない。** 調べるものが無い。
    assert recorder.queries == []


# ------------------------------------- 4 状態(決定2・3)


@pytest.mark.integration
async def test_実データがあれば_matched_で欠けている件数も出る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    source_id = await _register_and_scan(session)
    await _approve(session, blob_store)
    await _register_mapping(session, blob_store)

    recorder = _Recorder(has_data=True, missing={"displayName": 2})
    report = await _measure(session, blob_store, source_id=source_id, recorder=recorder)

    by_class = {item.target_class: item for item in report.classes}
    namespace = by_class[f"{_BASE_IRI}Namespace"]
    assert namespace.status is DivergenceStatus.MATCHED
    props = {p.path: p for p in namespace.properties}
    assert props[f"{_BASE_IRI}displayName"].missing_count == 2
    assert props[f"{_BASE_IRI}displayName"].status is DivergenceStatus.EMPTY
    # **マッピングが作っていない述語は `unmapped`** で、件数は出さない。
    assert props[f"{_BASE_IRI}baseIri"].status is DivergenceStatus.UNMAPPED
    assert props[f"{_BASE_IRI}baseIri"].missing_count is None
    assert report.version == "1.0.0"
    assert report.mapping_revision == 1


@pytest.mark.integration
async def test_マッピングが作っていないクラスは_unmapped_で乖離に数えない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**乖離ではない。** 直すのはマッピングであって定義ではない。"""
    await _setup(session)
    source_id = await _register_and_scan(session)
    await _approve(session, blob_store)
    await _register_mapping(session, blob_store)

    recorder = _Recorder(has_data=True)
    report = await _measure(session, blob_store, source_id=source_id, recorder=recorder)

    orphan = next(i for i in report.classes if i.target_class == f"{_BASE_IRI}Orphan")
    assert orphan.status is DivergenceStatus.UNMAPPED
    assert orphan not in report.diverged
    # **探りを投げていない。** 0 件が返るだけで意味が無い。
    assert not any(f"{_BASE_IRI}Orphan" in q for q in recorder.queries)


@pytest.mark.integration
async def test_マッピングはあるのに_0_件なら_empty(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**これが乖離である。**"""
    await _setup(session)
    source_id = await _register_and_scan(session)
    await _approve(session, blob_store)
    await _register_mapping(session, blob_store)

    report = await _measure(
        session, blob_store, source_id=source_id, recorder=_Recorder(has_data=False)
    )
    namespace = next(i for i in report.classes if i.target_class == f"{_BASE_IRI}Namespace")
    assert namespace.status is DivergenceStatus.EMPTY
    assert namespace in report.diverged


@pytest.mark.integration
async def test_クラスに実データが無ければプロパティの探りを投げない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**無駄な顧客 DB へのクエリを打たない**(決定5)。

    投げても意味のある答えにならない。
    """
    await _setup(session)
    source_id = await _register_and_scan(session)
    await _approve(session, blob_store)
    await _register_mapping(session, blob_store)

    recorder = _Recorder(has_data=False)
    report = await _measure(session, blob_store, source_id=source_id, recorder=recorder)

    assert all(q.startswith("ASK") for q in recorder.queries), recorder.queries
    # **投げなかった分は `issued_probes` から引く。**
    assert report.issued_probes == len(recorder.queries)


@pytest.mark.integration
async def test_仮想グラフに聞けなければ_unknown_で_conclusive_が偽(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`empty` にしない**(決定2)。「実データが無い」と「聞けなかった」は違う。"""
    await _setup(session)
    source_id = await _register_and_scan(session)
    await _approve(session, blob_store)
    await _register_mapping(session, blob_store)

    report = await _measure(session, blob_store, source_id=source_id, recorder=_Unreachable())
    namespace = next(i for i in report.classes if i.target_class == f"{_BASE_IRI}Namespace")
    assert namespace.status is DivergenceStatus.UNKNOWN
    assert namespace.note
    assert namespace not in report.diverged
    assert report.conclusive is False


# ------------------------------------- 上限(決定5)


@pytest.mark.integration
async def test_上限に達した形も報告に入り_conclusive_が偽になる(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**欠落させると「載っていないものは問題なし」と読まれる。**"""
    await _setup(session)
    source_id = await _register_and_scan(session)
    await _approve(session, blob_store)
    await _register_mapping(session, blob_store)

    report = await _measure(session, blob_store, source_id=source_id, recorder=_Recorder(), limit=0)
    # **形は 2 つとも報告に入る。** 1 つは `unmapped`、1 つは上限で `unknown`。
    assert len(report.classes) == 2
    assert report.skipped_probes > 0
    assert report.conclusive is False
    unknown = next(i for i in report.classes if i.status is DivergenceStatus.UNKNOWN)
    assert "上限" in unknown.note


# ------------------------------------- 報告の文章


@pytest.mark.integration
async def test_調べられなかったことも文章に出す(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**乖離だけ並べると、調べられなかったものが「問題なし」に見える。**"""
    await _setup(session)
    source_id = await _register_and_scan(session)
    await _approve(session, blob_store)
    await _register_mapping(session, blob_store)

    report = await _measure(session, blob_store, source_id=source_id, recorder=_Unreachable())
    lines = divergence_messages(report)
    assert any("調べられませんでした" in line for line in lines)


# ------------------------------------- ルータ


@pytest.mark.integration
async def test_エンドポイントが未設定なら_503(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**空の報告を返さない**(ADR-0046 決定11 と同じ形)。"""
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)
    await _register_mapping(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await measure_source_divergence(
            namespace=_NS,
            source=_SOURCE,
            principal=_ANALYST,
            session=session,
            settings=_settings(),
            blob=blob_store,
        )
    assert caught.value.status_code == 503


@pytest.mark.integration
async def test_測れないときは_404(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    """承認済み版が無い状態。**空の報告ではなく 404 で断る。**"""
    await _setup(session)
    await _register_and_scan(session)

    with pytest.raises(HTTPException) as caught:
        await measure_source_divergence(
            namespace=_NS,
            source=_SOURCE,
            principal=_ANALYST,
            session=session,
            settings=_settings(VKG_ENDPOINT_TEMPLATE=_ENDPOINT),
            blob=blob_store,
        )
    assert caught.value.status_code == 404


@pytest.mark.integration
async def test_権限の無い主体は測れない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    await _register_and_scan(session)

    with pytest.raises(HTTPException) as caught:
        await measure_source_divergence(
            namespace=_NS,
            source=_SOURCE,
            principal=Principal(subject="x", object_id="x-oid"),
            session=session,
            settings=_settings(VKG_ENDPOINT_TEMPLATE=_ENDPOINT),
            blob=blob_store,
        )
    assert caught.value.status_code == 403
