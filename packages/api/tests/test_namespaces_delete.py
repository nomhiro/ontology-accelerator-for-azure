"""名前空間の削除。

C-1(ブランチ全体レビュー): `DELETE /namespaces/{name}` は PostgreSQL の行と Fuseki の
データセットを消すだけで、Blob 上の正本 TTL には触らない。`load-snapshot.sh` は
PostgreSQL を一切参照せず Blob だけを見て名前空間を再構築するため、削除後に
Fuseki レプリカが再作成される(デプロイ・スケールイベント等)と削除済みのはずの
オントロジーが復活し、認証済みの呼び出し元に返ってしまう。

対応: Blob の当該名前空間プレフィックス配下に 1 件でも TTL が残っていれば
409 Conflict で削除を拒否し、PG 行 / Fuseki データセットのどちらも変更しない。
判定は PostgreSQL の版数ではなく Blob 本体を見る(`OntologyBlobStore.list_versions`)。
publish 失敗で残った孤児 TTL は PG に版が無くても復活源になるため、PG 側の情報だけでは
この経路を捉えられない。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.routers.namespaces import delete_namespace
from ontology_api.services.projection import ProjectionService
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.graphs import dataset_name
from ontology_core.sparql.client import FusekiStore

pytestmark = pytest.mark.integration

TTL = "@prefix ex: <https://e.example/#> .\nex:A a ex:Class .\n"
_PRINCIPAL = Principal.local_dev()

_PORT = os.environ.get("FUSEKI_PORT", "3030")
_BASE = f"http://localhost:{_PORT}"


@pytest.fixture
async def store() -> AsyncIterator[FusekiStore]:
    """実物の Fuseki(`just up` の docker compose)に対する管理クライアント。

    `test_isolation.py` と同じ設定。C-1 は「レプリカ再作成後の復活」が本題であり、
    フェイクストアでは Fuseki 側の実データセット状態を確認できない。
    """
    s = FusekiStore(
        query_endpoint=_BASE + "/{dataset}/sparql",
        update_endpoint=_BASE + "/{dataset}/update",
        gsp_endpoint=_BASE + "/{dataset}/data",
        admin_endpoint=_BASE + "/$/",
        admin_auth=("admin", os.environ.get("FUSEKI_ADMIN_PASSWORD", "localdev")),
    )
    yield s
    await s.aclose()


async def _create_namespace(session: AsyncSession, store: FusekiStore, name: str) -> None:
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    if dataset_name(name) not in await store.list_datasets():
        await store.create_dataset(dataset_name(name))
    await session.commit()


async def test_delete_is_rejected_with_409_when_blob_has_published_versions(
    session: AsyncSession, blob_store: OntologyBlobStore, store: FusekiStore
) -> None:
    """公開済みバージョンが Blob にある名前空間の削除は 409 になり、
    PG 行と Fuseki データセットのどちらも消えない(部分的に消えていないこと)。
    """
    name = "del-published"
    await _create_namespace(session, store, name)
    svc = ProjectionService(
        session=session, blob=blob_store, store=store, graph_iri_base="urn:ontology:graph"
    )
    await svc.publish(namespace=name, turtle=TTL, actor="t")
    await session.commit()

    try:
        with pytest.raises(HTTPException) as exc_info:
            await delete_namespace(
                name=name,
                principal=_PRINCIPAL,
                session=session,
                store=store,
                blob=blob_store,
            )
        assert exc_info.value.status_code == 409

        # 部分的に消えていないこと。
        assert await NamespaceRepository(session).get(name) is not None
        assert dataset_name(name) in await store.list_datasets()
    finally:
        await store.delete_dataset(dataset_name(name))


async def test_delete_succeeds_when_blob_has_no_versions(
    session: AsyncSession, blob_store: OntologyBlobStore, store: FusekiStore
) -> None:
    """Blob が空の名前空間は従来どおり削除でき、PG 行とデータセットが消える。"""
    name = "del-empty"
    await _create_namespace(session, store, name)

    await delete_namespace(
        name=name,
        principal=_PRINCIPAL,
        session=session,
        store=store,
        blob=blob_store,
    )
    await session.commit()

    assert await NamespaceRepository(session).get(name) is None
    assert dataset_name(name) not in await store.list_datasets()


async def test_delete_is_rejected_when_blob_has_orphan_ttl_without_pg_version(
    session: AsyncSession, blob_store: OntologyBlobStore, store: FusekiStore
) -> None:
    """PG にバージョン行が無くても、Blob に孤児 TTL が残っていれば 409 になること。

    publish は「Blob に書く → PostgreSQL に記録する」の順であり、1 番目の後
    2 番目の前で失敗すると PG に版が無いのに Blob だけに TTL が残る(孤児)。
    この経路こそが C-1 の復活源であり、判定を PG の版数に依拠すると見逃す。
    """
    name = "del-orphan"
    await _create_namespace(session, store, name)
    # PG のバージョン記録を経由せず Blob に直接置く(孤児 TTL を模す)。
    await blob_store.put_version(name, "1.0.0", TTL)

    try:
        with pytest.raises(HTTPException) as exc_info:
            await delete_namespace(
                name=name,
                principal=_PRINCIPAL,
                session=session,
                store=store,
                blob=blob_store,
            )
        assert exc_info.value.status_code == 409
        assert await NamespaceRepository(session).get(name) is not None
        assert dataset_name(name) in await store.list_datasets()
    finally:
        await store.delete_dataset(dataset_name(name))
