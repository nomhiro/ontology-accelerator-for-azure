"""FastAPI の依存関係。

認証とストアアクセスの配線をここに集約する。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ontology_core.auth.entra import Principal, TokenVerificationError, TokenVerifier
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import AuthMode, Settings, get_settings
from ontology_core.db import create_engine_and_factory, session_scope
from ontology_core.sparql.client import FusekiStore, SparqlStore

if TYPE_CHECKING:
    from azure.identity.aio import DefaultAzureCredential

__all__ = ["BlobDep", "CurrentPrincipal", "SessionDep", "SettingsDep", "StoreDep"]

SettingsDep = Annotated[Settings, Depends(get_settings)]


@lru_cache(maxsize=1)
def _token_verifier() -> TokenVerifier:
    settings = get_settings()
    return TokenVerifier(
        tenant_id=settings.entra_tenant_id,
        audience=settings.entra_api_audience,
    )


async def current_principal(
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """`Authorization: Bearer <token>` を検証して主体を返す。"""
    if settings.auth_mode is AuthMode.DISABLED:
        return Principal.local_dev()

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer トークンが必要です",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return _token_verifier().verify(authorization.split(" ", 1)[1].strip())
    except TokenVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


async def sparql_store(settings: SettingsDep) -> AsyncIterator[SparqlStore]:
    """リクエストごとにストアクライアントを提供する。

    Fuseki の管理 API を使う操作のみ Basic 認証を渡す。クエリと更新は
    内部 ingress に閉じているため追加の資格情報を持たせない。
    """
    admin_auth = (
        (settings.fuseki_admin_user, settings.fuseki_admin_password)
        if settings.fuseki_admin_password
        else None
    )
    store = FusekiStore(
        query_endpoint=settings.sparql_query_endpoint,
        update_endpoint=settings.sparql_update_endpoint,
        gsp_endpoint=settings.sparql_gsp_endpoint,
        admin_endpoint=settings.fuseki_admin_endpoint,
        admin_auth=admin_auth,
        timeout_seconds=settings.sparql_query_timeout_seconds,
    )
    try:
        yield store
    finally:
        await store.aclose()


StoreDep = Annotated[SparqlStore, Depends(sparql_store)]


# モジュールレベルで1個だけ生成し、以降のリクエストすべてで再利用する(遅延生成)。
# `db/engine.py` の `_get_credential()` と同じ方針。`DefaultAzureCredential` の
# 生成自体が環境変数・IMDS・Azure CLI 等を順にプローブするブロッキング処理であり、
# 毎リクエスト生成するとプロセス内のトークンキャッシュも毎回捨てられコストが積む
# (ブランチ全体レビュー O-2)。`azure.identity.aio`(非同期版)なので
# `db/engine.py` 側(同期版 `azure.identity.DefaultAzureCredential`)とは型が違い
# 実装は共有できないが、同じ「モジュールレベルで1個・遅延生成」のパターンを踏襲する。
_blob_credential: DefaultAzureCredential | None = None


def _get_blob_credential() -> DefaultAzureCredential:
    """Blob 用の `DefaultAzureCredential`(非同期版)をプロセス内で1個だけ生成して返す。"""
    global _blob_credential
    if _blob_credential is None:
        from azure.identity.aio import DefaultAzureCredential

        _blob_credential = DefaultAzureCredential()
    return _blob_credential


async def blob_store(settings: SettingsDep) -> AsyncIterator[OntologyBlobStore]:
    """リクエストごとに正本 TTL の Blob クライアントを提供する。

    `azure_storage_connection_string`(ローカル専用、Azurite 向け)が設定されて
    いればそちらを優先する。`from_account_url` + `DefaultAzureCredential` では
    Azurite(HTTP・共有キー認証)に認証できず、ローカルで Blob 依存の経路
    (publish / versions / delete)を動かす手段が無くなるため(final-fix-brief.md
    修正1 / I-1)。デプロイ環境では接続文字列を設定しないので従来どおり
    `from_account_url` + マネージド ID の経路になる。

    いずれの生成元も `OntologyBlobStore` が `BlobServiceClient` の所有権を持つ
    (`aclose()` で閉じる)。`DefaultAzureCredential` は `_get_blob_credential()` が
    プロセス内で共有するものであり、このリクエストが生成したものではないため、
    ここでは閉じない(プロセスの生存期間中は保持する)。
    """
    if settings.azure_storage_connection_string:
        store = OntologyBlobStore.from_connection_string(
            settings.azure_storage_connection_string,
            container=settings.ontology_blob_container,
            prefix=settings.ontology_blob_prefix,
        )
    else:
        store = OntologyBlobStore.from_account_url(
            settings.azure_storage_account_url,
            container=settings.ontology_blob_container,
            prefix=settings.ontology_blob_prefix,
            credential=_get_blob_credential(),
        )
    try:
        yield store
    finally:
        await store.aclose()


BlobDep = Annotated[OntologyBlobStore, Depends(blob_store)]


@lru_cache(maxsize=1)
def _session_factory() -> async_sessionmaker[AsyncSession]:
    """プロセス内で 1 つのエンジンを共有する。"""
    _engine, factory = create_engine_and_factory(get_settings())
    return factory


async def db_session() -> AsyncIterator[AsyncSession]:
    async for session in session_scope(_session_factory()):
        yield session


SessionDep = Annotated[AsyncSession, Depends(db_session)]
