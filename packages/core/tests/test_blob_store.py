"""OntologyBlobStore の Azure SDK 例外の扱い。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from azure.core.exceptions import AzureError
from azure.storage.blob.aio import BlobServiceClient

from ontology_core.blob import BlobStoreError, OntologyBlobStore

pytestmark = pytest.mark.integration

# Azurite の既定アカウント。キーは Azurite が公開している固定値で、秘密ではない。
# ポートは docker-compose.yml の AZURITE_PORT と揃える(packages/api/tests/conftest.py と同じ扱い)。
_PORT = os.environ.get("AZURITE_PORT", "10000")
_CONN = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    f"BlobEndpoint=http://localhost:{_PORT}/devstoreaccount1;"
)
_CONTAINER = "ontologies-core-test"


@pytest_asyncio.fixture
async def blob_store() -> AsyncIterator[OntologyBlobStore]:
    """コンテナは実在するが、対象の Blob は無い状態を作る。"""
    service = BlobServiceClient.from_connection_string(_CONN)
    try:
        await service.create_container(_CONTAINER)
    except Exception:  # 既存なら無視
        pass

    store = OntologyBlobStore.from_client(service, container=_CONTAINER, prefix="approved/")
    yield store
    await store.aclose()
    await service.close()


async def test_missing_blob_raises_blob_store_error(blob_store: OntologyBlobStore) -> None:
    """存在しない Blob を読むと azure の例外ではなく BlobStoreError になること。

    呼び出し側(ProjectionService.reconcile)は「Blob の問題は BlobStoreError」という
    契約に依拠して per-item の失敗として続行するかどうかを判断する。
    azure.core.exceptions の例外が生のまま漏れると、その判断が壊れる。
    """
    with pytest.raises(BlobStoreError):
        await blob_store.get_version("approved/does-not-exist/9.9.9.ttl")


async def test_wrapped_error_is_not_a_bare_azure_error(blob_store: OntologyBlobStore) -> None:
    """BlobStoreError 以外(生の azure 例外)は外に漏れないこと。"""
    with pytest.raises(BlobStoreError):
        try:
            await blob_store.get_version("approved/does-not-exist/9.9.9.ttl")
        except AzureError as exc:
            pytest.fail(f"azure の例外が生のまま漏れた: {exc!r}")
