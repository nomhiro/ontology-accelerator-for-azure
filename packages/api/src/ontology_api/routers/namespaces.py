"""名前空間の管理。

**Phase 0 の制約**: 永続化はプロセス内のメモリに置いたスタブである。Web 側の開発を
先に進められるようにルートとスキーマだけ確定させてある。Phase 1 で正本の PostgreSQL に
差し替え、同時に Fuseki 側のデータセット作成(名前空間の隔離)も行う。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import CurrentPrincipal
from ontology_core.models import Namespace

router = APIRouter(prefix="/namespaces", tags=["namespaces"])

# Phase 1 で PostgreSQL に置き換えるスタブ。プロセス再起動で消える。
_IN_MEMORY_STUB: dict[str, Namespace] = {}


class NamespaceCreate(BaseModel):
    """名前空間の作成要求。"""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$", examples=["retail-core"])
    display_name: str = Field(examples=["小売ドメイン"])
    description: str = ""
    base_iri: str = Field(examples=["https://example.com/ontology/retail#"])


@router.get("", summary="名前空間の一覧を取得する")
async def list_namespaces(principal: CurrentPrincipal) -> list[Namespace]:
    """呼び出し元が参照できる名前空間を返す。

    Phase 2 で名前空間ごとのロールによる絞り込みを行う。現時点では全件返す。
    """
    del principal  # Phase 2 で認可のフィルタに使う
    return list(_IN_MEMORY_STUB.values())


@router.post("", status_code=status.HTTP_201_CREATED, summary="名前空間を作成する")
async def create_namespace(payload: NamespaceCreate, principal: CurrentPrincipal) -> Namespace:
    """名前空間を作成する。"""
    if payload.name in _IN_MEMORY_STUB:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"名前空間 '{payload.name}' は既に存在します",
        )

    namespace = Namespace(
        name=payload.name,
        display_name=payload.display_name,
        description=payload.description,
        base_iri=payload.base_iri,
        created_at=datetime.now(UTC),
        created_by=principal.object_id or principal.subject,
    )
    _IN_MEMORY_STUB[namespace.name] = namespace
    return namespace


@router.get("/{name}", summary="名前空間を 1 件取得する")
async def get_namespace(name: str, principal: CurrentPrincipal) -> Namespace:
    """名前空間を取得する。"""
    del principal
    if (namespace := _IN_MEMORY_STUB.get(name)) is None:
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
async def delete_namespace(name: str, principal: CurrentPrincipal) -> None:
    """名前空間を削除する。"""
    del principal
    if _IN_MEMORY_STUB.pop(name, None) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{name}' が見つかりません",
        )
