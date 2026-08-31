"""名前空間リポジトリ。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceExistsError, NamespaceRepository
from ontology_core.graphs import NamespaceNameError

pytestmark = pytest.mark.integration


async def test_create_then_get(session: AsyncSession) -> None:
    repo = NamespaceRepository(session)

    created = await repo.create(
        name="retail-core",
        display_name="小売ドメイン",
        description="デモ",
        base_iri="https://example.com/ontology/retail#",
        created_by="tester",
    )
    assert created.name == "retail-core"
    assert created.display_name == "小売ドメイン"

    fetched = await repo.get("retail-core")
    assert fetched is not None
    assert fetched.base_iri == "https://example.com/ontology/retail#"


async def test_create_is_rejected_when_duplicated(session: AsyncSession) -> None:
    repo = NamespaceRepository(session)
    kwargs = dict(display_name="x", description="", base_iri="https://e.example/#", created_by="t")
    await repo.create(name="dup", **kwargs)

    with pytest.raises(NamespaceExistsError):
        await repo.create(name="dup", **kwargs)


async def test_create_is_rejected_when_precheck_race_slips_through(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """事前の get() チェックをすり抜けても DB の一意制約が最後の砦になること。

    本物の同時リクエストによる競合(2 つの create() が両方 get() で None を
    見た直後に両方が INSERT を試みる)を決定的に再現するのはタイミング依存で
    難しいため、事前チェックがすり抜けた状況を monkeypatch で直接再現する。
    """
    repo = NamespaceRepository(session)
    kwargs = dict(display_name="x", description="", base_iri="https://e.example/#", created_by="t")
    await repo.create(name="raced", **kwargs)
    await session.commit()

    async def _always_missing(_self: NamespaceRepository, _name: str) -> None:
        return None

    monkeypatch.setattr(NamespaceRepository, "get", _always_missing)

    with pytest.raises(NamespaceExistsError):
        await repo.create(name="raced", **kwargs)


async def test_invalid_name_is_rejected_before_touching_db(session: AsyncSession) -> None:
    repo = NamespaceRepository(session)
    with pytest.raises(NamespaceNameError):
        await repo.create(
            name="../escape",
            display_name="x",
            description="",
            base_iri="https://e.example/#",
            created_by="t",
        )


async def test_list_and_delete(session: AsyncSession) -> None:
    repo = NamespaceRepository(session)
    for name in ("alpha", "beta"):
        await repo.create(
            name=name,
            display_name=name,
            description="",
            base_iri=f"https://e.example/{name}#",
            created_by="t",
        )

    assert [n.name for n in await repo.list_all()] == ["alpha", "beta"]
    assert await repo.delete("alpha") is True
    assert [n.name for n in await repo.list_all()] == ["beta"]
    assert await repo.delete("alpha") is False


async def test_persists_across_sessions(session: AsyncSession) -> None:
    # メモリスタブではないこと(= 別のセッションから見えること)を確認する。
    repo = NamespaceRepository(session)
    await repo.create(
        name="persisted",
        display_name="p",
        description="",
        base_iri="https://e.example/p#",
        created_by="t",
    )
    await session.commit()

    other = NamespaceRepository(session)
    session.expunge_all()
    assert await other.get("persisted") is not None
