"""用語の検索(ADR-0050、`P3-02`)。

## 2 つの経路を 1 つの口にまとめる

| 経路 | 何に強いか |
|---|---|
| ベクトル(pgvector の cosine) | 言い換え・概念の近さ(「お客さん」→ `Customer`) |
| 3-gram(`pg_trgm` の `word_similarity`) | 表記の一致・部分一致(「顧客ID」→ `customerId`) |

**実測で両方が必要だと確かめた**(2026-09-13、PostgreSQL 16 + pgvector 0.8.6)。

| 問い | 3-gram | 判定 |
|---|---|---|
| 「顧客ID」 | `customerId` に 1.000 | 表記は 3-gram で足りる |
| 「お客様」 | **全件 0.000** | **3-gram では絶対に当たらない** |

つまり**ベクトルの経路を「あれば良いもの」として扱えない**。ただし
**逆も真ではない** — `word_similarity` は `customerId` のような識別子の
部分一致に強く、埋め込みだけでは取りこぼす。

## 「ベクトルが使えない」を「該当なし」にしない

埋め込みが 0 件、またはモデルが未設定でも **3-gram だけで検索は成立する**。
そのとき `vector_available: false` と理由を必ず返す(ADR-0050 決定8)。
**空の結果を返して「該当する用語がありません」と読ませない。**

## 権限は `data-analyst`

読み取りである。`/sparql` と同じ最小のロールにしてある — 検索は
「どの用語があるか」を知る行為であり、SPARQL で引けることと同じ範囲である。

## 作り直しは `maintainer`

埋め込みは射影なので、作り直しはデータを壊さない。一方で**モデルの呼び出しに
費用が発生する**ので、`data-analyst` には開けない。版を承認できる
`maintainer` と同じ重さに置いた。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import (
    BlobDep,
    CurrentPrincipal,
    EmbeddingClientDep,
    SessionDep,
    SettingsDep,
)
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.services.authorization import (
    NamespaceRetiredError,
    PermissionDeniedError,
    ensure_not_retired,
    principal_id_of,
    require_namespace_role,
)
from ontology_api.services.search import (
    SearchUnavailableError,
    rebuild_embeddings,
    search_terms,
)
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.models import (
    EmbeddingRebuildView,
    NamespaceRole,
    SearchHitView,
    SearchResultView,
)
from ontology_core.search import DEFAULT_LIMIT, MAX_LIMIT, SearchOutcome, clamp_limit, route_summary

router = APIRouter(prefix="/namespaces", tags=["search"])
logger = logging.getLogger(__name__)


class EmbeddingRebuildRequest(BaseModel):
    """埋め込みの作り直しの要求。"""

    reason: str = Field(
        min_length=1,
        description="作り直す理由。**必須である** — モデルを変えたのか、"
        "版を承認したのかが後から分からないと、作り直しの判断が再現しない",
    )


async def _prepare(
    session: SessionDep,
    *,
    namespace: str,
    principal: CurrentPrincipal,
    required: NamespaceRole,
) -> None:
    """名前空間名を検証し、存在と権限を確かめる(`vkg.py` と同じ形)。"""
    try:
        validate_namespace_name(namespace)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if await NamespaceRepository(session).get(namespace) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 {namespace} が見つかりません",
        )
    try:
        await require_namespace_role(
            session, namespace=namespace, principal=principal, required=required
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def to_view(outcome: SearchOutcome, *, query: str, limit: int) -> SearchResultView:
    """検索の結果を応答の形にする。

    **`limit` は丸めた後の値を返す。** 要求した値をそのまま返すと、
    上限に丸めたことが呼び出し側に見えない。
    """
    return SearchResultView(
        query=query,
        hits=tuple(
            SearchHitView(
                term_iri=hit.term_iri,
                route=hit.route.value,
                score=hit.score,
                vector_rank=hit.vector_rank,
                trigram_rank=hit.trigram_rank,
                vector_similarity=hit.vector_similarity,
                trigram_similarity=hit.trigram_similarity,
                source_text=hit.source_text,
                deprecated=hit.deprecated,
            )
            for hit in outcome.hits
        ),
        limit=clamp_limit(limit),
        vector_available=outcome.vector_available,
        vector_note=outcome.vector_note,
        embedded_term_count=outcome.embedded_term_count,
        deprecated_hits=outcome.deprecated_hits,
        conclusive=outcome.conclusive,
        routes=route_summary(outcome.hits),
    )


@router.get(
    "/{namespace}/search",
    summary="用語を検索する(`data-analyst`。状態は変えない)",
    responses={
        400: {"description": "問いが空。**空の問いで全件を返さない**"},
        404: {"description": "名前空間が無い"},
    },
)
async def search_namespace_terms(
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    client: EmbeddingClientDep,
    q: Annotated[str, Query(description="検索の問い。自然文でも識別子でもよい")],
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_LIMIT,
            description=f"返す件数。既定 {DEFAULT_LIMIT}、上限 {MAX_LIMIT}",
        ),
        # **`Annotated` の外に `Query(...)` を書かない。** 既定値の位置に
        # 置くと、ハンドラを直接呼ぶテストで `Query` オブジェクトが値として
        # 流れ込む(このリポジトリのルータのテストは直接呼ぶので必ず踏む)。
    ] = DEFAULT_LIMIT,
) -> SearchResultView:
    """用語を検索する。**2 つの経路の結果を RRF で融合して返す。**

    **状態は変えない。** 乖離の測定や SHACL 検証と同じ位置づけである。

    **`vector_available` が偽のときに「該当なし」と読んではいけない。**
    埋め込みを作っていない名前空間では、当たるのは表記の部分一致だけで、
    **「お客様」のような言い換えは実測で 1 件も当たらない**。

    **廃止済みの用語も返す。** 除外すると「なぜその用語が使えないのか」に
    答えられない(ADR-0017 決定3 と同じ向き)。`deprecated_hits` に出す。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    try:
        outcome = await search_terms(
            session,
            settings=settings,
            namespace=namespace,
            query=q,
            limit=limit,
            client=client,
        )
    except SearchUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    logger.info(
        "用語を検索しました %s (actor=%s, 件数=%d, ベクトル=%s, 埋め込み=%d 件)",
        namespace,
        principal_id_of(principal),
        len(outcome.hits),
        outcome.vector_available,
        outcome.embedded_term_count,
    )
    return to_view(outcome, query=q, limit=limit)


