"""オントロジーのバージョンの投入と一覧。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import BlobDep, CurrentPrincipal, SessionDep, SettingsDep, StoreDep
from ontology_api.repositories.versions import VersionRepository
from ontology_api.services.projection import (
    AutoVersionError,
    InvalidTransitionError,
    ProjectionService,
    ReconcileReport,
    UnknownNamespaceError,
    UnknownVersionError,
)
from ontology_core.graphs import NamespaceNameError, validate_namespace_name, validate_version
from ontology_core.models import OntologyVersion
from ontology_core.turtle import TurtleSyntaxError

router = APIRouter(tags=["versions"])


class PublishRequest(BaseModel):
    """オントロジーの投入要求。"""

    turtle: str = Field(min_length=1, max_length=20_000_000, description="Turtle 形式の本文")
    version: str | None = Field(default=None, description="省略時は自動採番")


class RejectRequest(BaseModel):
    """却下要求。理由は必須(空文字は 422)。"""

    reason: str = Field(min_length=1, description="却下の理由(必須)")


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
    except AutoVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except TurtleSyntaxError as exc:
        # P1-C2: 構文が壊れた TTL。`ProjectionService.publish` は Blob へ書く
        # 前に検証しているため、ここに来た時点で正本(Blob・PostgreSQL)には
        # 何も残っていない。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


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


@router.post(
    "/namespaces/{namespace}/versions/{version}/submit",
    summary="draft を in-review にする(名前付きグラフへ射影)",
)
async def submit_version(
    namespace: str,
    version: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
) -> OntologyVersion:
    """ADR-0010 決定1・5。Phase 1 では権限を強制しない(認証済みの呼び出し元は誰でも実行できる)。"""
    validate_namespace_name(namespace)
    validate_version(version)
    service = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base=settings.graph_iri_base
    )
    try:
        return await service.submit(
            namespace=namespace, version=version, actor=principal.object_id or principal.subject
        )
    except UnknownVersionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/namespaces/{namespace}/versions/{version}/approve",
    summary="in-review を approved にする(既定+名前付きグラフへ射影、前の版は自動 superseded)",
)
async def approve_version(
    namespace: str,
    version: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
) -> OntologyVersion:
    """ADR-0010 決定1・3・5・6。

    **Phase 1 では権限を強制しない。** 四眼原則(提案者と承認者を別人にする)と
    「責任者のみが承認できる」制約は、名前空間 RBAC(`P2A-06`)と責任者
    (`P2B-04`)に依存するため Phase 2 で対応する(ADR-0010)。認証済みの
    呼び出し元は誰でも approve できる。`approved_by` には実際に呼び出した
    主体が記録される(記録は正しいが、強制は無い)。README にも明記している。
    """
    validate_namespace_name(namespace)
    validate_version(version)
    service = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base=settings.graph_iri_base
    )
    try:
        return await service.approve(
            namespace=namespace, version=version, actor=principal.object_id or principal.subject
        )
    except UnknownVersionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/namespaces/{namespace}/versions/{version}/reject",
    summary="in-review を draft に戻す(理由必須。名前付きグラフから外す)",
)
async def reject_version(
    namespace: str,
    version: str,
    payload: RejectRequest,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
) -> OntologyVersion:
    """ADR-0010 決定1・5。Phase 1 では権限を強制しない。"""
    validate_namespace_name(namespace)
    validate_version(version)
    service = ProjectionService(
        session=session, blob=blob, store=store, graph_iri_base=settings.graph_iri_base
    )
    try:
        return await service.reject(
            namespace=namespace,
            version=version,
            actor=principal.object_id or principal.subject,
            reason=payload.reason,
        )
    except UnknownVersionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
