"""用語単位の責任者のテスト(P2B-04、ADR-0015)。

**中心にあるのは決定2 のフォールバックである。** 責任者がいない用語の
問い合わせを名前空間の `owner` に回す一方、**回したことを隠さない**。
隠すと `P2B-06` の健全性指標が「責任者が未設定の用語」を数えられない。

ADR-0014 決定6 は「権限に暗黙のフォールバックを作らない」と決めたので、
ここでフォールバックを作るのは一見矛盾する。矛盾しない理由は
**安全側の向きが逆だから**である(権限は「無いなら拒否」、ルーティングは
「無いなら上位に回す」)。その非対称性をテストで固定する。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.term_owners import TermOwnerRepository
from ontology_api.routers.term_owners import (
    TermOwnerAssign,
    assign_term_owner,
    list_term_owners,
    resolve_term_owner,
    unassign_term_owner,
)
from ontology_core.auth.entra import Principal
from ontology_core.models import NamespaceRole, OwnerResolutionSource, PlatformRole

_NS = "owners-ns"
_TERM = "https://e.example/#Product"
_OTHER_TERM = "https://e.example/#Customer"
_EXTERNAL_TERM = "http://www.w3.org/2004/02/skos/core#Concept"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_OWNER = Principal(subject="owner", object_id="owner-oid")
_OWNER2 = Principal(subject="owner2", object_id="aaa-owner2-oid")
_MAINTAINER = Principal(subject="maintainer", object_id="maintainer-oid")
_STEWARD = Principal(subject="steward", object_id="steward-oid")
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")
_EXPERT = Principal(subject="expert", object_id="expert-oid")


async def _make_namespace(session: AsyncSession, *, with_owner: bool = True) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri="https://e.example/#",
        created_by=_ADMIN.object_id,
    )
    repo = RoleRepository(session)
    grants = [
        (_MAINTAINER, NamespaceRole.MAINTAINER),
        (_STEWARD, NamespaceRole.DATA_STEWARD),
        (_ANALYST, NamespaceRole.DATA_ANALYST),
    ]
    if with_owner:
        grants += [(_OWNER, NamespaceRole.OWNER), (_OWNER2, NamespaceRole.OWNER)]
    for principal, role in grants:
        await repo.grant(
            namespace=_NS,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await session.commit()


# ---------------------------------------------------------------- 割り当て


@pytest.mark.integration
async def test_maintainer_は責任者を割り当てられる(session: AsyncSession) -> None:
    await _make_namespace(session)
    assigned = await assign_term_owner(
        namespace=_NS,
        payload=TermOwnerAssign(term_iri=_TERM, principal_id=_EXPERT.object_id),
        principal=_MAINTAINER,
        session=session,
    )
    assert assigned.term_iri == _TERM
    assert assigned.principal_id == _EXPERT.object_id
    assert assigned.assigned_by == _MAINTAINER.object_id


@pytest.mark.integration
async def test_data_steward_は割り当てられない(session: AsyncSession) -> None:
    """**`data-steward` に許すと、自分の提案の責任者を自分で他人にできる。**"""
    await _make_namespace(session)
    with pytest.raises(HTTPException) as exc:
        await assign_term_owner(
            namespace=_NS,
            payload=TermOwnerAssign(term_iri=_TERM, principal_id=_EXPERT.object_id),
            principal=_STEWARD,
            session=session,
        )
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_割り当ては冪等で付け替えになる(session: AsyncSession) -> None:
    """1 つの用語に責任者は 1 人(ADR-0015 決定1)。やり直しは付け替えである。"""
    await _make_namespace(session)
    for target in (_EXPERT, _STEWARD, _EXPERT):
        await assign_term_owner(
            namespace=_NS,
            payload=TermOwnerAssign(term_iri=_TERM, principal_id=target.object_id),
            principal=_MAINTAINER,
            session=session,
        )
    owners = await list_term_owners(namespace=_NS, principal=_ANALYST, session=session)
    assert len(owners) == 1, "重複した行が増えてはいけない"
    assert owners[0].principal_id == _EXPERT.object_id


@pytest.mark.integration
async def test_名前空間のロールを持たない主体も責任者にできる(session: AsyncSession) -> None:
    """ADR-0015 決定4。**退職して RBAC から外れた人が責任者のまま残る状態は
    起こる。** 書き込みを拒否して防ぐものではなく、健全性指標が可視化する。
    拒否すると「責任者を決めてからロールを付ける」順序が動かなくなる。
    """
    await _make_namespace(session)
    assigned = await assign_term_owner(
        namespace=_NS,
        payload=TermOwnerAssign(term_iri=_TERM, principal_id=_STRANGER.object_id),
        principal=_MAINTAINER,
        session=session,
    )
    assert assigned.principal_id == _STRANGER.object_id


@pytest.mark.integration
async def test_外部語彙の_IRI_にも責任者を置ける(session: AsyncSession) -> None:
    """ADR-0015 決定3。外部語彙へのマッピング(ADR-0009 決定8)の妥当性に
    ついて説明責任を負うのは張った側である。"""
    await _make_namespace(session)
    assigned = await assign_term_owner(
        namespace=_NS,
        payload=TermOwnerAssign(term_iri=_EXTERNAL_TERM, principal_id=_EXPERT.object_id),
        principal=_MAINTAINER,
        session=session,
    )
    assert assigned.term_iri == _EXTERNAL_TERM


@pytest.mark.integration
async def test_絶対_IRI_でなければ_422(session: AsyncSession) -> None:
    await _make_namespace(session)
    with pytest.raises(HTTPException) as exc:
        await assign_term_owner(
            namespace=_NS,
            payload=TermOwnerAssign(term_iri="Product", principal_id=_EXPERT.object_id),
            principal=_MAINTAINER,
            session=session,
        )
    assert exc.value.status_code == 422


@pytest.mark.integration
async def test_存在しない名前空間は_404(session: AsyncSession) -> None:
    """**403 ではなく 404 にする。** 名前空間が無いのに「権限がありません」と
    言うと、運用者はロールの付与を疑って時間を失う。"""
    await _make_namespace(session)
    with pytest.raises(HTTPException) as exc:
        await assign_term_owner(
            namespace="no-such-ns",
            payload=TermOwnerAssign(term_iri=_TERM, principal_id=_EXPERT.object_id),
            principal=_ADMIN,
            session=session,
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------- 一覧


@pytest.mark.integration
async def test_data_analyst_が一覧を読める(session: AsyncSession) -> None:
    """**`namespace_roles` の一覧とは要求するロールが違う**(あちらは `owner`)。
    責任者は「誰に聞けばよいか」を知るためのものなので、分析者が引けなければ
    存在する意味がない(ADR-0015 決定5)。"""
    await _make_namespace(session)
    await assign_term_owner(
        namespace=_NS,
        payload=TermOwnerAssign(term_iri=_TERM, principal_id=_EXPERT.object_id),
        principal=_MAINTAINER,
        session=session,
    )
    owners = await list_term_owners(namespace=_NS, principal=_ANALYST, session=session)
    assert [o.term_iri for o in owners] == [_TERM]


@pytest.mark.integration
async def test_ロールを持たない主体は一覧を読めない(session: AsyncSession) -> None:
    await _make_namespace(session)
    with pytest.raises(HTTPException) as exc:
        await list_term_owners(namespace=_NS, principal=_STRANGER, session=session)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_一覧は用語_IRI_で安定して並ぶ(session: AsyncSession) -> None:
    await _make_namespace(session)
    for term in (_TERM, _EXTERNAL_TERM, _OTHER_TERM):
        await assign_term_owner(
            namespace=_NS,
            payload=TermOwnerAssign(term_iri=term, principal_id=_EXPERT.object_id),
            principal=_MAINTAINER,
            session=session,
        )
    owners = await list_term_owners(namespace=_NS, principal=_ANALYST, session=session)
    assert [o.term_iri for o in owners] == sorted([_TERM, _EXTERNAL_TERM, _OTHER_TERM])


# ---------------------------------------------------------------- 解決


@pytest.mark.integration
async def test_責任者がいれば_その人を返す(session: AsyncSession) -> None:
    await _make_namespace(session)
    await assign_term_owner(
        namespace=_NS,
        payload=TermOwnerAssign(term_iri=_TERM, principal_id=_EXPERT.object_id),
        principal=_MAINTAINER,
        session=session,
    )
    resolved = await resolve_term_owner(
        namespace=_NS, principal=_ANALYST, session=session, term_iri=_TERM
    )
    assert resolved.source is OwnerResolutionSource.TERM_OWNER
    assert resolved.principal_ids == (_EXPERT.object_id,)


@pytest.mark.integration
async def test_責任者がいなければ名前空間の_owner_に回す(session: AsyncSession) -> None:
    """**ここがフォールバックの本体。** 誰にも届かない問い合わせは放置され、
    放置されたことも分からない。上位に回すほうが安全側である。"""
    await _make_namespace(session)
    resolved = await resolve_term_owner(
        namespace=_NS, principal=_ANALYST, session=session, term_iri=_TERM
    )
    assert resolved.source is OwnerResolutionSource.NAMESPACE_OWNERS
    # 並びは principal_id で安定させる。
    assert resolved.principal_ids == (_OWNER2.object_id, _OWNER.object_id)


@pytest.mark.integration
async def test_フォールバックしたことが_source_で分かる(session: AsyncSession) -> None:
    """**代替で解決したことを隠さない**(ADR-0015 決定2)。

    隠すと `P2B-06` の健全性指標が「責任者が未設定の用語」を数えられない。
    問い合わせ先が返るだけでは、責任者がいるのかいないのか区別できない。
    """
    await _make_namespace(session)
    await assign_term_owner(
        namespace=_NS,
        payload=TermOwnerAssign(term_iri=_TERM, principal_id=_EXPERT.object_id),
        principal=_MAINTAINER,
        session=session,
    )
    with_owner = await resolve_term_owner(
        namespace=_NS, principal=_ANALYST, session=session, term_iri=_TERM
    )
    without_owner = await resolve_term_owner(
        namespace=_NS, principal=_ANALYST, session=session, term_iri=_OTHER_TERM
    )
    assert with_owner.source is not without_owner.source, (
        "責任者がある場合と無い場合を、呼び出し元が区別できなければならない"
    )


@pytest.mark.integration
async def test_owner_もいなければ_解決不能を返す(session: AsyncSession) -> None:
    """**404 にしない。** 「誰にも回せない」はエラーではなく、この API が
    答えるべき事実である。エラーにすると、健全性指標が拾えない。
    """
    await _make_namespace(session, with_owner=False)
    resolved = await resolve_term_owner(
        namespace=_NS, principal=_ANALYST, session=session, term_iri=_TERM
    )
    assert resolved.source is OwnerResolutionSource.UNRESOLVED
    assert resolved.principal_ids == ()


@pytest.mark.integration
async def test_解決に必要なのは_data_analyst(session: AsyncSession) -> None:
    await _make_namespace(session)
    with pytest.raises(HTTPException) as exc:
        await resolve_term_owner(
            namespace=_NS, principal=_STRANGER, session=session, term_iri=_TERM
        )
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_他の名前空間の責任者は見えない(session: AsyncSession) -> None:
    """名前空間は隔離の境界である(不変条件5)。責任者もその境界の内側にある。"""
    await _make_namespace(session)
    await NamespaceRepository(session).create(
        name="other-ns",
        display_name="other",
        description="",
        base_iri="https://e.example/other#",
        created_by=_ADMIN.object_id,
    )
    await session.commit()
    await assign_term_owner(
        namespace=_NS,
        payload=TermOwnerAssign(term_iri=_TERM, principal_id=_EXPERT.object_id),
        principal=_MAINTAINER,
        session=session,
    )
    # 同じ用語 IRI を別の名前空間で解決しても、こちらの責任者は返らない。
    resolved = await resolve_term_owner(
        namespace="other-ns", principal=_ADMIN, session=session, term_iri=_TERM
    )
    assert resolved.source is not OwnerResolutionSource.TERM_OWNER


# ---------------------------------------------------------------- 取り消し


@pytest.mark.integration
async def test_maintainer_は責任者を外せる(session: AsyncSession) -> None:
    await _make_namespace(session)
    await assign_term_owner(
        namespace=_NS,
        payload=TermOwnerAssign(term_iri=_TERM, principal_id=_EXPERT.object_id),
        principal=_MAINTAINER,
        session=session,
    )
    await unassign_term_owner(namespace=_NS, principal=_MAINTAINER, session=session, term_iri=_TERM)
    assert await list_term_owners(namespace=_NS, principal=_ANALYST, session=session) == []


@pytest.mark.integration
async def test_設定されていない用語を外すと_404(session: AsyncSession) -> None:
    """**「外した」と「もともと無かった」を同じ 204 にしない。**

    用語の実在を検査しない設計なので(決定4)、IRI のタイプミスは割り当て時に
    は分からない。ここで 404 を返すのが、タイプミスに気づける唯一の経路である。
    """
    await _make_namespace(session)
    with pytest.raises(HTTPException) as exc:
        await unassign_term_owner(
            namespace=_NS, principal=_MAINTAINER, session=session, term_iri=_TERM
        )
    assert exc.value.status_code == 404


@pytest.mark.integration
async def test_data_analyst_は外せない(session: AsyncSession) -> None:
    await _make_namespace(session)
    await assign_term_owner(
        namespace=_NS,
        payload=TermOwnerAssign(term_iri=_TERM, principal_id=_EXPERT.object_id),
        principal=_MAINTAINER,
        session=session,
    )
    with pytest.raises(HTTPException) as exc:
        await unassign_term_owner(
            namespace=_NS, principal=_ANALYST, session=session, term_iri=_TERM
        )
    assert exc.value.status_code == 403


# ---------------------------------------------------------------- 名前空間の削除


@pytest.mark.integration
async def test_名前空間を消すと責任者も消える(session: AsyncSession) -> None:
    """外部キーの `ON DELETE CASCADE` に依存している。孤児が残ると、
    同名の名前空間を作り直したときに古い責任者が復活する。"""
    await _make_namespace(session)
    await assign_term_owner(
        namespace=_NS,
        payload=TermOwnerAssign(term_iri=_TERM, principal_id=_EXPERT.object_id),
        principal=_MAINTAINER,
        session=session,
    )
    await session.commit()
    await NamespaceRepository(session).delete(_NS)
    await session.commit()
    assert await TermOwnerRepository(session).get(namespace=_NS, term_iri=_TERM) is None
