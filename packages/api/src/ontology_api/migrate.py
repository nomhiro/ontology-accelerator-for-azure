"""API コンテナ起動時に DB マイグレーションを適用する。

**実測で判明した問題**: `alembic upgrade head` はマイグレーション自体を同時実行に対して
アトミックにしない。複数レプリカが同時に起動して並行実行すると、`alembic_version`
テーブルの作成が衝突し、片方が
`UniqueViolationError: duplicate key value violates unique constraint
"pg_type_typname_nsp_index"` で失敗する(2 プロセス同時実行で再現済み)。
Container Apps の API は `maxReplicas: 3` のため複数レプリカの同時起動が起こり得る。

**対策**: PostgreSQL のアドバイザリロック(`pg_advisory_lock`)で直列化してから
`alembic upgrade head` を実行する(2 プロセス同時実行で両方 exit=0 になることを
実測で確認済み)。シェルから `psql` を呼ぶ方式は取らない(コンテナに psql を
追加導入する必要が生じるため)。ロックの取得・解放は SQLAlchemy 経由でこの
モジュールが行う。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from ontology_core.config import get_settings
from ontology_core.db import create_engine_and_factory

logger = logging.getLogger(__name__)
# alembic.ini の [logger_root] は level = WARNING で、`_run_alembic_upgrade` 内で
# 呼ばれる `fileConfig()` がこれを root ロガーに適用してしまう(`disable_existing_loggers`
# はロガーの無効化だけを防ぎ、level の上書きまでは防がない、実測で確認済み)。
# このロガー自身に明示的なレベルを設定しておけば、root の level が後から
# WARNING に変わっても実効レベルの決定でこちらが優先されるため、
# マイグレーション完了・ロック解放のログが消えない。
logger.setLevel(logging.INFO)

# このプロジェクト固有の任意の bigint 定数。pg_advisory_lock の第一引数(ロック ID)に
# 使う。値そのものに業務的な意味は無く、他プロジェクトのロック ID と衝突しなければよい。
_MIGRATION_LOCK_ID = 918273645

# packages/api/alembic.ini への絶対パス。uvicorn の起動時カレントディレクトリに
# 依存させないため、このファイルの位置から相対的に組み立てる。
# __file__ = packages/api/src/ontology_api/migrate.py なので、
# parents[2] が packages/api (= alembic.ini の置き場所) になる。
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _run_alembic_upgrade() -> None:
    """`alembic upgrade head` を実行する。

    Alembic の `env.py`(`run_migrations_online`)は内部で `asyncio.run()` を呼ぶ。
    この関数を呼び出し側(`_upgrade_with_lock`)と同じイベントループの上で
    直接 await すると "asyncio.run() cannot be called from a running event loop"
    になるため、呼び出し側は `asyncio.to_thread` でこの関数を別スレッドに投げる。
    別スレッドには実行中のイベントループが無いので、Alembic 側の `asyncio.run()`
    がそのスレッドで新しいイベントループを持てる。
    """
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "head")


async def _upgrade_with_lock() -> None:
    """アドバイザリロックを取得してから `alembic upgrade head` を実行する。

    ロックは `pg_advisory_lock`(セッションスコープ)を使う。同じ接続を
    保持し続けている間だけ他プロセスをブロックし、`pg_advisory_unlock` で
    明示的に解放する。Alembic 自体は別の接続で動く(`_run_alembic_upgrade` 側の
    `create_engine_and_factory` が独立したエンジンを作る)が、PostgreSQL の
    アドバイザリロックはロック ID が一致する限り接続をまたいで(= プロセスをまたいで)
    ブロックするため、直列化の効果は変わらない。
    """
    settings = get_settings()
    engine, _factory = create_engine_and_factory(settings)
    try:
        async with engine.connect() as conn:
            logger.info(
                "マイグレーション用アドバイザリロック(id=%s)を取得します", _MIGRATION_LOCK_ID
            )
            await conn.execute(
                text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": _MIGRATION_LOCK_ID}
            )
            try:
                logger.info("alembic upgrade head を実行します")
                await asyncio.to_thread(_run_alembic_upgrade)
                logger.info("alembic upgrade head が完了しました")
            finally:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": _MIGRATION_LOCK_ID}
                )
                logger.info("アドバイザリロックを解放しました")
    finally:
        await engine.dispose()


def main() -> None:
    """`python -m ontology_api.migrate` のエントリポイント。"""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_upgrade_with_lock())


if __name__ == "__main__":
    main()
