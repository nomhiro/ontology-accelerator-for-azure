"""監査証跡の照会(`P2B-11`)と PROV-O での書き出し(`P2A-07` / `P2A-15`、
ADR-0026 / ADR-0027)。

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

## 2 つの表現を同じモジュールに置く

`/audit`(JSON)と `/provenance`(PROV-O の Turtle)は**同じ絞り込みを取る**
(ADR-0026 決定1)。別のモジュールに分けると、絞り込みを足したときに片方だけ
更新されて表現によって見える範囲が変わる。回帰テストで署名の一致も固定して
ある(`test_provenance_api.py`)。

**`/audit` と `/provenance` の間では内容交渉しない**(ADR-0026 の代替案)。
JSON には `cursor` が要るが RDF では意味が薄く、**同じ URL が表現によって
違うパラメータを取る**形になる。

**`/provenance` の中では内容交渉する**
([ADR-0036](../../../../../docs/adr/0036-jsonld-serialization.md)、`P2A-17`)。
`Accept: application/ld+json` で同じグラフが JSON-LD で返る。
**却下した理由が当たらない** — 同じグラフの 2 つの直列化はパラメータが
同じなので、「表現によって違うパラメータを取る」形にならない。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response, status

from ontology_api.dependencies import CurrentPrincipal, SessionDep
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import AuditRepository, VersionRepository
from ontology_api.services.authorization import PermissionDeniedError, require_namespace_role
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.jsonld import (
    JSONLD_MEDIA_TYPE,
    PROVENANCE_CONTEXT,
    prefers_jsonld,
    render_jsonld,
)
from ontology_core.models import AuditPage, NamespaceRole
from ontology_core.prov import provenance_graph, referenced_versions
from ontology_core.turtle import TURTLE_MEDIA_TYPE

router = APIRouter(prefix="/namespaces", tags=["audit"])


async def _authorize(
    session: SessionDep,
    *,
    namespace: str,
    principal: CurrentPrincipal,
) -> None:
    """名前空間名を検証し、存在と `data-analyst` 権限を確かめる。"""
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
    await _authorize(session, namespace=namespace, principal=principal)

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


@router.get(
    "/{namespace}/provenance",
    summary="監査証跡を PROV-O で書き出す(Turtle / JSON-LD)",
    response_class=Response,
    responses={
        200: {
            "content": {"text/turtle": {}, JSONLD_MEDIA_TYPE: {}},
            "description": "W3C PROV-O。既定は Turtle、"
            "`Accept: application/ld+json` で JSON-LD(ADR-0036)",
        }
    },
)
async def export_provenance(
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
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
    limit: Annotated[int, Query(description="書き出す件数の上限")] = AuditRepository.DEFAULT_LIMIT,
    # **`Annotated` で受ける。** 既定値の位置に `Header(...)` を書くと、
    # ハンドラを直接呼ぶテストで `Header` オブジェクトが値として流れ込む
    # (`Query` と同じ罠。FastAPI 経由なら解決されるので HTTP で叩く
    # テストだけでは気づけない)。
    accept: Annotated[
        str | None,
        Header(description="`application/ld+json` を明示すると JSON-LD で返る(ADR-0036)"),
    ] = None,
) -> Response:
    """監査証跡を W3C PROV-O として返す。`data-analyst` が必要。

    ADR-0006 決定3 が約束していた「PROV-O で表現する」の実装である
    (ADR-0026)。権限は `/audit` と同じ — **同じ情報を別の語彙で出すだけ**
    なので、ここだけ厳しくすると見せかけの制限になる。

    **`cursor` は受けない**(ADR-0026 決定1)。RDF は順序を持たないので、
    カーソルで切り出した断片を RDF として渡す意味が薄い。代わりに
    **切り詰めたことを Turtle の中に書く**(`ont:truncated`)。件数が多い
    名前空間は `since` / `until` で期間を区切って取る。

    **`prov:wasDerivedFrom` は、系譜が記録されている版にだけ出る**
    (ADR-0026 決定2 / ADR-0027 決定5)。`publish` に `base_version` を
    渡さなかった版は「何から編集したか分からない」ので辺が出ず、
    代わりに `ont:editedFromRecorded false` が出る。**承認の順序から派生を
    出すのは、測っていないことを標準語彙で主張することになる。**

    **既定は Turtle である**(ADR-0036 決定5)。`Accept: application/ld+json`
    を明示したときだけ JSON-LD になる。`application/json` では切り替わらず、
    解釈できない `Accept` でも 406 にはしない — 内容交渉は**足すだけ**にして
    既存のクライアントの振る舞いを変えない。

    **JSON-LD の `@context` は文書に埋め込む**(決定2)。この API のコンテキスト
    URL は認証が要る(= JSON-LD プロセッサから解決できない)うえ、デプロイごとに
    違う。埋め込めば文書が自己完結する。
    """
    await _authorize(session, namespace=namespace, principal=principal)

    try:
        page = await AuditRepository(session).query(
            namespace=namespace,
            action=action,
            actor=actor,
            subject=subject,
            since=since,
            until=until,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    # **系譜は参照されている版だけ引く**(ADR-0027 決定6)。名前空間の全版を
    # 引かない。対象の解析規則は `referenced_versions` に閉じてある — ここに
    # 書き写すと、片方だけ直したときに「実体は出るが系譜が出ない」という
    # 静かな不整合になる。
    versions = await VersionRepository(session).get_many(
        namespace, referenced_versions(page.events, namespace=namespace)
    )

    # **`next_cursor` の有無が「続きがあるか」である。** 件数が `limit`
    # ちょうどでも続きがあるとは限らないので、`len(events) == limit` で
    # 判定しない(リポジトリが `limit + 1` 件取って確かめている)。
    # **1 つのグラフから 2 つの直列化を出す**(ADR-0036 決定6)。経路ごとに
    # トリプルを組み立てると、片方だけ直したときに表現によって内容が違う
    # という静かな不整合になる。
    graph = provenance_graph(
        page.events,
        namespace=namespace,
        truncated=page.next_cursor is not None,
        exported_at=datetime.now(UTC),
        versions=versions,
    )
    if prefers_jsonld(accept):
        return Response(
            content=render_jsonld(graph, context=PROVENANCE_CONTEXT),
            media_type=JSONLD_MEDIA_TYPE,
        )
    serialized = graph.serialize(format="turtle")
    turtle = serialized if isinstance(serialized, str) else serialized.decode("utf-8")
    return Response(content=turtle, media_type=TURTLE_MEDIA_TYPE)
