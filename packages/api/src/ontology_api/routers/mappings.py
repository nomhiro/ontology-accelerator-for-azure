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

from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import CurrentPrincipal, SessionDep
from ontology_api.repositories.mappings import MappingRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.services.authorization import (
    PermissionDeniedError,
    require_namespace_role,
)
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.mapping import (
    MappingPredicate,
    MappingValidationError,
    validate_mapping,
)
from ontology_core.models import Namespace, NamespaceRole, TermMapping

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
    """
    found = await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    repository = MappingRepository(session)
    if direction is MappingDirection.INCOMING:
        return await repository.incoming(namespace, base_iri=found.base_iri)
    return await repository.outgoing(namespace)


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
        subject=f"{source_term} -> {target_term}",
    )
    await session.commit()
