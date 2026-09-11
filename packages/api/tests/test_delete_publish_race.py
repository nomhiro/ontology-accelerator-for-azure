"""名前空間の削除と公開の競合(`P2B-12`、ADR-0024)。

**2 本の独立したトランザクションを使う。** 1 つのセッションでは行ロックが
効いているかを確かめられない — 同じトランザクションは自分のロックを待たない。

## 何を証明したいか

`DELETE /namespaces/{name}` は「Blob に `.ttl` が 1 件でも残っていれば 409」で
守られている。**ローダは PostgreSQL を見ず Blob だけを見て再構築する**ので、
Blob を残して PostgreSQL の行だけ消すと、次のレプリカ再作成で名前空間が
復活してしまうためである。

その確認と行の削除の間に `publish` が入り込むと、**Blob に TTL があって
PostgreSQL には何も無い**状態ができる。ADR-0024 決定1 は両方の経路に同じ
行ロックを取らせてこれを閉じた。

## どう証明するか

**「待たされること」を timeout で観測する。** 一方がロックを保持している間、
他方の処理を `asyncio.wait_for` で短い時間だけ待ち、`TimeoutError` に
なることを確認する。ロックが無ければ待たされずに進むので、この検査は
落ちる(変異テストで確認済み)。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_api.routers.namespaces import delete_namespace
from ontology_api.routers.versions import PublishRequest, publish_version
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.models import NamespaceRole, PlatformRole
from ontology_core.sparql.client import SparqlStore

_NS = "race-ns"
_BASE = "https://e.example/#"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_OWNER = Principal(subject="owner", object_id="owner-oid")

_TTL = """
@prefix ex: <https://e.example/#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
ex:Product a owl:Class .
"""

# ロックが効いていれば必ず超える待ち時間。**短くしすぎない** — CI の遅い
# 環境で「ロックが無いのに待たされた」ように見えるのを避ける。
_BLOCKED_FOR = 1.0


class _NullStore(SparqlStore):
    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        return {"results": {"bindings": []}}

    async def update(self, sparql: str, *, dataset: str) -> None: ...
    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None: ...
    async def put_default_graph(self, turtle: str, *, dataset: str) -> None: ...
    async def delete_graph(self, graph_iri: str, *, dataset: str) -> None: ...
    async def list_graphs(self, dataset: str) -> list[str]:
        return []

    async def has_default_graph_content(self, dataset: str) -> bool:
        return True

    async def list_datasets(self) -> list[str]:
        return []

    async def create_dataset(self, dataset: str) -> None: ...
    async def delete_dataset(self, dataset: str) -> None: ...


async def _setup(session: AsyncSession) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri=_BASE,
        created_by=_ADMIN.object_id,
        require_two_person_approval=False,
    )
    await RoleRepository(session).grant(
        namespace=_NS,
        principal_id=_OWNER.object_id,
        role=NamespaceRole.OWNER,
        granted_by=_ADMIN.object_id,
    )
    await session.commit()


async def _publish(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await publish_version(
        namespace=_NS,
        payload=PublishRequest(version="1.0.0", turtle=_TTL),
        principal=_OWNER,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
        response=Response(),
    )
    await session.commit()


@asynccontextmanager
async def _cleanup(*sessions: AsyncSession) -> AsyncIterator[list[asyncio.Task[None]]]:
    """テストが**失敗しても**ロックを残さないようにする。

    **これが無いとスイート全体が固まる。** 行ロックを持ったまま、あるいは
    ロック待ちのタスクを残したままテストが失敗すると、次のテストの
    `drop_all` が待たされる(実測。conftest の `lock_timeout` はその
    「固まる」を「落ちる」に変えるための二重の保険である)。

    `yield` したリストにタスクを入れると、終了時に確実に片付ける。
    """
    tasks: list[asyncio.Task[None]] = []
    try:
        yield tasks
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for s in sessions:
            await s.rollback()


async def _delete(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    await delete_namespace(
        name=_NS,
        principal=_OWNER,
        session=session,
        store=_NullStore(),
        blob=blob_store,
    )


# --------------------------------------------------- 削除が先にロックを取る


@pytest.mark.integration
async def test_削除がロックを持つ間_publish_は待たされる(
    session: AsyncSession,
    other_session: AsyncSession,
    blob_store: OntologyBlobStore,
    settings: Settings,
) -> None:
    """**ロックが効いていることを「待たされること」で観測する**(ADR-0024 決定1)。

    削除側がロックを取ったまま commit していない間、publish は
    `SELECT ... FOR UPDATE` で待つ。
    """
    await _setup(session)
    async with _cleanup(session, other_session) as tasks:
        # 削除側がロックを取る(まだ commit しない)。
        assert await NamespaceRepository(session).get_locked(_NS) is not None

        task = asyncio.create_task(_publish(other_session, blob_store, settings))
        tasks.append(task)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=_BLOCKED_FOR)
        # **Blob には何も書かれていない。** publish は名前空間の存在確認で
        # 止まっているので、`put_version` に到達していない。
        assert await blob_store.list_versions(namespace=_NS) == []


@pytest.mark.integration
async def test_削除が終わった後の_publish_は_404_になり_Blob_を汚さない(
    session: AsyncSession,
    other_session: AsyncSession,
    blob_store: OntologyBlobStore,
    settings: Settings,
) -> None:
    """**これが競合の核心である。**

    削除がロックを持っている間 publish は待ち、削除が commit した後は
    **名前空間の行が無い**ので 404 になる(サービス層の
    `UnknownNamespaceError` をルータが 404 に変換する)。
    **Blob には `.ttl` が 1 件も書かれない** — だから復活しない。
    """
    await _setup(session)
    async with _cleanup(session, other_session) as tasks:
        assert await NamespaceRepository(session).get_locked(_NS) is not None

        task = asyncio.create_task(_publish(other_session, blob_store, settings))
        tasks.append(task)
        # publish がロック待ちに入るのを待つ。
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=_BLOCKED_FOR)

        # 削除を完了させる(Blob は空なので通る)。
        await _delete(session, blob_store)

        with pytest.raises(HTTPException) as exc:
            await task
        assert exc.value.status_code == 404

        assert await blob_store.list_versions(namespace=_NS) == []
        assert await NamespaceRepository(session).get(_NS) is None


# --------------------------------------------------- publish が先にロックを取る


@pytest.mark.integration
async def test_publish_がロックを持つ間_削除は待たされる(
    session: AsyncSession,
    other_session: AsyncSession,
    blob_store: OntologyBlobStore,
    settings: Settings,
) -> None:
    await _setup(session)
    async with _cleanup(session, other_session) as tasks:
        # publish 側がロックを取る(まだ commit しない)。
        assert await NamespaceRepository(other_session).get_locked(_NS) is not None

        task = asyncio.create_task(_delete(session, blob_store))
        tasks.append(task)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=_BLOCKED_FOR)


@pytest.mark.integration
async def test_publish_が終わった後の削除は_409_になる(
    session: AsyncSession,
    other_session: AsyncSession,
    blob_store: OntologyBlobStore,
    settings: Settings,
) -> None:
    """**逆順でも食い違いが残らない。**

    publish が Blob と PostgreSQL に書き終わってから削除がロックを取るので、
    削除は**その TTL を見て** 409 になる。
    """
    await _setup(session)
    await _publish(other_session, blob_store, settings)

    with pytest.raises(HTTPException) as exc:
        await _delete(session, blob_store)
    assert exc.value.status_code == 409
    # 名前空間も版も残っている。
    assert await NamespaceRepository(session).get(_NS) is not None
    assert len(await VersionRepository(session).list_for(_NS)) == 1


# ------------------------------------------------------------ 競合が無いとき


@pytest.mark.integration
async def test_競合が無ければ削除はそのまま通る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**ロックを入れたことで普通の削除が壊れていないこと。**"""
    await _setup(session)
    await _delete(session, blob_store)
    assert await NamespaceRepository(session).get(_NS) is None


