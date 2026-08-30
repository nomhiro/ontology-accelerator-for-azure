"""async エンジンとセッションの生成。

パスワードが設定されていない場合は Entra ID のアクセストークンをパスワードとして
渡す(PostgreSQL Flexible Server のパスワードレス接続)。トークンは有効期限が
あるため、接続のたびに取得する。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ontology_core.config import Settings

if TYPE_CHECKING:
    from azure.identity import DefaultAzureCredential

__all__ = ["create_engine_and_factory", "session_scope"]

# Azure Database for PostgreSQL のトークン対象スコープ。
_POSTGRES_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"

# モジュールレベルで1個だけ生成し、以降の接続すべてで再利用する(遅延生成)。
# `DefaultAzureCredential` の生成自体が(環境変数・IMDS・Azure CLI 等を順に
# プローブする)ブロッキング処理であり、接続ごとに新規生成すると
# `pool_size=5, max_overflow=5` で最大10回分のコストが積む。
_credential: DefaultAzureCredential | None = None


def _get_credential() -> DefaultAzureCredential:
    """`DefaultAzureCredential` をモジュールレベルで1個だけ生成して返す。"""
    global _credential
    if _credential is None:
        from azure.identity import DefaultAzureCredential

        _credential = DefaultAzureCredential()
    return _credential


def _entra_password_provider() -> str:
    """Entra ID のアクセストークンを取得してパスワードとして返す。

    資格情報オブジェクト自体はモジュールレベルで1個を再利用するが、
    `get_token()` は接続のたびに呼ぶ(トークンの有効期限に対応するため)。
    `DefaultAzureCredential` は内部でトークンをキャッシュし、有効期限が近づくと
    自動的に再取得するので、生成を1回に抑えてもここを都度呼ぶことに問題は無い。
    """
    return _get_credential().get_token(_POSTGRES_SCOPE).token


def create_engine_and_factory(
    settings: Settings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """エンジンとセッションファクトリを作る。

    パスワードが空のときは接続ごとに Entra トークンを取得する。
    """
    connect_args: dict[str, Any] = {}
    if settings.postgres_password:
        connect_args["password"] = settings.postgres_password
    elif not settings.database_url:
        # DSN にパスワードを埋めず、接続時にトークンを渡す。
        connect_args["password"] = _entra_password_provider

    engine = create_async_engine(
        settings.async_postgres_dsn,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args=connect_args,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """1 リクエスト 1 トランザクション。例外時はロールバックする。"""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
