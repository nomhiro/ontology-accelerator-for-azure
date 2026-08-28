"""SPARQL の仲介。

外部に公開するのは**読み取りのみ**。トリプルストアは正本ではなく再構築可能な射影
であり、更新は正本に書いたうえで Core API 内部の射影処理が行う。したがって
SPARQL Update をここから公開することはない。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import CurrentPrincipal, SettingsDep, StoreDep
from ontology_core.sparql.client import SparqlStoreError
from ontology_core.sparql.guards import QueryRejectedError, ensure_agent_safe_query

router = APIRouter(prefix="/namespaces/{namespace}/sparql", tags=["sparql"])


class SparqlQueryRequest(BaseModel):
    """SPARQL クエリの実行要求。"""

    query: str = Field(
        min_length=1,
        max_length=100_000,
        examples=["SELECT ?class WHERE { ?class a owl:Class } LIMIT 20"],
    )


@router.post("", summary="読み取り専用の SPARQL クエリを実行する")
async def run_query(
    namespace: str,
    payload: SparqlQueryRequest,
    principal: CurrentPrincipal,
    settings: SettingsDep,
    store: StoreDep,
) -> dict[str, Any]:
    """クエリを検査してからストアへ渡し、SPARQL Results JSON を返す。

    ガードは多層防御の外側であり、権威ある制御はストア側の設定
    (`containers/fuseki/config.ttl` の `SERVICE` 無効化)にある。
    """
    del principal  # Phase 2 で名前空間ごとの認可に使う

    try:
        ensure_agent_safe_query(payload.query, allow_service=settings.sparql_allow_service)
    except QueryRejectedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        return await store.query(payload.query, dataset=namespace)
    except SparqlStoreError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
