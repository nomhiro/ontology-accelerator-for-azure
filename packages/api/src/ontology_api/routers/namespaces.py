"""名前空間の管理。

正本は PostgreSQL、射影は Fuseki のデータセットである。作成は「正本(DB)に書く →
射影先(データセット)を作る」の順で固定する。データセット作成に失敗しても名前空間は
残り、reconcile が後から埋める(`docs/adr/0002-triple-store-as-rebuildable-projection.md`)。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import CurrentPrincipal, SessionDep, StoreDep
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
) -> None:
    """名前空間を削除する。

    順序は作成と対にして「正本(DB)から消す → 射影先(データセット)を消す」にする。
    """
    del principal
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