@pytest.mark.integration
async def test_別の名前空間への_publish_は待たされない(
    session: AsyncSession,
    other_session: AsyncSession,
    blob_store: OntologyBlobStore,
    settings: Settings,
) -> None:
    """**ロックは名前空間ごとである。** 全体を直列化してはいけない。"""
    await _setup(session)
    other = "race-ns-2"
    await NamespaceRepository(session).create(
        name=other,
        display_name=other,
        description="",
        base_iri="https://e.example/other#",
        created_by=_ADMIN.object_id,
        require_two_person_approval=False,
    )
    await RoleRepository(session).grant(
        namespace=other,
        principal_id=_OWNER.object_id,
        role=NamespaceRole.OWNER,
        granted_by=_ADMIN.object_id,
    )
    await session.commit()

    # `_NS` のロックを持ったまま、別の名前空間へ publish する。
    async with _cleanup(session, other_session):
        assert await NamespaceRepository(session).get_locked(_NS) is not None
        await asyncio.wait_for(
            publish_version(
                namespace=other,
                payload=PublishRequest(version="1.0.0", turtle=_TTL),
                principal=_OWNER,
                session=other_session,
                blob=blob_store,
                store=_NullStore(),
                settings=settings,
                response=Response(),
            ),
            timeout=30.0,
        )
        await other_session.commit()
        assert await blob_store.list_versions(namespace=other) != []


