"""オントロジーのバージョンの投入と一覧。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import BlobDep, CurrentPrincipal, SessionDep, SettingsDep, StoreDep
from ontology_api.repositories.versions import VersionRepository
from ontology_api.services.projection import (
    ProjectionService,
    ReconcileReport,
    UnknownNamespaceError,
)
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.models import OntologyVersion

router = APIRouter(tags=["versions"])


class PublishRequest(BaseModel):
    """オントロジーの投入要求。"""

    turtle: str = Field(min_length=1, max_length=20_000_000, description="Turtle 形式の本文")
    version: str | None = Field(default=None, description="省略時は自動採番")


@router.post(
    "/namespaces/{namespace}/versions",
    status_code=status.HTTP_201_CREATED,
    summary="オントロジーを新しいバージョンとして公開する",
)
async def publish_version(
    namespace: str,
    payload: PublishRequest,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
) -> OntologyVersion:
    """正本に書いてからストアへ射影する。"""
    service = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base=settings.graph_iri_base
    )
    try:
        # `namespace` はこの後 Blob パス・グラフ IRI・Fuseki データセット名の
        # 組み立てに使われる(`ProjectionService.publish` 経由)。DB に存在しない
        # 名前空間なら結局 UnknownNamespaceError で 404 になるが、それは
        # 「たまたま検証されている」だけの経路であり、名前空間名がセキュリティ境界
        # であることの明示的な契約にはならない。パスパラメータの入口で検証する。
        validate_namespace_name(namespace)
        return await service.publish(
            namespace=namespace,
            turtle=payload.turtle,
            actor=principal.object_id or principal.subject,
            version=payload.version,
        )
    except UnknownNamespaceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/namespaces/{namespace}/versions", summary="バージョンの一覧を取得する")
async def list_versions(
    namespace: str, principal: CurrentPrincipal, session: SessionDep
) -> list[OntologyVersion]:
    """名前空間のバージョン一覧を返す。

    不正な `namespace` は該当行が無いだけで空リストが返り実害はないが、
    名前空間名はセキュリティ境界(`packages/api/tests/test_isolation.py`)
    なので、パスパラメータの入口では一貫して検証する。
    """
    del principal
    try:
        validate_namespace_name(namespace)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await VersionRepository(session).list_for(namespace)


@router.post("/admin/reconcile", summary="正本を基準にストアの状態を揃える")
async def reconcile(
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
) -> ReconcileReport:
    """レプリカ再作成後や射影失敗後の回復に使う。

    Phase 2 で platform-admin ロールを要求するようにする。
    """
    del principal
    service = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base=settings.graph_iri_base
    )
    return await service.reconcile()
