"""FastAPI の依存関係。

認証とストアアクセスの配線をここに集約する。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ontology_core.auth.entra import Principal, TokenVerificationError, TokenVerifier
from ontology_core.config import AuthMode, Settings, get_settings
from ontology_core.db import create_engine_and_factory, session_scope
from ontology_core.sparql.client import FusekiStore, SparqlStore

__all__ = ["CurrentPrincipal", "SessionDep", "SettingsDep", "StoreDep"]

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


@lru_cache(maxsize=1)
def _session_factory() -> async_sessionmaker[AsyncSession]:
    """プロセス内で 1 つのエンジンを共有する。"""
    _engine, factory = create_engine_and_factory(get_settings())
    return factory


async def db_session() -> AsyncIterator[AsyncSession]:
    async for session in session_scope(_session_factory()):
        yield session


SessionDep = Annotated[AsyncSession, Depends(db_session)]
