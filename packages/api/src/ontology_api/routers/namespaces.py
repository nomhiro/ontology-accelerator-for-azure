"""名前空間の管理。

正本は PostgreSQL、射影は Fuseki のデータセットである。作成は「正本(DB)に書く →
射影先(データセット)を作る」の順で固定する。データセット作成に失敗しても名前空間は
残り、reconcile が後から埋める(`docs/adr/0002-triple-store-as-rebuildable-projection.md`)。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import BlobDep, CurrentPrincipal, SessionDep, StoreDep
from ontology_api.repositories.namespaces import NamespaceExistsError, NamespaceRepository
from ontology_core.graphs import NamespaceNameError, dataset_name
from ontology_core.models import Namespace
from ontology_core.sparql.client import SparqlStoreError

router = APIRouter(prefix="/namespaces", tags=["namespaces"])
logger = logging.getLogger(__name__)


class NamespaceCreate(BaseModel):
    """名前空間の作成要求。"""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$", examples=["retail-core"])
    display_name: str = Field(examples=["小売ドメイン"])
    description: str = ""
    base_iri: str = Field(examples=["https://example.com/ontology/retail#"])


@router.get("", summary="名前空間の一覧を取得する")
async def list_namespaces(principal: CurrentPrincipal, session: SessionDep) -> list[Namespace]:
    """呼び出し元が参照できる名前空間を返す。

    Phase 2 で名前空間ごとのロールによる絞り込みを行う。現時点では全件返す。
    """
    del principal  # Phase 2 で認可のフィルタに使う
    return await NamespaceRepository(session).list_all()


@router.post("", status_code=status.HTTP_201_CREATED, summary="名前空間を作成する")
async def create_namespace(
    payload: NamespaceCreate,
    principal: CurrentPrincipal,
    session: SessionDep,
    store: StoreDep,
) -> Namespace:
    """名前空間を作成し、対応する Fuseki データセットを用意する。

    順序は「正本(DB)に書く → 射影先(データセット)を作る」。逆にしない。
    データセット作成に失敗した場合も名前空間は残る。reconcile が後から埋める。
    """
    repo = NamespaceRepository(session)
    try:
        namespace = await repo.create(
            name=payload.name,
            display_name=payload.display_name,
            description=payload.description,
            base_iri=payload.base_iri,
            created_by=principal.object_id or principal.subject,
        )
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except NamespaceExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    try:
        await store.create_dataset(dataset_name(namespace.name))
    except SparqlStoreError:
        logger.exception(
            "名前空間 '%s' のデータセット作成に失敗しました。reconcile で回復します",
            namespace.name,
        )

    return namespace


@router.get("/{name}", summary="名前空間を 1 件取得する")
async def get_namespace(name: str, principal: CurrentPrincipal, session: SessionDep) -> Namespace:
    """名前空間を取得する。"""
    del principal
    namespace = await NamespaceRepository(session).get(name)
    if namespace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{name}' が見つかりません",
        )
    return namespace


@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="名前空間を削除する",
)
async def delete_namespace(
    name: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    store: StoreDep,
    blob: BlobDep,
) -> None:
    """名前空間を削除する。

    順序は作成と対にして「正本(DB)から消す → 射影先(データセット)を消す」にする。

    ただし Blob の当該名前空間プレフィックス配下に公開済み TTL が 1 件でも残って
    いれば 409 Conflict で拒否し、PG 行・データセットのどちらも変更しない。
    `containers/fuseki/load-snapshot.sh` は PostgreSQL を一切見ず Blob だけを見て
    名前空間ごとの TDB2 を再構築するため、Blob を消さずに PG 行だけ消すと
    レプリカ再作成(デプロイ・スケールイベント等)で削除済みのオントロジーが
    復活し、認証済みの呼び出し元に返ってしまう(ブランチ全体レビュー C-1)。
    判定を PostgreSQL の版数に依拠すると、publish 失敗で残った孤児 TTL
    (PG に記録される前に Blob 書き込みだけ成功した場合)を見逃すため、
    **復活源そのものである Blob 本体**を見る。オントロジーは不変リビジョン
    (ADR-0006)なので、この削除経路で Blob を消す実装にはしない。公開済み
    オントロジーを含む名前空間の削除は Phase 2(監査経路)で対応する。
    """
    del principal
    if await NamespaceRepository(session).get(name) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{name}' が見つかりません",
        )

    remaining = await blob.list_versions(namespace=name)
    if remaining:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"名前空間 '{name}' には公開済みオントロジーの Blob が "
                f"{len(remaining)} 件残っているため削除できません: "
                f"{', '.join(remaining)}. "
                "公開済みオントロジーを含む名前空間の削除は Phase 2(監査経路)で"
                "対応します。"
            ),
        )

    deleted = await NamespaceRepository(session).delete(name)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{name}' が見つかりません",
        )

    try:
        await store.delete_dataset(dataset_name(name))
    except SparqlStoreError:
        logger.exception(
            "名前空間 '%s' のデータセット削除に失敗しました。reconcile で回復します",
            name,
        )
