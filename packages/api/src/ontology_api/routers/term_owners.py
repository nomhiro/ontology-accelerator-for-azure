"""用語単位の責任者(ADR-0015、`P2B-04`)。

## 責任者は権限ではない

`namespace_roles`(ADR-0014)は「何ができるか」、責任者は「誰が説明責任を
負うか」である。責任者を持っていても、それだけでは何の操作も許されない。

## 用語 IRI をパスに載せない

用語 IRI は `/` と `#` を含むので、パスパラメータにすると必ずエスケープの
問題になる。書き込みは本文、読み取りはクエリパラメータで渡す。
**個人情報はクエリに載せない**という規律には反しない — 用語 IRI は
オントロジーの語彙であって個人情報ではない。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import CurrentPrincipal, SessionDep
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.term_owners import TermOwnerRepository
from ontology_api.services.authorization import (
    PermissionDeniedError,
    principal_id_of,
    require_namespace_role,
)
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.iri import TermIriError, validate_term_iri
from ontology_core.models import NamespaceRole, OwnerResolution, TermOwner

router = APIRouter(prefix="/namespaces", tags=["term-owners"])


class TermOwnerAssign(BaseModel):
    """責任者の割り当て要求(ADR-0015 決定1)。"""

    term_iri: str = Field(
        description="責任の対象となる用語の絶対 IRI。`base_iri` 配下に限らない"
        "(外部語彙へのマッピングの責任者も記録できるようにするため)",
        examples=["https://example.com/ontology/retail#Product"],
    )
    principal_id: str = Field(
        description="Entra のオブジェクト ID。UPN や表示名ではない",
        examples=["00000000-0000-0000-0000-000000000000"],
    )


async def _prepare(
    session: SessionDep,
    *,
    namespace: str,
    principal: CurrentPrincipal,
    required: NamespaceRole,
) -> None:
    """名前空間名を検証し、存在と権限を確かめる。

    3 つのハンドラで同じ前処理を書くと、**足す操作を追加したときに権限
    チェックを書き忘れる**(`versions.py` の `_require` と同じ理由)。
    """
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


def _validated_iri(term_iri: str) -> str:
    try:
        return validate_term_iri(term_iri)
    except TermIriError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.get("/{namespace}/term-owners", summary="用語の責任者を一覧する")
async def list_term_owners(
    namespace: str, principal: CurrentPrincipal, session: SessionDep
) -> list[TermOwner]:
    """責任者の一覧を返す。`data-analyst` で読める(ADR-0015 決定5)。

    **`namespace_roles` の一覧とは要求するロールが違う。** あちらは `owner`
    を要求する(誰がどの権限を持っているかは管理する立場の人が知るべき情報)。
    責任者は逆で、「この用語について誰に聞けばよいか」を知ることが主目的
    なので、分析者が引けなければ存在する意味がない。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    return await TermOwnerRepository(session).list_for(namespace)


@router.get("/{namespace}/term-owners/resolve", summary="この用語の問い合わせ先を解決する")
async def resolve_term_owner(
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    term_iri: Annotated[
        str,
        Query(
            description="解決したい用語の絶対 IRI",
            examples=["https://example.com/ontology/retail#Product"],
        ),
    ],
) -> OwnerResolution:
    """「この用語は誰に聞けばよいか」を返す(ADR-0015 決定2)。

    用語の責任者 → 名前空間の `owner` → 解決不能 の順に落ちる。**何で
    解決したかを `source` で返す。** 代替で解決したことが見えなければ、
    健全性指標が「責任者が未設定の用語」を数えられない。

    **責任者が未設定でも 404 にしない。** 「その用語に責任者がいない」は
    エラーではなく、この API が答えるべき事実である。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    return await TermOwnerRepository(session).resolve(
        namespace=namespace, term_iri=_validated_iri(term_iri)
    )


@router.put(
    "/{namespace}/term-owners",
    summary="用語の責任者を割り当てる(既存があれば付け替える)",
)
async def assign_term_owner(
    namespace: str,
    payload: TermOwnerAssign,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> TermOwner:
    """責任者を割り当てる。`maintainer` が必要(ADR-0015 決定5)。

    **冪等である**(既にあれば付け替える)。1 つの用語に責任者は 1 人なので、
    やり直しは付け替えであり重複エラーにする理由がない。

    **用語が実在するかは検査しない**(ADR-0015 決定4)。ストアは再構築可能な
    射影であって正本ではないため、存在確認は正本への書き込みを射影の可用性に
    依存させる。まだ承認されていない版で定義される用語に先に責任者を決めて
    おくことも自然に起こる。

    **責任者がその名前空間のロールを持っているかも検査しない。** 退職して
    RBAC から外れた人が責任者のまま残る状態は起こる。それは書き込みを拒否
    して防ぐものではなく、健全性指標(`P2B-06`)が可視化するものである。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.MAINTAINER
    )
    return await TermOwnerRepository(session).assign(
        namespace=namespace,
        term_iri=_validated_iri(payload.term_iri),
        principal_id=payload.principal_id,
        assigned_by=principal_id_of(principal),
    )


@router.delete(
    "/{namespace}/term-owners",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="用語の責任者を外す",
)
async def unassign_term_owner(
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    term_iri: Annotated[str, Query(description="責任者を外す用語の絶対 IRI")],
) -> None:
    """責任者を外す。`maintainer` が必要(ADR-0015 決定5)。

    **無かった場合は 404 を返す。** 「外した」と「もともと無かった」を同じ
    204 にすると、IRI のタイプミスに気づけない(用語の実在を検査しない
    設計なので、タイプミスは割り当て時には分からない)。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.MAINTAINER
    )
    removed = await TermOwnerRepository(session).unassign(
        namespace=namespace, term_iri=_validated_iri(term_iri)
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{namespace}' の用語 '{term_iri}' に責任者は設定されていません",
        )
