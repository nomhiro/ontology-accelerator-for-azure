"""テスト用のフィクスチャ。

integration マークのテストは実際の PostgreSQL / Azurite を使う。ローカルでは
`just up` で立てた compose の PostgreSQL と Azurite を、CI では services の
それらを使う。**スキップはしない** — 接続できなければ失敗させる。
静かにスキップされたテストは、通っているように見えて何も検証しない。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from azure.storage.blob.aio import BlobServiceClient
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.blob import OntologyBlobStore
from ontology_core.config import AuthMode, Settings
from ontology_core.db import Base, create_engine_and_factory


def _test_settings() -> Settings:
    return Settings(
        POSTGRES_HOST=os.environ.get("POSTGRES_HOST", "localhost"),
        POSTGRES_PORT=int(os.environ.get("POSTGRES_PORT", "5432")),
        POSTGRES_DATABASE=os.environ.get("POSTGRES_DATABASE", "ontology"),
        POSTGRES_USER=os.environ.get("POSTGRES_USER", "ontology"),
        POSTGRES_PASSWORD=os.environ.get("POSTGRES_PASSWORD", "localdev"),
        AUTH_MODE=AuthMode.DISABLED,
    )


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """テーブルを作り直したまっさらな DB のセッションを返す。"""
    engine, factory = create_engine_and_factory(_test_settings())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def settings() -> Settings:
    return _test_settings()


# Azurite の既定アカウント。キーは Azurite が公開している固定値で、秘密ではない。
#
# ポートは docker-compose.yml の AZURITE_PORT と揃える。10000 番を別のプロジェクトで
# 使っている場合に備えて環境変数から読む(Fuseki の FUSEKI_PORT と同じ扱い)。
_PORT = os.environ.get("AZURITE_PORT", "10000")
_CONN = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    f"BlobEndpoint=http://localhost:{_PORT}/devstoreaccount1;"
)


@pytest_asyncio.fixture
async def blob_store() -> AsyncIterator[OntologyBlobStore]:
    """Azurite に対する `OntologyBlobStore`。Task 6 の test_projection.py も使う。"""
    service = BlobServiceClient.from_connection_string(_CONN)
    container = "ontologies-test"
    try:
        await service.create_container(container)
    except Exception:  # 既存なら無視
        pass

    # 前のテストが残した Blob をすべて削除する。コンテナごと削除して作り直すと、
    # Azurite では削除直後の再作成が「コンテナ削除中」エラーになりテストが不安定になる
    # ため、コンテナは残したまま中身だけ空にする(コンテナ再作成に戻さないこと)。
    cc = service.get_container_client(container)
    async for blob in cc.list_blobs():
        await cc.delete_blob(blob.name)

    store = OntologyBlobStore.from_client(service, container=container, prefix="versions/")
    yield store
    await store.aclose()
    # `OntologyBlobStore.aclose()` は渡されたコンテナクライアントだけを閉じる
    # (`from_client` で外から渡されたクライアントの所有権を持たない設計のため)。
    # ここで作った `service`(BlobServiceClient)はフィクスチャ側で閉じる。
    await service.close()
