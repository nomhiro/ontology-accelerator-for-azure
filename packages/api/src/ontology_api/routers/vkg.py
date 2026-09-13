"""仮想グラフ(Ontop VKG)— R2RML マッピングの管理と照会(ADR-0046、`P3-01`)。

## 宛先を分ける。跨ぐ結合はしない

オントロジーへの照会は `/namespaces/{ns}/sparql`(Fuseki)、実データへの
照会は `/namespaces/{ns}/scan-sources/{source}/sparql`(Ontop)である。
**1 つのクエリが両方に跨ることはない**(決定1)。

跨ぐ結合を実装するには分散クエリのエンジンが必要で、
[ADR-0001](../../../../../docs/adr/0001-rdf-store-selection.md) は SPARQL の
実装を自作しないと決めている。`SERVICE` 句で繋ぐ選択は SSRF 対策として
封じてある(`sparql/guards.py`)。

**跨げないことの実害は小さい。** 実測で、Ontop は `--ontology` で渡した
TBox を使って推論する(`rdfs:subClassOf` の上位クラスで問い合わせると
インスタンスが返る)。つまり**語彙の意味は仮想グラフ側でも効く**。
効かないのは `rdfs:label` や `owl:deprecated` のような**語彙そのものに
ついての主張**で、それは Fuseki 側の担当である。

## 宛先を間違えると 0 件が返る

実測で、Ontop は TBox のトリプルを**データとして返さない**
(`?s a owl:Class` は 0 件、`rdfs:label` も 0 件)。

**つまり「問える範囲の外」は例外ではなく空の結果として現れる。**
これは「該当する行が無い」と区別できない。だから

- 宛先を URL で分け、どちらに聞いているかを呼び出し側に決めさせる
- **マッピングが無いソースへの照会は 404 で断る**(空を返さない)
- **エンドポイントが未設定なら 503 で断る**(空を返さない)

## 相手のエラー本文を通さない

実測で、Ontop は参照する関係が無いとき
`Cannot find relation ... (available choices: [...])` を返し、
**接続ユーザから見える全関係を列挙する**(こちらの制御平面の表も含む)。
`VirtualGraphClient` がログにだけ出し、応答にはステータスまでしか
載せない(ADR-0046 決定10)。

## アクセスログには残らない(`P3-08`)

**この経路が返すのは顧客の実データである。** それにもかかわらず、
`access_events`(`P2B-05`、ADR-0018)には記録していない。`access_events` の
行は「どの**版**の何を返したか」を持つ形で、仮想グラフには版が無い。
`default_graph_version` を `NULL` で埋めると「承認済み版が無かった」と
混ざる(ADR-0046 決定13)。

**アプリのログには出す。** 監査証跡ではないが、無記録でもない。
埋めるのは `P3-08` である。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import BlobDep, CurrentPrincipal, SessionDep, SettingsDep
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.scan import ScanRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.repositories.vkg import VkgMappingRepository
from ontology_api.services.authorization import (
    NamespaceRetiredError,
    PermissionDeniedError,
    ensure_not_retired,
    principal_id_of,
    require_namespace_role,
)
from ontology_api.services.vkg import MappingRejectedError, validate_mapping
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.models import NamespaceRole, VkgMapping, VkgMappingSummary
from ontology_core.r2rml import MAX_MAPPING_LENGTH, R2rmlError
from ontology_core.sparql.guards import QueryRejectedError, ensure_agent_safe_query, query_form
from ontology_core.sparql.limits import cap_bindings
from ontology_core.turtle import TURTLE_MEDIA_TYPE
from ontology_core.vkg import (
    VirtualGraphClient,
    VirtualGraphError,
    VirtualGraphNotConfiguredError,
    resolve_endpoint,
)

router = APIRouter(prefix="/namespaces", tags=["virtual-knowledge-graph"])
logger = logging.getLogger(__name__)

#: 切り詰めたことを伝えるヘッダ。**`sparql.py` と同じ名前を使う** —
#: 同じ意味のものに別の名前を付けると、クライアントが 2 つ覚える。
RESULT_TRUNCATED_HEADER = "X-Ontology-Result-Truncated"
RESULT_LIMIT_HEADER = "X-Ontology-Result-Limit"
RESULT_TOTAL_ROWS_HEADER = "X-Ontology-Result-Total-Rows"


class VkgMappingRevise(BaseModel):
    """R2RML マッピングの新しい改訂(ADR-0046 決定2)。"""

    content: str = Field(
        min_length=1,
        max_length=MAX_MAPPING_LENGTH,
        description="R2RML マッピングの Turtle。**`rr:sqlQuery` は受け付けない**"
        "(任意の SQL が安全かを判定する手段が無いため。ADR-0046 決定4)",
    )
    reason: str = Field(
        min_length=1,
        description="この改訂を入れる理由。**必須である** — "
        "マッピングは実データへの入口の定義なので、入口が変わった理由が"
        "残らないのは監査として成立しない",
    )


class VkgQueryRequest(BaseModel):
    """仮想グラフへの SPARQL クエリ。"""

    query: str = Field(
        min_length=1,
        max_length=100_000,
        examples=["SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20"],
    )


async def _prepare(
    session: SessionDep,
    *,
    namespace: str,
    principal: CurrentPrincipal,
    required: NamespaceRole,
) -> None:
    """名前空間名を検証し、存在と権限を確かめる(`scan.py` と同じ形)。"""
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


async def _resolve_source_id(session: SessionDep, *, namespace: str, source: str) -> int:
    """ソースの id を返す。無ければ 404。

    **id だけを返す。** `ScanSourceRow` を持ち回すと、途中で `rollback` が
    入ったときに `MissingGreenlet` になる(`scan_job.sweep_sources` で
    実際に踏んだ罠)。
    """
    row = await ScanRepository(session).get_source(namespace=namespace, name=source)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{namespace}' にソース '{source}' がありません",
        )
    return row.id


@router.post(
    "/{namespace}/scan-sources/{source}/vkg-mapping",
    status_code=status.HTTP_201_CREATED,
    summary="R2RML マッピングに新しい改訂を足す(`owner` が必要)",
)
async def revise_vkg_mapping(
    namespace: str,
    source: str,
    payload: VkgMappingRevise,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
) -> VkgMapping:
    """新しい改訂を足す。**`owner` が必要**(ADR-0046 決定2)。

    **`PUT` ではなく `POST` である。** 既存の改訂を置き換えるのではなく、
    新しい改訂を作る(改訂は不変)。

    **`owner` を要求する。** マッピングは**実データへの入口の定義**であり、
    ソースの登録(`scan.py` の `owner`)と同じ重さの行為である。
    `maintainer` に緩めると、ソースを登録できない主体が、登録済みの
    ソースから何を読むかを決められてしまう。

    **保存する前に検査する。** 検査を通らないマッピングを保存すると、
    **Ontop が起動しない改訂が有効な改訂として残る**(実測: 参照する
    関係が無いと起動に失敗する)。不正な本文は **422** である。
    """
    await _prepare(session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER)
    # **退役した名前空間ではマッピングを改訂しない**(ADR-0032 決定5)。
    # 版を作れない名前空間の実データへの入口を書き換える意味が無い。
    try:
        await ensure_not_retired(session, namespace=namespace, doing="R2RML マッピングの改訂")
    except NamespaceRetiredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    source_row = await ScanRepository(session).get_source(namespace=namespace, name=source)
    if source_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{namespace}' にソース '{source}' がありません",
        )

    try:
        validated = await validate_mapping(
            session,
            blob=blob,
            namespace=namespace,
            source=source_row,
            content=payload.content,
        )
    except (R2rmlError, MappingRejectedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    actor = principal_id_of(principal)
    created = await VkgMappingRepository(session).add(
        namespace=namespace,
        source_id=source_row.id,
        source=source,
        content=payload.content,
        triples_map_count=validated.mapping.triples_maps,
        tables=validated.mapping.tables,
        validated_against_version=validated.validated_against_version,
        actor=actor,
        reason=payload.reason,
    )

    # **照合した版を監査に残す。** `NULL` は「承認済み版が無かった」であり、
    # 後から「そのとき何と突き合わせたのか」を追えるようにする(決定12)。
    against = validated.validated_against_version or "(承認済み版なし)"
    note = (
        f"TriplesMap {created.triples_map_count} 件 / "
        f"読む関係: {', '.join(created.tables)} / 照合した版: {against}"
    )
    await AuditRepository(session).record(
        namespace=namespace,
        action="vkg-mapping-revised",
        actor=actor,
        actor_type=principal.actor_type,
        subject=f"{namespace}#vkg/{source}@{created.revision}",
        reason=f"{payload.reason} / {note}",
    )
    # **同一トランザクションで書く。** これがこのテーブルを PostgreSQL に
    # 置いた理由である(ADR-0046 決定2)。
    await session.commit()
    return created


@router.get(
    "/{namespace}/scan-sources/{source}/vkg-mapping",
    summary="有効な R2RML マッピングを返す(`data-analyst`)",
)
async def get_vkg_mapping(
    namespace: str,
    source: str,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> VkgMapping:
    """有効な改訂(最大の `revision`)を本文込みで返す。

    **マッピングが無ければ 404 にする。** 「登録していない」は事実だが、
    本文を返す口としては返すものが無い。**空の本文を返さない** —
    空の R2RML は「何も読まない有効なマッピング」と区別できない。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    source_id = await _resolve_source_id(session, namespace=namespace, source=source)
    mapping = await VkgMappingRepository(session).active(source_id=source_id, source=source)
    if mapping is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ソース '{source}' には R2RML マッピングが登録されていません",
        )
    return mapping


