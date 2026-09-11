"""領域間マッピング(ADR-0023、`P2B-10`)。

## 統合しない

[ADR-0009](../../../../../docs/adr/0009-ontology-operations.md) 決定8 は
「領域をまたぐ矛盾は統合せずマッピングする」と決めた。同 ADR は
`owl:equivalentClass` も候補に挙げていたが、**却下した**(ADR-0023 決定1)。
互いに素なクラスの下にある用語を `equivalentClass` で結ぶと**両方が充足不能
になる**ことを ELK で実測している。使えるのは SKOS の 5 つだけである。

## 相違を消さない

**逆向きは自動で作らない**(決定3)。代わりに両方向から見えるようにする。

- `GET .../mappings` — その名前空間が**張った**もの(outgoing)
- `GET .../mappings?direction=incoming` — 他の名前空間から**張られた**もの

**片側だけの主張は異常ではない**(`reciprocal=False`)。相手がまだ宣言して
いないだけである。**述語が食い違っていたら両方残して `disputed` で見せる**
(決定4) — 自動で片方に寄せる実装は、相違を消す実装である。

## 宣言は `owner`

マッピングは**相手の領域の意味について何かを主張する**行為である。
[ADR-0015](../../../../../docs/adr/0015-term-owners.md) が「マッピングの妥当性
について説明責任を負うのは張った側」と書いた責任がここに発生するので、
用語の責任者の付与(`maintainer`)より 1 段上に置く(決定5)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import BlobDep, CurrentPrincipal, SessionDep
from ontology_api.repositories.mappings import MappingRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.services.authorization import (
    NamespaceRetiredError,
    PermissionDeniedError,
    ensure_not_retired,
    require_namespace_role,
)
from ontology_api.services.mapping_targets import resolve_target_lifecycles
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.jsonld import (
    JSONLD_MEDIA_TYPE,
    MAPPING_CONTEXT,
    prefers_jsonld,
    render_jsonld,
)
from ontology_core.mapping import (
    MappingPredicate,
    MappingValidationError,
    mapping_graph,
    validate_mapping,
)
from ontology_core.models import Namespace, NamespaceRole, TermMapping
from ontology_core.turtle import TURTLE_MEDIA_TYPE

router = APIRouter(prefix="/namespaces", tags=["mappings"])


class MappingDirection(StrEnum):
    """どちら向きのマッピングを見るか(ADR-0023 決定3)。"""

    OUTGOING = "outgoing"
    INCOMING = "incoming"


class MappingDeclare(BaseModel):
    """マッピングの宣言(ADR-0023 決定1・5)。"""

    source_term: str = Field(
        description="始点の用語の絶対 IRI。**この名前空間の用語であるべきだが検査はしない**",
        examples=["https://example.com/ontology/sales#GoodCustomer"],
    )
    target_term: str = Field(
        description="終点の用語の絶対 IRI。**外部語彙でもよい**(SKOS、schema.org 等)",
        examples=["http://www.w3.org/2004/02/skos/core#Concept"],
    )
    predicate: str = Field(
        description="SKOS のマッピング述語。"
        "`exactMatch` / `closeMatch` / `broadMatch` / `narrowMatch` / `relatedMatch`。"
        "**`owl:equivalentClass` は使えません**(ADR-0023 決定1)",
        examples=[MappingPredicate.CLOSE_MATCH.value],
    )
    reason: str = Field(
        min_length=1,
        description="なぜ同じ(近い)と言えるのか。**必須である** — "
        "理由の無いマッピングはレビュー対象になりえない(ADR-0023 決定5)",
    )


async def _prepare(
    session: SessionDep,
    *,
    namespace: str,
    principal: CurrentPrincipal,
    required: NamespaceRole,
) -> Namespace:
    """名前空間名を検証し、存在と権限を確かめて名前空間を返す。

    `base_iri` が incoming の判定に必要なので、`term_owners.py` の `_prepare`
    と違って名前空間そのものを返す。
    """
    try:
        validate_namespace_name(namespace)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    found = await NamespaceRepository(session).get(namespace)
    if found is None:
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
    return found


@router.get("/{namespace}/mappings", summary="領域間マッピングを一覧する")
async def list_mappings(
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    direction: Annotated[
        MappingDirection,
        Query(
            description="`outgoing` はこの名前空間が張ったもの、"
            "`incoming` は他の名前空間からこの名前空間の用語へ張られたもの。"
            "**両方向から見えることが、逆向きを自動生成しない代償である**"
            "(ADR-0023 決定3)"
        ),
    ] = MappingDirection.OUTGOING,
) -> list[TermMapping]:
    """マッピングを返す。`data-analyst` で読める。

    各件に `reciprocal` と `disputed` が付く。**`reciprocal=False` は異常では
    ない** — 相手がまだ宣言していないだけである。

    **`incoming` は他の名前空間が張った主張である。** 自分の名前空間の用語に
    対して誰がどう主張しているかを知る経路で、これが無いと相手の主張に
    気づけない。

    **各件に終点の生死(`target_status`)が付く**(ADR-0030、`P2B-18`)。
    `deprecated` なら `target_successor` に張り替え先が入る。

    **`unknown` は「調べていない・調べられなかった」であって「問題なし」では
    ない。** 理由は `target_status_note` に入る — 特に**相手の名前空間を読む
    権限が無い場合もここに来る**(決定1)。権限の無い相手の用語の状態を
    推測しない。
    """
    return await _with_target_status(
        session,
        blob=blob,
        principal=principal,
        namespace=namespace,
        direction=direction,
    )


async def _with_target_status(
    session: SessionDep,
    *,
    blob: BlobDep,
    principal: CurrentPrincipal,
    namespace: str,
    direction: MappingDirection,
) -> list[TermMapping]:
    """権限を確かめ、マッピングを引いて終点の生死を付ける。

    **JSON の一覧と Turtle の書き出しで共有する**(ADR-0031 決定2)。
    片方だけ生死を付ける状態を作らないため — 表現によって見える情報が
    変わるのは、ADR-0026 決定1 が避けた形である。
    """
    found = await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    repository = MappingRepository(session)
    if direction is MappingDirection.INCOMING:
        mappings = await repository.incoming(namespace, base_iri=found.base_iri)
    else:
        mappings = await repository.outgoing(namespace)

    # **終点の生死を付ける**(ADR-0030)。名前空間ごとに 1 回だけ TTL を読む。
    lifecycles = await resolve_target_lifecycles(
        session, blob=blob, principal=principal, mappings=mappings
    )
    return [
        mapping.model_copy(
            update={
                "target_status": lifecycles[mapping.target_term].status.value,
                "target_successor": lifecycles[mapping.target_term].successor,
                "target_status_note": lifecycles[mapping.target_term].note,
            }
        )
        for mapping in mappings
    ]


@router.get(
    "/{namespace}/mappings/export",
    summary="領域間マッピングを Turtle で書き出す",
    response_class=Response,
    responses={200: {"content": {"text/turtle": {}}, "description": "SKOS + 独自語彙の Turtle"}},
)
async def export_mappings(
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    direction: Annotated[
        MappingDirection,
        Query(description="JSON の一覧と同じ。`outgoing` / `incoming`"),
    ] = MappingDirection.OUTGOING,
    # **`Annotated` で受ける**(既定値の位置に書くと `Header` オブジェクトが
    # 値として流れ込む。`Query` と同じ罠)。
    accept: Annotated[
        str | None,
        Header(description="`application/ld+json` を明示すると JSON-LD で返る(ADR-0036)"),
    ] = None,
) -> Response:
    """マッピングを RDF で返す(既定は `text/turtle`)。`data-analyst` で読める。

    **トリプルストアには射影していない**(ADR-0023 決定7、
    [ADR-0031](../../../../../docs/adr/0031-mapping-export.md) 決定1)。
    この口は「SPARQL だけを使うクライアントから見えない」を
    **「自分のストアへ読み込める」**に変えるためのものである。
    ADR-0026 の PROV-O 書き出しと同じ形である。

    **素の SKOS のトリプルと、記述ノードの両方が出る**(決定3)。
    素のトリプル(`<source> skos:closeMatch <target>`)はそのまま引けるが、
    **それだけを読むと `reason` と終点の生死は分からない** —
    記述ノード(`ont:Mapping`)に載っている(決定5)。

    **`ont:targetStatus` は `unknown` でも出る**(決定4)。省略すると
    「問題なし」と読まれる。

    **`Accept: application/ld+json` で JSON-LD になる**
    ([ADR-0036](../../../../../docs/adr/0036-jsonld-serialization.md)、
    `P2A-17`)。`/provenance` と**同じ規則**である — RDF を返す口を片方だけ
    交渉可能にすると、説明のつかない差になる。`@context` は文書に埋め込む。
    """
    mappings = await _with_target_status(
        session,
        blob=blob,
        principal=principal,
        namespace=namespace,
        direction=direction,
    )
    # **1 つのグラフから 2 つの直列化を出す**(ADR-0036 決定6)。
    graph = mapping_graph(mappings, exported_at=datetime.now(UTC))
    if prefers_jsonld(accept):
        return Response(
            content=render_jsonld(graph, context=MAPPING_CONTEXT),
            media_type=JSONLD_MEDIA_TYPE,
        )
    serialized = graph.serialize(format="turtle")
    turtle = serialized if isinstance(serialized, str) else serialized.decode("utf-8")
    return Response(content=turtle, media_type=TURTLE_MEDIA_TYPE)


@router.put(
    "/{namespace}/mappings",
    summary="領域間マッピングを宣言する(既存があれば付け替える。`owner` が必要)",
)
async def declare_mapping(
    namespace: str,
    payload: MappingDeclare,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> TermMapping:
    """マッピングを宣言する。**`owner` が必要**(ADR-0023 決定5)。

    **冪等である**(同じ用語ペアが既にあれば付け替える)。1 つの用語ペアに
    述語は 1 つなので、やり直しは付け替えであり重複エラーにする理由がない。

    **`owl:equivalentClass` は 422 で拒否する**(決定1)。互いに素なクラスの
    下にある用語を結ぶと両方が充足不能になり、しかも承認では止まらない
    (推論器は CI にしかいない。ADR-0021 決定5)。

    **用語の実在は検査しない**(決定6、ADR-0015 決定4 と同じ理由)。外部語彙
    へのマッピングが正当な主用途なので、実在を要求すると使えなくなる。

    **逆向きは作らない**(決定3)。相手側は相手が宣言する。
    """
    await _prepare(session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER)
    # **退役した領域から新しい主張はしない**(ADR-0032 決定5)。
    # 取り消し(`DELETE`)は片付けなので通す。
    try:
        await ensure_not_retired(session, namespace=namespace, doing="マッピングの宣言")
    except NamespaceRetiredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    try:
        source, target, predicate = validate_mapping(
            source_term=payload.source_term,
            target_term=payload.target_term,
            predicate=payload.predicate,
        )
    except MappingValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    actor = principal.object_id or principal.subject
    repository = MappingRepository(session)
    await repository.declare(
        namespace=namespace,
        source_term=source,
        target_term=target,
        predicate=predicate,
        reason=payload.reason,
        actor=actor,
    )
    await AuditRepository(session).record(
        namespace=namespace,
        action="mapping-declared",
        actor=actor,
        actor_type=principal.actor_type,
        subject=f"{source} {predicate.value} {target}",
        reason=payload.reason,
    )
    await session.commit()

    # 宣言した 1 件を、相手側との突き合わせ込みで返す。**`disputed` をここで
    # 返すのが要点** — 宣言した直後に「相手は違うことを言っている」と分かる。
    for mapping in await repository.outgoing(namespace):
        if mapping.source_term == source and mapping.target_term == target:
            return mapping
    raise HTTPException(  # pragma: no cover - 直前に書いた行が読めないことは無い
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="宣言したマッピングを読み出せませんでした",
    )


@router.delete(
    "/{namespace}/mappings",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="領域間マッピングを取り消す(`owner` が必要)",
)
async def revoke_mapping(
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    source_term: Annotated[str, Query(description="始点の用語の絶対 IRI")],
    target_term: Annotated[str, Query(description="終点の用語の絶対 IRI")],
) -> None:
    """マッピングを取り消す。**`owner` が必要**。

    **取り消せるのは自分が張ったものだけである**(決定3)。`incoming` の
    マッピングは相手の主張なので、こちらからは消せない — 消せてしまうと
    「相違が消されずに記録される」が成立しない。

    無ければ **404** にする。「取り消した」と「元から無かった」を混同すると、
    取り消しが効いたかどうかが分からない。
    """
    await _prepare(session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER)
    actor = principal.object_id or principal.subject
    repository = MappingRepository(session)
    if not await repository.revoke(
        namespace=namespace, source_term=source_term, target_term=target_term
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{source_term}' から '{target_term}' へのマッピングがありません",
        )
    await AuditRepository(session).record(
        namespace=namespace,
        action="mapping-revoked",
        actor=actor,
        actor_type=principal.actor_type,
        subject=f"{source_term} -> {target_term}",
    )
    await session.commit()