@router.post(
    "/{namespace}/search/rebuild",
    summary="承認済み版から埋め込みを作り直す(`maintainer` が必要)",
    responses={
        404: {
            "description": "承認済み版が無い、正本が読めない、用語が 1 件も無い、"
            "またはモデルが未設定。**「0 件作った」とは返さない**"
        },
    },
)
async def rebuild_namespace_embeddings(
    namespace: str,
    payload: EmbeddingRebuildRequest,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    blob: BlobDep,
    client: EmbeddingClientDep,
) -> EmbeddingRebuildView:
    """承認済み版の TTL から埋め込みを作り直す。**`maintainer` が必要**。

    **承認の経路からは呼ばない**(ADR-0050 決定4)。埋め込みは射影なので、
    作成の失敗が承認を止めてはいけない(不変条件3 と同じ向き)。
    だから独立した口にしてある。

    **提案中の版からは作らない。** 検索に出た用語は「使ってよい語彙」として
    エージェントに渡るので、四眼原則を通っていない版の用語を出さない。

    **名前空間の行を丸ごと入れ替える。** 縮めた版(用語を廃止して消した版)を
    反映したときに、消えた用語が検索に残らないようにするため。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.MAINTAINER
    )
    # **退役した名前空間では作り直さない**(ADR-0032 決定5 と同じ形)。
    # 版を作れない名前空間の検索索引を作る意味が無く、費用だけが発生する。
    try:
        await ensure_not_retired(session, namespace=namespace, doing="埋め込みの作り直し")
    except NamespaceRetiredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    try:
        outcome = await rebuild_embeddings(
            session, blob=blob, settings=settings, namespace=namespace, client=client
        )
    except SearchUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    actor = principal_id_of(principal)
    await AuditRepository(session).record(
        namespace=namespace,
        action="embeddings-rebuilt",
        actor=actor,
        actor_type=principal.actor_type,
        subject=f"{namespace}#embeddings@{outcome.version}",
        reason=f"{payload.reason} / 用語 {outcome.term_count} 件 / モデル: {outcome.model}",
    )
    await session.commit()

    logger.info(
        "埋め込みを作り直しました %s (actor=%s, 版=%s, 用語=%d 件, 切り詰め=%d 件)",
        namespace,
        actor,
        outcome.version,
        outcome.term_count,
        len(outcome.truncated_terms),
    )
    return EmbeddingRebuildView(
        namespace=outcome.namespace,
        version=outcome.version,
        model=outcome.model,
        term_count=outcome.term_count,
        truncated_terms=outcome.truncated_terms,
        deprecated_terms=outcome.deprecated_terms,
    )
