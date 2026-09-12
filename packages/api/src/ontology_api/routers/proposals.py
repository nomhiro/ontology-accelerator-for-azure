"""カタログからオントロジー候補を生成する(ADR-0043、`P2A-02`)。

Phase 2 柱 A の中心である。`P2A-01` のカタログを入力に、LLM が OWL/SHACL の
候補を作る。

## 候補は `draft` にしか入らない

**この製品の前提は「LLM の出力が人間の承認を経ずに正本へ入ることはない」**
である。これまでその前提は**LLM を呼ぶ経路が 1 つも無かったから**成り立って
いた。ここで初めてコードで強制する(ADR-0043 決定5)。

`draft` は Blob と PostgreSQL にだけ存在し、**Fuseki には一切現れない**
(ADR-0010 決定1)。つまり**エージェントからは見えない**。このハンドラは
`submit` も `approve` も呼ばない。

## 実行には `owner` が必要

**組織のスキーマのメタデータをモデルの提供者へ送る**外向きの操作であり、
**呼ぶたびに課金される**(ADR-0043 決定7)。ソースの登録(ADR-0041 決定8)と
同じロールに揃えてある — 「この DB をスキャンしてよい」と決めた人が、
「このカタログをモデルへ送ってよい」も決める。

## 多すぎたら断る。切り詰めない

`PROPOSAL_MAX_TABLES` を超えたら 413(ADR-0043 決定6)。**候補の Turtle には
「一部である」と書く場所が無い**(ADR-0034 決定4 と同じ「封筒が無い」問題)。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import (
    BlobDep,
    CurrentPrincipal,
    SessionDep,
    SettingsDep,
    StoreDep,
)
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.scan import ScanRepository
from ontology_api.services.authorization import (
    PermissionDeniedError,
    principal_id_of,
    require_namespace_role,
)
from ontology_api.services.projection import ProjectionService
from ontology_api.services.proposal import (
    ModelUnavailableError,
    ProposalService,
    ProposalUnusableError,
)
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.models import NamespaceRole, ScanTable
from ontology_core.turtle import TurtleSyntaxError

router = APIRouter(prefix="/namespaces", tags=["proposals"])


class ProposalRequest(BaseModel):
    """候補の生成要求。"""

    tables: list[str] = Field(
        default_factory=list,
        description="対象のテーブル名(`schema.table` または `table`)。"
        "**省略時はカタログ全体**。多すぎると 413 で断る(切り詰めない)",
        examples=[["public.products", "public.categories"]],
    )
    version: str | None = Field(default=None, description="作る `draft` の版番号。省略時は自動採番")
    run_id: int | None = Field(
        default=None,
        description="使うスキャンの run。省略時は**最後に完了した** run",
    )
    reason: str = Field(
        default="",
        description="この候補を作る理由。**モデルの出自は自動で追記される**(ADR-0043 決定10)",
    )


def _matches(table: ScanTable, selectors: set[str]) -> bool:
    """`schema.table` と `table` のどちらでも選べる。"""
    return f"{table.schema_name}.{table.table_name}" in selectors or table.table_name in selectors


@router.post(
    "/{namespace}/scan-sources/{name}/propose",
    status_code=status.HTTP_201_CREATED,
    summary="カタログから OWL/SHACL の候補を生成して `draft` にする(`owner`)",
)
async def propose_ontology(
    namespace: str,
    name: str,
    payload: ProposalRequest,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """カタログから候補を生成し、**`draft` として記録する**。`owner` が必要。

    **`submit` も `approve` もしない**(ADR-0043 決定5)。`draft` は Fuseki に
    現れないので、エージェントからは見えない。人が `submit` してレビューへ
    回す。

    **検証を通らなかった候補は返さない**(決定3・4)。上限回数まで理由を
    添えて再試行し、それでも通らなければ 422 で断る — **部分的に正しい
    Turtle を返さない。**

    **何回目で通ったかを応答と監査に残す**(決定4・10)。1 回で通った候補と
    3 回目でやっと通った候補は、レビュアにとって別物である。
    """
    try:
        validate_namespace_name(namespace)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    ns = await NamespaceRepository(session).get(namespace)
    if ns is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{namespace}' が見つかりません",
        )
    try:
        # **`owner` である**(ADR-0043 決定7)。外向きのデータの流れと課金を
        # 伴う操作を、ソースの登録と同じロールに揃えている。
        await require_namespace_role(
            session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    repo = ScanRepository(session)
    source = await repo.get_source(namespace=namespace, name=name)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"ソース '{name}' が見つかりません"
        )

    if payload.run_id is None:
        run = await repo.latest_succeeded_run(source_id=source.id)
    else:
        run = await repo.get_run(source_id=source.id, run_id=payload.run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"run {payload.run_id} が見つかりません",
            )
    if run is None:
        # **「まだスキャンしていない」を「テーブルが 0 件」にしない**
        # (ADR-0041 決定7)。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ソース '{name}' には完了したスキャンがありません。"
            "先に POST .../scan を実行してください",
        )

    tables = await repo.list_catalog(run_id=run.id)
    if payload.tables:
        selectors = set(payload.tables)
        tables = [table for table in tables if _matches(table, selectors)]
        missing = selectors - {
            selector
            for table in tables
            for selector in (f"{table.schema_name}.{table.table_name}", table.table_name)
        }
        if missing:
            # **黙って無視しない。** 綴りを間違えたテーブルを落として
            # 生成すると、覆っていない範囲が応答のどこにも出ない。
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"カタログに無いテーブルを指定しています: {', '.join(sorted(missing))}",
            )
    if not tables:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"run {run.id} のカタログにテーブルがありません",
        )
    if len(tables) > settings.proposal_max_tables:
        # **切り詰めない**(ADR-0043 決定6)。候補の Turtle には「一部である」と
        # 書く場所が無い。
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"対象が {len(tables)} 件あり、上限 {settings.proposal_max_tables} 件を"
            "超えています。`tables` で対象を選んでください"
            "(切り詰めると、覆っている範囲を主張しない候補ができます)",
        )

    proposal = ProposalService(settings=settings)
    try:
        result = await proposal.propose(namespace=namespace, base_iri=ns.base_iri, tables=tables)
    except ModelUnavailableError as exc:
        # **502 にする。** こちらの入力の誤りではなく、外部の依存に届かな
        # かったことである。
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ProposalUnusableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    # **ここから先は既存の `publish` と同じ経路である**(ADR-0043 決定5)。
    # 候補専用の保管場所を作らない — 不変リビジョン・監査・意味的差分を
    # もう一度実装することになる。
    projection = ProjectionService(
        session=session,
        blob=blob,
        store=store,
        graph_iri_base=settings.graph_iri_base,
        retain_superseded=settings.superseded_retain,
    )
    reason = f"{payload.reason}\n{result.provenance()}" if payload.reason else result.provenance()
    try:
        published = await projection.publish(
            namespace=namespace,
            turtle=result.turtle,
            actor=principal_id_of(principal),
            actor_type=principal.actor_type,
            version=payload.version,
            reason=reason,
        )
    except TurtleSyntaxError as exc:  # pragma: no cover - 検証済みなので通常起きない
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return {
        "namespace": namespace,
        "source": name,
        "run_id": run.id,
        "version": published.version,
        "status": published.status.value,
        "table_count": len(tables),
        # **何回目で通ったか**(ADR-0043 決定4)。3 回目でやっと通った候補は
        # 「モデルがこのスキーマを扱いかねている」信号である。
        "attempts": result.attempts,
        "model": result.model,
        "deployment": result.deployment,
    }
