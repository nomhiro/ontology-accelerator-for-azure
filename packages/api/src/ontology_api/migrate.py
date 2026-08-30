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

# ロック待ちの上限(秒)。`infra/modules/api.bicep` の startupProbe は
# initialDelaySeconds(10) + periodSeconds(10) * failureThreshold(30) = 310秒を
# 予算として持つ(fuseki.bicep の Startup probe と同等)。この予算を超えて
# Container Apps に kill されると、実行中の transactional DDL がロールバックし、
# 再起動して同じ場所で再び kill される = そのマイグレーションが永久に適用できない
# 状態に陥る。ロック待ちをこの予算より十分短く区切っておけば、待ちきれない場合は
# probe に kill される前に自分から非ゼロ終了でき、実際のマイグレーション実行(通常は
# 数秒〜数十秒)にも probe 予算の残りを残せる。310秒の予算に対し余白を70秒ほど
# 残す 240秒(4分)とする。
_LOCK_TIMEOUT_SECONDS = 240

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
            # PostgreSQL の SET はプリペアドステートメントのパラメータバインドを
            # 受け付けない(値はリテラルでなければならない)。ここは定数
            # `_LOCK_TIMEOUT_SECONDS` を埋め込むだけでユーザー入力は関与しないため、
            # f-string での組み立てはインジェクションのリスクにならない。
            await conn.execute(text(f"SET lock_timeout = '{_LOCK_TIMEOUT_SECONDS}s'"))
            logger.info(
                "マイグレーション用アドバイザリロック(id=%s, lock_timeout=%s秒)を取得します",
                _MIGRATION_LOCK_ID,
                _LOCK_TIMEOUT_SECONDS,
            )
            await conn.execute(
                text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": _MIGRATION_LOCK_ID}
            )
            # `pg_advisory_lock` はセッションスコープのロックであり、`COMMIT` しても
            # 解放されない(トランザクションスコープのロックが欲しい場合は
            # `pg_advisory_xact_lock` を使う)。したがってここで commit してもロックは
            # 維持されたまま、接続の状態だけが `idle in transaction` から `idle` に変わる。
            # `idle_in_transaction_session_timeout` はコネクションが `idle in transaction`
            # のときにしか発火しないため、commit しておくことでこの設定の対象から外れる。
            # Azure Database for PostgreSQL の既定値は 0(無効)だが、運用者が設定すると、
            # `alembic upgrade head` の実行中(ロックを保持したままの接続は何も
            # クエリを発行していないので `idle in transaction` に見える)にこの接続が
            # タイムアウトで強制切断され、ロックが失われて直列化が破れる
            # (実測: `ALTER DATABASE ontology SET idle_in_transaction_session_timeout = '2s'`
            # の下で、5秒後にロックが消え、別レプリカが同じロックを取得できることを確認済み)。
            await conn.commit()
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