# ------------------------------------------- 危険な順序を直接作って検証する


@pytest.mark.integration
async def test_Blob_確認の直後に_publish_が割り込めない(
    session: AsyncSession,
    other_session: AsyncSession,
    blob_store: OntologyBlobStore,
    settings: Settings,
) -> None:
    """**これが唯一、削除側の行ロックの必要性を突くテストである。**

    危険な順序は次で、**削除側が `FOR UPDATE` を Blob の確認より前に取らない
    限り閉じない**。

    | 時刻 | DELETE | PUBLISH |
    |---|---|---|
    | t1 | Blob を確認 → 空 | |
    | t2 | | Blob に `.ttl` を書いて PostgreSQL に版を書き commit |
    | t3 | PostgreSQL の名前空間を削除 → commit | |

    **`DELETE` 文が暗黙に取る行ロックでは遅すぎる。** t3 で待っても、t2 の
    Blob 書き込みは既に終わっている。結果は「Blob に TTL があって
    PostgreSQL には何も無い」= ローダが名前空間を復活させる状態である。

    t1 の直後に publish を差し込むために、`list_versions` を包んで
    **その中から** publish を起動する。削除側がロックを持っていれば publish は
    待たされ、Blob は汚れない。
    """
    await _setup(session)
    original = blob_store.list_versions
    started: list[asyncio.Task[None]] = []

    async def _list_then_publish(namespace: str | None = None) -> list[str]:
        """Blob を確認した**直後**に publish を割り込ませる。"""
        result = await original(namespace=namespace)
        if not started:
            task = asyncio.create_task(_publish(other_session, blob_store, settings))
            started.append(task)
            # **完走したら割り込めたということ。** 行ロックが効いていれば
            # ここでタイムアウトする。
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=_BLOCKED_FOR)
        return result

    async with _cleanup(session, other_session) as tasks:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(blob_store, "list_versions", _list_then_publish)
            await _delete(session, blob_store)
        tasks.extend(started)

        # **食い違いが無いことを確かめる。** 名前空間は消えていて、
        # **Blob にも `.ttl` が無い**。片方だけが残っていたら復活する。
        assert await NamespaceRepository(session).get(_NS) is None
        assert await original(namespace=_NS) == [], (
            "名前空間を削除したのに Blob に TTL が残っている。"
            "ローダは Blob だけを見て再構築するので、この名前空間は復活する"
        )
