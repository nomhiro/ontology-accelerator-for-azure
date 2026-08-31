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
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
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

    `namespace` は `FusekiStore._resolve` が `{dataset}` へそのまま埋め込む
    (`ontology_core.sparql.client.FusekiStore._resolve` を参照)。ここで検証
    しないと `../ds` のような値が URL の `..` セグメントとして正規化され、
    予約データセット `ds` や他の名前空間へ到達できてしまう。名前空間名は
    Fuseki のデータセット名・グラフ IRI に使うセキュリティ境界であり
    (`packages/api/tests/test_isolation.py` が実証している境界そのもの)、
    パスパラメータとして受け取る入口では必ず検証する。
    """
    del principal  # Phase 2 で名前空間ごとの認可に使う

    try:
        validate_namespace_name(namespace)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        ensure_agent_safe_query(payload.query, allow_service=settings.sparql_allow_service)
    except QueryRejectedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        return await store.query(payload.query, dataset=namespace)
    except SparqlStoreError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
