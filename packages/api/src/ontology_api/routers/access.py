"""アクセスログの読み出しと削除(ADR-0018、`P2B-05`)。

## イベントは `owner`、集約は `data-analyst`

**`P2B-11` の監査照会で使った「足し合わせれば見えるものを集約したときだけ
隠すのは見せかけの制限」という論法は、ここには当てはまらない。** 監査照会は
版単位の `decisions` が既に `data-analyst` に開いていたので、集約を絞っても
意味が無かった。アクセスログは違う — **「誰がいつ何を問い合わせたか」は
他のどの口からも導出できない**。

そしてこれは**同僚の行動の記録**である。分析者が他の分析者のクエリ履歴を
読める必要は無い(`namespace_roles` の一覧を `owner` に限ったのと同じ判断。
ADR-0014)。

**集約(`term_access`)は個人を特定しない**ので分析者に開く。指標を見るために
`owner` を要求すると、指標が使われなくなる。

## 削除は明示的で、削除したことが消せない場所に残る

`audit_events` は `DELETE` を剥奪しているが(ADR-0011 決定2)、`access_events`
には与えている。**性質が違う** — 人の決定の記録は緩やかに増えて消す理由が
無いが、機械の参照の記録はエージェントの稼働に比例して無限に伸びる。

ただし「消せる」を「黙って消える」にはしない(ADR-0018 決定2)。自動の削除
ジョブは置かず、運用者が `before` を明示して呼ぶ。そして**削除したこと自体を
`audit_events` に記録する**ので、消えた事実は消せない場所に残る。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import CurrentPrincipal, SessionDep
from ontology_api.repositories.access import AccessRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.services.authorization import (
    PermissionDeniedError,
    principal_id_of,
    require_namespace_role,
)
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.models import AccessPage, NamespaceRole, TermAccess

router = APIRouter(prefix="/namespaces", tags=["access-log"])


class AccessLogPurge(BaseModel):
    """アクセスログの削除要求(ADR-0018 決定2)。"""

    before: datetime = Field(
        description="この時刻より前のイベントを削除する。**既定値は無い**。タイムゾーン必須",
    )
    reason: str = Field(
        min_length=1,
        description="削除の理由(**必須**)。`audit_events` に記録され、消せない",
    )


async def _prepare(
    session: SessionDep,
    *,
    namespace: str,
    principal: CurrentPrincipal,
    required: NamespaceRole,
) -> None:
    """名前空間名を検証し、存在と権限を確かめる。"""
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
            session, namespace=namespace, principal=principal, required=required
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.get("/{namespace}/access-log", summary="コンテキストのアクセスログを照会する")
async def query_access_log(
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    actor: Annotated[
        str | None,
        Query(description="この主体だけに絞る(Entra のオブジェクト ID。完全一致)"),
    ] = None,
    since: Annotated[
        datetime | None, Query(description="この時刻以降(**含む**)。タイムゾーン必須")
    ] = None,
    until: Annotated[
        datetime | None, Query(description="この時刻より前(**含まない**)。タイムゾーン必須")
    ] = None,
    limit: Annotated[int, Query(description="1 ページの件数")] = AccessRepository.DEFAULT_LIMIT,
    cursor: Annotated[
        int | None, Query(description="前のページの `next_cursor` をそのまま渡す")
    ] = None,
) -> AccessPage:
    """「いつ・誰に・どの版の何を返したか」を新しい順に 1 ページ返す。

    **`owner` が必要**(ADR-0018 決定5)。**同僚の行動の記録**であり、他の
    どの口からも導出できないため、分析者には開かない。

    **`used_graph_clause` が真の行は、`default_graph_version` が読んだ版の
    全体ではない**(ADR-0018 決定7)。`GRAPH` 句を明示したクエリは他の版を
    読みうる。

    ページングの鍵は `id` である(`occurred_at` はトランザクション開始時刻な
    ので同時刻が並ぶ)。`next_cursor` が `null` なら最後のページである。
    """
    await _prepare(session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER)
    try:
        return await AccessRepository(session).query_events(
            namespace=namespace,
            actor=actor,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.get("/{namespace}/term-access", summary="用語ごとの参照の集約を返す")
async def list_term_access(
    namespace: str, principal: CurrentPrincipal, session: SessionDep
) -> list[TermAccess]:
    """用語ごとの「最後の参照」と「参照回数」を、**古い順**に返す。

    **`data-analyst` で読める**(ADR-0018 決定5)。個人を特定しないので、
    指標として広く見られるべきである。

    古い順にするのは、健全性指標が知りたいのが「参照されていない用語」だから
    である。新しい順に並べると、見たいものが最後に来る。

    **一度も参照されていない用語はここに現れない。** 行が作られるのは最初に
    参照されたときである。「未参照の用語」を数えるには、オントロジーの用語
    一覧との差集合を取る必要がある(`P2B-06` の仕事)。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    return await AccessRepository(session).list_term_access(namespace)


@router.post(
    "/{namespace}/access-log/purge",
    summary="保持期間を過ぎたアクセスログを削除する(削除したことは監査に残る)",
)
async def purge_access_log(
    namespace: str,
    payload: AccessLogPurge,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> dict[str, Any]:
    """`before` より前のイベントを削除する。**`owner` が必要**。

    **`term_access`(集約)は消さない**(ADR-0018 決定1)。集約はイベントより
    長生きしなければならない — 消した瞬間に「90 日参照されていない」が
    計算不能になる。

    **削除したことを `audit_events` に記録する。** 監査証跡の側は追記専用
    なので(ADR-0011 決定2)、この記録は消えない。「アクセスログが消えている」
    という事実が、消せないテーブルに残る。

    **自動の削除ジョブは無い。** 「消せる」を「黙って消える」にしないため、
    運用者が明示的に呼ぶ形だけを用意している。
    """
    await _prepare(session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER)
    repo = AccessRepository(session)
    try:
        deleted = await repo.purge_before(namespace=namespace, before=payload.before)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    await AuditRepository(session).record(
        namespace=namespace,
        action="access-log-purged",
        actor=principal_id_of(principal),
        subject=f"{namespace}/access-log",
        reason=f"{payload.before.isoformat()} より前の {deleted} 件を削除: {payload.reason}",
    )
    return {"namespace": namespace, "before": payload.before, "deleted": deleted}
