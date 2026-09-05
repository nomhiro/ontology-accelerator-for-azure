import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import text
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
            # ADR-0011 決定2・3: テーブルの所有者は `ontology_owner`(NOLOGIN)であり、
            # アプリのロール(UAMI)には所有権も DDL も無い。マイグレーションは
            # 運用者が Entra 管理者として接続し、`SET ROLE` で一時的に所有者に
            # なって実行する。`MIGRATION_ROLE` は `ontology_core.config.Settings` に
            # 加えていない — アプリの実行時設定ではなく、マイグレーション実行者
            # (運用者の postdeploy / `just migrate`)だけが使う環境変数のため。
            #
            # 未設定(ローカル開発。`ontology_owner` ロールが存在しない)なら
            # 何もしない。値は運用者が postdeploy で明示的に設定する定数であり、
            # 外部入力ではないため f-string での組み込みはインジェクションの
            # リスクにならない(`ontology_api.migrate` の `_LOCK_TIMEOUT_SECONDS`
            # と同じ考え方)。識別子として `"..."` で囲むのは、将来 `MIGRATION_ROLE`
            # に特殊文字を含む値が渡されても構文エラーにならないようにするため。
            migration_role = os.environ.get("MIGRATION_ROLE", "")
            if migration_role:
                quoted_role = '"' + migration_role.replace('"', '""') + '"'
                await connection.execute(text(f"SET ROLE {quoted_role}"))
                # `execute()` は AsyncConnection を暗黙に「トランザクション開始済み」
                # にする(autobegin)。ここで明示的に commit してその暗黙トランザクション
                # を終わらせておかないと、直後の Alembic の `begin_transaction()` が
                # 既存の開いたトランザクションの中で動くことになり、Alembic 側は
                # 成功ログを出す(内部的にはネストしたトランザクションのコミットが
                # 成功している)にもかかわらず、外側のトランザクションは一度も
                # commit されないまま `engine.connect()` の `async with` を抜けて
                # 暗黙に ROLLBACK され、**マイグレーションの DDL が丸ごと消える**
                # (実測で再現・確認済み: 追加前はテーブルが1つも作られなかった)。
                await connection.commit()
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
