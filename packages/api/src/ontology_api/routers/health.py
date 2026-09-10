"""健全性指標(ADR-0020、`P2B-06`)。

[ADR-0009](../../../../../docs/adr/0009-ontology-operations.md) 決定5 の
「**測っていないものは、致命的になるまで見えない**」に応える口。

`data-analyst` で読める。指標は**件数だけ**を返すので個人を特定しない
(ADR-0020 決定4)。**指標に強い権限を要求すると、指標が使われなくなる。**
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from ontology_api.dependencies import BlobDep, CurrentPrincipal, SessionDep
from ontology_api.services.authorization import PermissionDeniedError, require_namespace_role
from ontology_api.services.health import HealthService, UnknownNamespaceError
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.health import SUMMARY_MAX_TERMS
from ontology_core.models import NamespaceRole

router = APIRouter(prefix="/namespaces", tags=["health"])


@router.get("/{namespace}/health", summary="名前空間の健全性指標を返す")
async def measure_health(
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    include_terms: Annotated[
        bool,
        Query(
            description="用語 IRI の一覧も返す。既定で返さないのは応答を小さく保つため"
            "(件数は個人を特定しないので権限の話ではない)"
        ),
    ] = False,
) -> dict[str, Any]:
    """ADR-0009 決定5 の 6 項目を集計して返す。`data-analyst` が必要。

    **`null` は「測れなかった」であり `0` ではない**(ADR-0020 決定3)。
    どの項目がなぜ測れなかったかは `unavailable` に並ぶ。健全性指標が
    障害時に「健全」と言うのは、目的に正面から反する。

    **「全部か無か」にはしない。** 正本の TTL に到達できなくても、PostgreSQL
    だけで測れる項目(`approval_age_days`、`unprojected_version_count`)は
    そのまま返る。

    項目の読み方:

    - `term_count`: その名前空間が発行した用語の数。**正本の TTL から数える**
      (ストアから数えると、ストアが空のときに「完全に健全」と報告してしまう)
    - `unreferenced_count` / `unreferenced_ratio`: `unreferenced_window_days`
      日間エージェントに渡っていない用語(`P2B-05` のアクセスログが原資料)。
      **一度も参照されていない用語も含む**
    - `without_owner_count`: 責任者が未設定の用語(`P2B-04`)。
      「責任者のいない用語は誰も保守しない」(ADR-0009)
    - `approval_age_days`: 現在の承認済み版が承認されてからの日数。
      **版単位である** — このシステムの承認は版単位なので、用語単位の
      「再承認の古さ」は計算できない(ADR-0020 決定2)
    - `shacl_violation_count`: **`approve` が違反をブロックするので、構造上
      ほぼ常に 0 である**(`P2A-05`)。`P2A-05` より前に承認された版と、
      shape 自身の問題を拾うために残している
    - `unprojected_version_count`: `projected_at IS NULL` の版。`draft` は
      射影しないことが正常なので数えない
    """
    try:
        validate_namespace_name(namespace)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        await require_namespace_role(
            session,
            namespace=namespace,
            principal=principal,
            required=NamespaceRole.DATA_ANALYST,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    try:
        report = await HealthService(session=session, blob=blob).measure(namespace)
    except UnknownNamespaceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return report.summary(include_terms=include_terms, max_terms=SUMMARY_MAX_TERMS)
