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

    store = OntologyBlobStore.from_client(service, container=_CONTAINER, prefix="versions/")
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
        await blob_store.get_version("versions/does-not-exist/9.9.9.ttl")


async def test_wrapped_error_is_not_a_bare_azure_error(blob_store: OntologyBlobStore) -> None:
    """BlobStoreError 以外(生の azure 例外)は外に漏れないこと。"""
    with pytest.raises(BlobStoreError):
        try:
            await blob_store.get_version("versions/does-not-exist/9.9.9.ttl")
        except AzureError as exc:
            pytest.fail(f"azure の例外が生のまま漏れた: {exc!r}")


async def test_from_connection_string_owns_the_service_it_creates() -> None:
    """final-fix-brief.md 修正1: `from_connection_string` は `from_account_url` と
    同様に `BlobServiceClient` を自分で作るため、`_owned_service` を設定して
    所有権を持つこと。

    `from_client` は所有権を持たない設計(呼び出し側が渡したクライアントを使う
    だけ)なのでそちらを流用すると閉じ漏れる。過去に `from_account_url` で
    まさにこの欠陥が出た(`ContainerClient.close()` が意図的な no-op のため、
    `_owned_service` を保持しない実装では `aclose()` が何も閉じていなかった)。
    """
    service = BlobServiceClient.from_connection_string(_CONN)
    try:
        await service.create_container(_CONTAINER)
    except Exception:  # 既存なら無視
        pass
    await service.close()

    store = OntologyBlobStore.from_connection_string(
        _CONN, container=_CONTAINER, prefix="versions/"
    )
    assert store._owned_service is not None


async def test_from_connection_string_aclose_closes_the_owned_service() -> None:
    """`_owned_service` を保持するだけでなく、`aclose()` が実際にそれを閉じて
    aiohttp のセッションを解放することを確認する回帰テスト。

    未使用のまま `aclose()` すると transport のセッションが最初から開かれておらず
    (遅延生成)何も検証できないため、先に実際のリクエストを1回送って開かせる。
    """
    store = OntologyBlobStore.from_connection_string(
        _CONN, container=_CONTAINER, prefix="versions/"
    )
    owned_service = store._owned_service
    assert owned_service is not None

    await store.list_versions()  # transport のセッションを実際に開かせる

    transport = owned_service._client._client._pipeline._transport
    assert transport.session is not None
    assert not transport.session.closed

    await store.aclose()

    assert transport.session is None
