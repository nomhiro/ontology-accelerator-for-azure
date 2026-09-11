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
from ontology_api.repositories.access import AccessRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_api.services.authorization import (
    NamespaceRetiredError,
    PermissionDeniedError,
    ensure_not_retired,
    principal_id_of,
    require_namespace_role,
)
from ontology_core.access import build_access_record
from ontology_core.deprecation import deprecated_iris_in_results
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.models import NamespaceRole, OntologyVersionStatus
from ontology_core.sparql.client import SparqlStore, SparqlStoreError
from ontology_core.sparql.guards import QueryRejectedError, ensure_agent_safe_query
from ontology_core.sparql.limits import cap_bindings

router = APIRouter(prefix="/namespaces/{namespace}/sparql", tags=["sparql"])
logger = logging.getLogger(__name__)

#: 結果に現れた廃止済み用語を載せるヘッダ(ADR-0017 決定3)。
DEPRECATED_TERMS_HEADER = "X-Ontology-Deprecated-Terms"

# 結果を切り詰めたことを伝えるヘッダ(`P2A-08`、ADR-0025 決定4)。
#
# **本文は標準の SPARQL Results JSON のままにする**(ADR-0001 の
# 「SPARQL 1.1 Protocol をハード境界にする」)。エージェント向けには MCP が
# 本文に載せ替える — **エージェントはヘッダを見ない**(ADR-0017 決定3 と
# 同じ理由・同じ形)。
RESULT_TRUNCATED_HEADER = "X-Ontology-Result-Truncated"
RESULT_LIMIT_HEADER = "X-Ontology-Result-Limit"
RESULT_TOTAL_ROWS_HEADER = "X-Ontology-Result-Total-Rows"

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


async def _record_access(
    session: SessionDep,
    *,
    namespace: str,
    actor: str,
    query: str,
    results: dict[str, Any],
) -> None:
    """アクセスログを記録する(`P2B-05`、ADR-0018)。

    **失敗してもクエリを失敗させない**(決定3)。不変条件3(射影の失敗は正本への
    書き込みを失敗させない)と同じ向きの判断である — アクセスログは読み取りの
    副産物であって、読み取りの前提条件ではない。

    **「記録できなかった」を黙って無かったことにはしない。** 警告としてログに
    残す。
    """
    try:
        ns = await NamespaceRepository(session).get(namespace)
        if ns is None:
            # 権限判定を通っている以上ここには来ないが、来たら記録しない。
            return
        current = next(
            (
                v
                for v in await VersionRepository(session).list_for(namespace)
                if v.status is OntologyVersionStatus.APPROVED
            ),
            None,
        )
        record = build_access_record(
            namespace=namespace,
            actor=actor,
            query=query,
            results=results,
            base_iri=ns.base_iri,
            default_graph_version=None if current is None else current.version,
        )
        await AccessRepository(session).record(record)
    except Exception:
        logger.exception(
            "名前空間 '%s' のアクセスログを記録できませんでした。応答はそのまま返します",
            namespace,
        )


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

    **アクセスログを記録する**(`P2B-05`、ADR-0018)。ADR-0006 決定4 が言う
    「エージェントへ提供したコンテキスト」はこの経路である。**記録に失敗しても
    クエリは失敗させない**(読み取りの副産物であって前提条件ではない)。

    **推論は行わない**(ADR-0028)。ストアには承認された TTL がそのまま載って
    いるだけで、OWL の含意は展開されていない。`A ⊑ B ⊑ C` を主張しても
    `?s rdfs:subClassOf C` は `A` を返さない。階層を辿るには
    **プロパティパス**(`rdfs:subClassOf+`、`rdf:type/rdfs:subClassOf*`)を
    使う。パスは推論ではなくグラフの到達可能性なので、
    **返る経路はすべて誰かが承認した公理である**。
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

    # **退役した名前空間では 0 行を静かに返さない**(ADR-0032 決定5)。
    # `retire` はデータセットを消すので、通すとクエリは空の結果を返す。
    # **エージェントはそれを「該当なし」と読んで回答を作る。**
    try:
        await ensure_not_retired(session, namespace=namespace, doing="SPARQL クエリ")
    except NamespaceRetiredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    try:
        ensure_agent_safe_query(payload.query, allow_service=settings.sparql_allow_service)
    except QueryRejectedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        results = await store.query(payload.query, dataset=namespace)
    except SparqlStoreError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # ---- 結果件数の上限(P2A-08、ADR-0025) ----
    #
    # **ストア側では止められないので、ここが唯一の強制点である。**
    # `SPARQL_MAX_RESULTS` は長らく「保持しているだけ」で効いていなかった。
    #
    # **切り詰めたことを必ず見せる**(決定3)。切り詰めた結果を「全部です」と
    # して返すと、エージェントは「該当は N 件で全部見た」と信じて回答を作る。
    #
    # **エラーにはしない**(決定5)。エージェントは「全部は取れなかった」ことを
    # 知ったうえで続けられるべきである。
    capped = cap_bindings(results, settings.sparql_max_results)
    if capped.truncated:
        response.headers[RESULT_TRUNCATED_HEADER] = "true"
        response.headers[RESULT_LIMIT_HEADER] = str(settings.sparql_max_results)
        if capped.total_rows is not None:
            response.headers[RESULT_TOTAL_ROWS_HEADER] = str(capped.total_rows)

    deprecated = await deprecated_terms_in_dataset(store, namespace=namespace)
    # **廃止の検査は切り詰めた後の結果に対して行う。** 返していない行の用語を
    # 警告しても、受け取った側には対応する行が無い。
    found = deprecated_iris_in_results(capped.payload, deprecated)
    if found:
        # ヘッダの値は ASCII に限られる。IRI は ASCII なのでそのまま並べる。
        response.headers[DEPRECATED_TERMS_HEADER] = ", ".join(found)

    # **コンテキストを渡した記録を残す**(P2B-05、ADR-0018 決定4)。
    # ADR-0006 決定4 が言う「コンテキスト」はこの経路である。
    #
    # **切り詰める前の行数を記録する**(ADR-0025 決定6)。「エージェントに何行
    # 渡したか」ではなく「**何行返ろうとしたか**」でなければ、上限に張り付いて
    # いるクエリを見つけられない。そのため `capped.payload` ではなく `results`
    # を渡す。
    await _record_access(
        session,
        namespace=namespace,
        actor=principal_id_of(principal),
        query=payload.query,
        results=results,
    )
    return capped.payload
