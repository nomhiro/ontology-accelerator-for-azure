"""テスト用のフィクスチャ。

integration マークのテストは実際の PostgreSQL を使う。ローカルでは
`just up` で立てた compose の PostgreSQL を、CI では services の
PostgreSQL を使う。**スキップはしない** — 接続できなければ失敗させる。
静かにスキップされたテストは、通っているように見えて何も検証しない。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

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
