import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from ontology_core.config import get_settings
from ontology_core.db import Base, create_engine_and_factory

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
#
# disable_existing_loggers=False にする理由: 既定 (True) だと、このモジュールを
# import する前に作られたロガー(例: ontology_api.migrate が起動時に作るロガー)を
# 無効化してしまい、マイグレーション前後のログ("ロックを取得しました" 等)が
# 出力されなくなる(migrate.py からの呼び出しで実際に観測した)。
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


def get_url() -> str:
    """接続先 DSN を返す。alembic.ini の sqlalchemy.url は空にしておき、
    設定の正本である `Settings.async_postgres_dsn` をここから取得する。
    """
    return get_settings().async_postgres_dsn


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Engine を作り、接続をマイグレーションのコンテキストに結び付ける。

    DSN 文字列にはパスワードを含めない方針(アプリ本体と同じ)のため、
    URL だけを組み立てる `get_url()` ではなく、パスワード(または Entra
    トークン)の受け渡しロジックを持つ `create_engine_and_factory` を使う。
    """
    engine, _factory = create_engine_and_factory(get_settings())

    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
