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
from ontology_api.routers.versions import PublishRequest, publish_version
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.sparql.client import SparqlStore

pytestmark = pytest.mark.integration

TTL = "@prefix ex: <https://e.example/#> .\nex:A a ex:Class .\n"
_PRINCIPAL = Principal.local_dev()


class _NullStore(SparqlStore):
    """射影は成功したことにするだけの最小フェイク。この経路には未到達のはず。"""

    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        return {"results": {"bindings": []}}

    async def update(self, sparql: str, *, dataset: str) -> None:
        return None

    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None:
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
