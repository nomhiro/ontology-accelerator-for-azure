"""監査証跡の照会(`P2B-11`、ADR-0006 §3 / ADR-0009 決定7)。

## 版単位の `decisions` との違い

`GET /namespaces/{ns}/versions/{v}/decisions`(`P2B-08`)は**1 つの版について
「誰が・いつ・なぜ」を起きた順に**返す。エージェントが答えの根拠を示すための口
である(MCP の `version_decisions` がこれを叩く)。

こちらは**名前空間全体を新しい順に、絞り込みとページングつきで**返す。
運用者が「先週この名前空間で何が起きたか」「この人が何をしたか」を追うための
口である。

## `data-analyst` で読める

**`owner` を要求しない。** 版単位の `decisions` は既に `data-analyst` で
読めるので、名前空間全体の照会だけを絞っても、版を列挙して同じ情報を集め
られる。**足し合わせれば見えるものを、集約したときだけ隠すのは見せかけの
制限である。** 見せかけの制限は「守られている」という誤解を作るぶん、
制限が無いより悪い。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from ontology_api.dependencies import CurrentPrincipal, SessionDep
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.services.authorization import PermissionDeniedError, require_namespace_role
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.models import AuditPage, NamespaceRole

router = APIRouter(prefix="/namespaces", tags=["audit"])


@router.get("/{namespace}/audit", summary="監査証跡を照会する")
async def query_audit(
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    # **`Annotated` を使う。** `action: str | None = Query(default=None)` と
    # 書くと、既定値そのものが `Query` オブジェクトになり、**ハンドラを直接
    # 呼ぶテストで `Query` インスタンスが値として流れ込む**(FastAPI 経由なら
    # 解決されるので、HTTP で叩くテストだけでは気づけない)。
    action: Annotated[
        str | None,
        Query(
            description="この操作だけに絞る(完全一致)。`published` / `submitted` / "
            "`approved` / `rejected` / `superseded` など"
        ),
    ] = None,
    actor: Annotated[
        str | None,
        Query(description="この主体だけに絞る(Entra のオブジェクト ID。完全一致)"),
    ] = None,
    subject: Annotated[
        str | None,
        Query(description="この対象だけに絞る(`<名前空間>@<バージョン>`。完全一致)"),
    ] = None,
    since: Annotated[
        datetime | None, Query(description="この時刻以降(**含む**)。タイムゾーン必須")
    ] = None,
    until: Annotated[
        datetime | None, Query(description="この時刻より前(**含まない**)。タイムゾーン必須")
    ] = None,
    limit: Annotated[int, Query(description="1 ページの件数")] = AuditRepository.DEFAULT_LIMIT,
    cursor: Annotated[
        int | None, Query(description="前のページの `next_cursor` をそのまま渡す")
    ] = None,
) -> AuditPage:
    """名前空間の監査証跡を**新しい順**に 1 ページ返す。`data-analyst` が必要。

    **並び順とページングの鍵は `id` である**(`occurred_at` ではない)。
    `occurred_at` は `now()` = トランザクション開始時刻なので、同一
    トランザクション内の複数イベントは同じ値になり、時刻でページングすると
    境界で取りこぼす。

    **`next_cursor` が `None` なら最後のページ**である。件数が `limit`
    ちょうどでもそうなる。「返った件数が `limit` より少ないから最後」という
    判定に頼らないこと。

    期間は半開区間 `[since, until)` である。境界を両側とも含めると、期間を
    並べて集計したときに二重に数える。
    """
    try:
        validate_namespace_name(namespace)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if await NamespaceRepository(session).get(namespace) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{namespace}' が見つかりません",
        )
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
        return await AuditRepository(session).query(
            namespace=namespace,
            action=action,
            actor=actor,
            subject=subject,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        # 照会の指定の誤り(範囲外の limit、タイムゾーン無しの日時、逆転した期間)。
        # **空の結果を返さない。** 空だと「本当に何も無い」と誤解させる。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
