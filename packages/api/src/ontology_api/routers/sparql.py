"""SPARQL の仲介。

外部に公開するのは**読み取りのみ**。トリプルストアは正本ではなく再構築可能な射影
であり、更新は正本に書いたうえで Core API 内部の射影処理が行う。したがって
SPARQL Update をここから公開することはない。

## 廃止の警告はヘッダに載せる(ADR-0017 決定3)

**本文の SPARQL Results JSON を書き換えない。** ADR-0001 は
「SPARQL 1.1 Protocol をハード境界にし、`SPARQL_ENDPOINT` を差し替えられる」
ことを設計原則にした。結果の JSON に独自のキーを混ぜると、標準の形を期待する
クライアントとの互換が崩れ、ストアを差し替えたときに挙動が変わる。

そのため `X-Ontology-Deprecated-Terms` ヘッダに載せる。**MCP は本文に載せる** —
エージェントはヘッダを見ないので、見えなければ廃止された用語を自信を持って
使ってしまう。同じ情報を、層ごとに違う場所に置く。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import CurrentPrincipal, SessionDep, SettingsDep, StoreDep
from ontology_api.services.authorization import PermissionDeniedError, require_namespace_role
from ontology_core.deprecation import deprecated_iris_in_results
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.models import NamespaceRole
from ontology_core.sparql.client import SparqlStore, SparqlStoreError
from ontology_core.sparql.guards import QueryRejectedError, ensure_agent_safe_query

router = APIRouter(prefix="/namespaces/{namespace}/sparql", tags=["sparql"])
logger = logging.getLogger(__name__)

#: 結果に現れた廃止済み用語を載せるヘッダ(ADR-0017 決定3)。
DEPRECATED_TERMS_HEADER = "X-Ontology-Deprecated-Terms"

#: 廃止された用語を引くクエリ。**`GRAPH` 句を書かない**ので、既定グラフ
#: (= 承認済みの現行版。ADR-0010 決定5)だけを見る。エージェントが見るのと
#: 同じ範囲で判定しなければ、警告と結果が食い違う。
_DEPRECATED_QUERY = (
    "SELECT DISTINCT ?s WHERE { ?s <http://www.w3.org/2002/07/owl#deprecated> true }"
)


class SparqlQueryRequest(BaseModel):
    """SPARQL クエリの実行要求。"""

    query: str = Field(
        min_length=1,
        max_length=100_000,
        examples=["SELECT ?class WHERE { ?class a owl:Class } LIMIT 20"],
    )


async def deprecated_terms_in_dataset(store: SparqlStore, *, namespace: str) -> frozenset[str]:
    """この名前空間で廃止されている用語の IRI を返す。

    **失敗しても空集合を返す。** 警告のために本来の応答を壊してはいけない
    (ADR-0017)。廃止の警告が出ないのは劣化だが、クエリそのものが失敗するのは
    回帰である。
    """
    try:
        result = await store.query(_DEPRECATED_QUERY, dataset=namespace)
    except SparqlStoreError:
        logger.warning(
            "名前空間 '%s' の廃止済み用語を取得できませんでした。警告なしで応答します",
            namespace,
        )
        return frozenset()
    bindings = result.get("results", {}).get("bindings", []) if isinstance(result, dict) else []
    found: set[str] = set()
    for row in bindings:
        if not isinstance(row, dict):
            continue
        cell = row.get("s")
        if isinstance(cell, dict) and cell.get("type") == "uri":
            value = cell.get("value")
            if isinstance(value, str):
                found.add(value)
    return frozenset(found)


@router.post("", summary="読み取り専用の SPARQL クエリを実行する")
async def run_query(
    namespace: str,
    payload: SparqlQueryRequest,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    store: StoreDep,
    response: Response,
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

    **結果に廃止済みの用語が現れたら `X-Ontology-Deprecated-Terms` ヘッダに
    載せる**(`P2B-03`、ADR-0017 決定3)。本文は標準の SPARQL Results JSON の
    ままにする。
    """
    try:
        validate_namespace_name(namespace)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # 読み取りにも権限が必要(ADR-0014 決定2: `data-analyst` 以上)。
    # **エージェント経路もここを通る。** MCP は呼び出し元のトークンを転送する
    # ので(ADR-0012)、エージェントの識別子でこの判定が効く。
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
        ensure_agent_safe_query(payload.query, allow_service=settings.sparql_allow_service)
    except QueryRejectedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        results = await store.query(payload.query, dataset=namespace)
    except SparqlStoreError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    deprecated = await deprecated_terms_in_dataset(store, namespace=namespace)
    found = deprecated_iris_in_results(results, deprecated)
    if found:
        # ヘッダの値は ASCII に限られる。IRI は ASCII なのでそのまま並べる。
        response.headers[DEPRECATED_TERMS_HEADER] = ", ".join(found)
    return results
