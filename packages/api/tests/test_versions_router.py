"""バージョン投入ルーターの例外マッピング。

M-1: `_next_version` が自動採番できない版に遭遇したときに `AutoVersionError` が
`ProjectionService.publish` から抜けて `routers/versions.py` まで伝播し、
未捕捉の 500 ではなく 422 に変換されることを確認する。サービス層の挙動は
`test_projection.py` で確認済みなので、ここではルーターの例外マッピングだけを見る。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.routers.versions import (
    PublishRequest,
    RejectRequest,
    approve_version,
    publish_version,
    reject_version,
    submit_version,
)
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.sparql.client import SparqlStore

pytestmark = pytest.mark.integration

TTL = "@prefix ex: <https://e.example/#> .\nex:A a ex:Class .\n"
# 述語だけで終わる。rdflib は BadSyntax ではなく IndexError を投げる経路
# (packages/core/tests/test_turtle.py で実測済み)。P1-C2 のブリーフの例そのもの。
BROKEN_TTL = "@prefix ex: <http://e/> . ex:A a"
_PRINCIPAL = Principal.local_dev()


class _NullStore(SparqlStore):
    """射影は成功したことにするだけの最小フェイク。この経路には未到達のはず。"""

    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        return {"results": {"bindings": []}}

    async def update(self, sparql: str, *, dataset: str) -> None:
        return None

    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None:
        return None

    async def put_default_graph(self, turtle: str, *, dataset: str) -> None:
        return None

    async def delete_graph(self, graph_iri: str, *, dataset: str) -> None:
        return None

    async def list_datasets(self) -> list[str]:
        return []

    async def create_dataset(self, dataset: str) -> None:
        return None

    async def delete_dataset(self, dataset: str) -> None:
        return None


async def test_publish_version_maps_auto_version_error_to_422(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    name = "ver-422"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()

    store = _NullStore()
    # 明示バージョン(英字を含む)での publish は成功する。
    await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=TTL, version="1.beta.0"),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )

    # 以後 version 省略の publish は 500 ではなく 422 になる。
    with pytest.raises(HTTPException) as exc_info:
        await publish_version(
            namespace=name,
            payload=PublishRequest(turtle=TTL + "\nex:B a ex:Class .\n", version=None),
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )

    assert exc_info.value.status_code == 422
    assert "1.beta.0" in str(exc_info.value.detail)


async def test_publish_version_maps_turtle_syntax_error_to_422(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """P1-C2: 構文が壊れた TTL の publish は 422 になり、Blob にも PostgreSQL にも
    何も残らない。

    「422 を返す」だけでは、Blob に書いた後で検証している実装でも通ってしまう
    ため、サービス層(test_projection.py)と同じく Blob・PostgreSQL の状態も
    ここで明示的に確認する。
    """
    from ontology_api.repositories.versions import VersionRepository

    name = "ver-ttl-422"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()
    store = _NullStore()

    with pytest.raises(HTTPException) as exc_info:
        await publish_version(
            namespace=name,
            payload=PublishRequest(turtle=BROKEN_TTL),
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )

    assert exc_info.value.status_code == 422
    assert await blob_store.list_versions(name) == []
    assert await VersionRepository(session).list_for(name) == []


async def test_submit_approve_reject_router_status_codes(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """必須テスト4: 不正な遷移が 409、存在しない版が 404、reject の空 reason が 422。"""
    name = "ver-approval"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()
    store = _NullStore()

    # 存在しない版への submit は 404。
    with pytest.raises(HTTPException) as exc_info:
        await submit_version(
            namespace=name,
            version="9.9.9",
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 404

    published = await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=TTL, version="1.0.0"),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )

    # draft を approve しようとすると 409。
    with pytest.raises(HTTPException) as exc_info:
        await approve_version(
            namespace=name,
            version=published.version,
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 409

    submitted = await submit_version(
        namespace=name,
        version=published.version,
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    assert submitted.status.value == "in-review"

    # in-review を再度 submit しようとすると 409。
    with pytest.raises(HTTPException) as exc_info:
        await submit_version(
            namespace=name,
            version=published.version,
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 409

    approved = await approve_version(
        namespace=name,
        version=published.version,
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    assert approved.status.value == "approved"
    assert approved.approved_by == (_PRINCIPAL.object_id or _PRINCIPAL.subject)

    # approved を submit しようとすると 409。
    with pytest.raises(HTTPException) as exc_info:
        await submit_version(
            namespace=name,
            version=published.version,
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 409


async def test_reject_empty_reason_is_422(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """RejectRequest.reason は空文字を拒否する(pydantic の入口検証で 422)。"""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        RejectRequest(reason="")


async def test_reject_unknown_version_is_404(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    name = "ver-reject-404"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()
    store = _NullStore()

    with pytest.raises(HTTPException) as exc_info:
        await reject_version(
            namespace=name,
            version="9.9.9",
            payload=RejectRequest(reason="無効な版"),
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 404