@router.get(
    "/{namespace}/scan-sources/{source}/vkg-mapping.ttl",
    summary="有効な R2RML マッピングを Turtle で返す(`data-analyst`)",
    response_class=Response,
    responses={200: {"content": {"text/turtle": {}}}},
)
async def get_vkg_mapping_turtle(
    namespace: str,
    source: str,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> Response:
    """有効な改訂の本文を `text/turtle` で返す。

    **Ontop に渡すためにある。** Ontop は `--mapping <ファイル>` しか
    受け付けないので、起動する側がこの口から取ってファイルに落とす
    (デプロイ環境での配線は `P3-07`)。

    **`Content-Type` は `text/turtle` である。** JSON に包むと、取り出す
    側が毎回ほどくことになり、`jq -r` を挟んだ経路で**改行やエスケープが
    壊れる**(このリポジトリで何度も踏んだ形)。
    """
    mapping = await get_vkg_mapping(namespace, source, principal, session)
    # **ヘッダは返す `Response` に載せる。** 注入した `Response` に載せても
    # `Response` を返した時点で消える(`test_construct_api.py` の回帰)。
    return Response(
        content=mapping.content,
        media_type=TURTLE_MEDIA_TYPE,
        headers={"X-Ontology-Vkg-Revision": str(mapping.revision)},
    )


@router.get(
    "/{namespace}/scan-sources/{source}/vkg-mapping/revisions",
    summary="R2RML マッピングの改訂を一覧する(`data-analyst`)",
)
async def list_vkg_mapping_revisions(
    namespace: str,
    source: str,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> list[VkgMappingSummary]:
    """改訂の履歴を新しい順に返す(本文は含まない)。

    **実データへの入口がいつ・誰に・なぜ変えられたかの履歴である。**
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    source_id = await _resolve_source_id(session, namespace=namespace, source=source)
    return await VkgMappingRepository(session).revisions(source_id=source_id, source=source)


@router.post(
    "/{namespace}/scan-sources/{source}/sparql",
    summary="仮想グラフに SPARQL で問い合わせる(`data-analyst`)",
    # **`dict | Response` は Pydantic のフィールドにできないので応答の
    # モデル生成を切る**(`sparql.py` と同じ。切らないと import の時点で
    # `FastAPIError` になる)。代わりに応答の形を `responses` で明示する。
    response_model=None,
    responses={
        200: {
            "description": (
                "`SELECT` / `ASK` は SPARQL Results JSON、`CONSTRUCT` / `DESCRIBE` は Turtle"
            ),
            "content": {"application/sparql-results+json": {}, "text/turtle": {}},
        },
        404: {"description": "R2RML マッピングが登録されていない。**空を返さない**"},
        503: {"description": "`VKG_ENDPOINT_TEMPLATE` が未設定。**空を返さない**"},
    },
)
async def query_virtual_graph(
    namespace: str,
    source: str,
    payload: VkgQueryRequest,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
) -> dict[str, Any] | Response:
    """仮想グラフへクエリを渡し、結果を返す。**実データは実体化しない。**

    **読み取り専用である。** `ensure_agent_safe_query` が更新と `SERVICE` を
    弾く。加えて実測で、**Ontop 側も SPARQL Update を受け付けない**
    (415、`/update` 系の経路も無い) — 多層防御になっている。

    **マッピングが無ければ 404、エンドポイントが未設定なら 503 である。**
    どちらも空の結果を返さない(決定11)。空を返すと「該当する行が無い」と
    区別できず、設定漏れが**データが無いこと**として通る。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    # **退役した名前空間では 0 行を静かに返さない**(ADR-0032 決定5 と同じ形)。
    try:
        await ensure_not_retired(session, namespace=namespace, doing="仮想グラフへの SPARQL クエリ")
    except NamespaceRetiredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    source_id = await _resolve_source_id(session, namespace=namespace, source=source)
    mapping = await VkgMappingRepository(session).active(source_id=source_id, source=source)
    if mapping is None:
        # **空を返さない。** マッピングを登録していないソースは「読める実データが
        # 無い」のではなく「入口を定義していない」である。
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ソース '{source}' には R2RML マッピングが登録されていません。"
            "仮想グラフはマッピングが無いと存在しません",
        )

    try:
        ensure_agent_safe_query(payload.query, allow_service=settings.sparql_allow_service)
    except QueryRejectedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        endpoint = resolve_endpoint(
            settings.vkg_endpoint_template, namespace=namespace, source=source
        )
    except VirtualGraphNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    returns_rdf = query_form(payload.query).returns_rdf
    async with VirtualGraphClient(timeout_seconds=settings.vkg_query_timeout_seconds) as client:
        try:
            if returns_rdf:
                turtle = await client.construct(payload.query, endpoint=endpoint)
            else:
                results = await client.query(payload.query, endpoint=endpoint)
        except VirtualGraphError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    actor = principal_id_of(principal)
    if returns_rdf:
        # **トリプル数の上限はここでは当てない**(ADR-0046 決定14)。
        # `sparql.py` は 413 で断るために解析しているが、仮想グラフの
        # `CONSTRUCT` は顧客 DB の行数だけ膨らむので、**上限を当てるために
        # 全部を解析する**のが一番重い経路になる。エージェント向けの経路は
        # `SELECT` であり、そこには行数の上限が効いている。
        logger.info(
            "仮想グラフ %s/%s へ RDF のクエリ(actor=%s, 文字数=%d)",
            namespace,
            source,
            actor,
            len(turtle),
        )
        return Response(content=turtle, media_type=TURTLE_MEDIA_TYPE)

    capped = cap_bindings(results, settings.sparql_max_results)
    if capped.truncated:
        response.headers[RESULT_TRUNCATED_HEADER] = "true"
        response.headers[RESULT_LIMIT_HEADER] = str(settings.sparql_max_results)
        if capped.total_rows is not None:
            response.headers[RESULT_TOTAL_ROWS_HEADER] = str(capped.total_rows)

    # **アクセスログには残らない**(`P3-08`)。ここはアプリのログである —
    # 監査証跡ではないが、無記録でもない。
    logger.info(
        "仮想グラフ %s/%s へクエリ(actor=%s, 改訂=%d, 行数=%s, 切り詰め=%s)",
        namespace,
        source,
        actor,
        mapping.revision,
        capped.total_rows,
        capped.truncated,
    )
    return capped.payload
