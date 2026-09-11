"""テスト用のフィクスチャ。

integration マークのテストは実際の PostgreSQL / Azurite を使う。ローカルでは
`just up` で立てた compose の PostgreSQL と Azurite を、CI では services の
それらを使う。**スキップはしない** — 接続できなければ失敗させる。
静かにスキップされたテストは、通っているように見えて何も検証しない。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from functools import cache
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
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


@cache
def _alembic_head() -> str:
    """マイグレーションの head リビジョンを返す(P1-23)。

    ハードコードするとマイグレーションを追加したときに黙って古い値を
    stamp することになるため、alembic のスクリプトから読む。`alembic.ini`
    は使わず script_location だけを渡す(ini を探す経路を増やさないため)。
    """
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None, "alembic の head リビジョンが取得できません"
    return head


@pytest.fixture
def alembic_head() -> str:
    """マイグレーションの head リビジョン(テストから参照するため)。"""
    return _alembic_head()


# ロック待ちの上限(`P2B-12`、ADR-0024)。
#
# **これが無いとスイート全体が固まる。** 前のテストがトランザクションを開いた
# まま終わると、次のテストの `drop_all` の `DROP TABLE` が**無期限に待つ**
# (実測: 行ロックの変異テストで publish のトランザクションが残り、次のテストの
# `drop_all` が返らなくなった)。タイムアウトを置くと「固まる」代わりに
# 「落ちる」ので、**原因が分かる**。
#
# 行ロックの検証(`test_delete_publish_race.py`)は 1 秒の待ちを観測するので、
# それより十分に長くする。
_LOCK_TIMEOUT = "15s"


async def _limit_lock_wait(s: AsyncSession) -> None:
    """そのセッションのロック待ちに上限を置く。"""
    await s.execute(sa.text(f"SET lock_timeout = '{_LOCK_TIMEOUT}'"))


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """テーブルを作り直したまっさらな DB のセッションを返す。"""
    engine, factory = create_engine_and_factory(_test_settings())
    async with engine.begin() as conn:
        await conn.execute(sa.text(f"SET lock_timeout = '{_LOCK_TIMEOUT}'"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # `alembic_version` を head で stamp する(P1-23)。
        #
        # **これが無いと、テストを実行した後の `just migrate` が
        # DuplicateTableError で失敗する。** テーブルは `create_all` で
        # 出来ているのに alembic は「まだ何も適用されていない」と判断し、
        # `CREATE TABLE` からやり直そうとするため。「テストを回してから
        # just migrate」という自然な順序で貢献者が踏む。
        #
        # `alembic_version` は `Base.metadata` に無いので `drop_all` の
        # 対象外である。ここで毎回 head に揃える(冪等)。
        await conn.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS alembic_version ("
                "  version_num VARCHAR(32) NOT NULL,"
                "  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
                ")"
            )
        )
        await conn.execute(sa.text("DELETE FROM alembic_version"))
        await conn.execute(
            sa.text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
            {"head": _alembic_head()},
        )
    async with factory() as s:
        await _limit_lock_wait(s)
        try:
            yield s
        finally:
            # **ロックを残したまま終わらせない。** 失敗したテストが開いた
            # トランザクションが残ると、次のテストの `drop_all` が待たされる。
            await s.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def other_session(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """`session` と同じ DB に対する**独立したトランザクション**(`P2B-12`)。

    **1 つのセッションでは行ロックが効いているかを確かめられない** — 同じ
    トランザクションは自分のロックを待たないので、`SELECT ... FOR UPDATE` を
    2 回呼んでも何も起きない。ADR-0024 の排他を検証するには 2 本の接続が要る。

    `session` に依存させているのは、テーブルの作り直しが先に済んでいる
    必要があるためである(このフィクスチャ自身は作り直さない)。
    """
    engine, factory = create_engine_and_factory(_test_settings())
    async with factory() as s:
        await _limit_lock_wait(s)
        try:
            yield s
        finally:
            await s.rollback()
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
