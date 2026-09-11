"""領域間マッピングの API 経路のテスト(ADR-0023、`P2B-10`)。

**名前空間を 2 つ作る。** 領域間マッピングは 2 つの領域があって初めて意味を
持つ機能なので、1 つの名前空間だけでは決定3(逆向きを自動生成しない)と
決定4(争いを消さない)を検証できない。

営業(`sales-ns`)と経理(`finance-ns`)の「優良顧客」を題材にする。
これは ADR-0009 決定8 が挙げた例そのものである。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.mappings import MappingRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.routers.health import measure_health
from ontology_api.routers.mappings import (
    MappingDeclare,
    MappingDirection,
    declare_mapping,
    list_mappings,
    revoke_mapping,
)
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.models import NamespaceRole, PlatformRole

_SALES = "sales-ns"
_FINANCE = "finance-ns"
_SALES_IRI = "https://e.example/sales#"
_FINANCE_IRI = "https://e.example/finance#"

_SALES_TERM = _SALES_IRI + "GoodCustomer"
_FINANCE_TERM = _FINANCE_IRI + "GoodCustomer"
_EXTERNAL_TERM = "http://www.w3.org/2004/02/skos/core#Concept"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_SALES_OWNER = Principal(subject="s-owner", object_id="s-owner-oid")
_SALES_MAINTAINER = Principal(subject="s-maint", object_id="s-maint-oid")
_SALES_ANALYST = Principal(subject="s-analyst", object_id="s-analyst-oid")
_FINANCE_OWNER = Principal(subject="f-owner", object_id="f-owner-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")


async def _setup(session: AsyncSession) -> None:
    repo = NamespaceRepository(session)
    roles = RoleRepository(session)
    for name, base_iri, grants in (
        (
            _SALES,
            _SALES_IRI,
            (
                (_SALES_OWNER, NamespaceRole.OWNER),
                (_SALES_MAINTAINER, NamespaceRole.MAINTAINER),
                (_SALES_ANALYST, NamespaceRole.DATA_ANALYST),
            ),
        ),
        (_FINANCE, _FINANCE_IRI, ((_FINANCE_OWNER, NamespaceRole.OWNER),)),
    ):
        await repo.create(
            name=name,
            display_name=name,
            description="",
            base_iri=base_iri,
            created_by=_ADMIN.object_id,
            require_two_person_approval=False,
        )
        for principal, role in grants:
            await roles.grant(
                namespace=name,
                principal_id=principal.object_id,
                role=role,
                granted_by=_ADMIN.object_id,
            )
    await session.commit()


async def _declare(
    session: AsyncSession,
    *,
    namespace: str,
    source: str,
    target: str,
    predicate: str,
    principal: Principal,
    reason: str = "同じ顧客区分を指している",
) -> object:
    return await declare_mapping(
        namespace=namespace,
        payload=MappingDeclare(
            source_term=source, target_term=target, predicate=predicate, reason=reason
        ),
        principal=principal,
        session=session,
    )


# ---------------------------------------------------------------- 述語の制限


@pytest.mark.integration
async def test_SKOS_の述語で宣言できる(session: AsyncSession) -> None:
    await _setup(session)
    created = await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="closeMatch",
        principal=_SALES_OWNER,
    )
    assert created.predicate == "closeMatch"  # type: ignore[attr-defined]
    assert created.predicate_iri.endswith("skos/core#closeMatch")  # type: ignore[attr-defined]
    # **相手はまだ宣言していない。** これは異常ではない(決定3)。
    assert not created.reciprocal  # type: ignore[attr-defined]
    assert not created.disputed  # type: ignore[attr-defined]


@pytest.mark.integration
async def test_equivalentClass_は_422_で拒否する(session: AsyncSession) -> None:
    """**ADR-0023 の中心。** 実測で両方のクラスが充足不能になった。"""
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _declare(
            session,
            namespace=_SALES,
            source=_SALES_TERM,
            target=_FINANCE_TERM,
            predicate="http://www.w3.org/2002/07/owl#equivalentClass",
            principal=_SALES_OWNER,
        )
    assert exc.value.status_code == 422
    assert "論理的帰結" in exc.value.detail
    # 何も保存されていない。
    assert await MappingRepository(session).outgoing(_SALES) == []


@pytest.mark.integration
async def test_理由は必須(session: AsyncSession) -> None:
    """**理由の無いマッピングはレビュー対象になりえない**(決定5)。"""
    await _setup(session)
    with pytest.raises(ValueError):
        MappingDeclare(
            source_term=_SALES_TERM, target_term=_FINANCE_TERM, predicate="closeMatch", reason=""
        )


@pytest.mark.integration
async def test_外部語彙へも張れる(session: AsyncSession) -> None:
    """**実在を検査しないのは、これが主用途だから**(決定6)。"""
    await _setup(session)
    created = await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_EXTERNAL_TERM,
        predicate="broadMatch",
        principal=_SALES_OWNER,
    )
    assert created.target_term == _EXTERNAL_TERM  # type: ignore[attr-defined]


# ------------------------------------------------------------------ 権限


@pytest.mark.integration
async def test_maintainer_は宣言できない(session: AsyncSession) -> None:
    """**相手の領域の意味について主張する行為**なので `owner` に限る(決定5)。"""
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _declare(
            session,
            namespace=_SALES,
            source=_SALES_TERM,
            target=_FINANCE_TERM,
            predicate="closeMatch",
            principal=_SALES_MAINTAINER,
        )
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_analyst_は読める(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    await _setup(session)
    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="closeMatch",
        principal=_SALES_OWNER,
    )
    found = await list_mappings(
        namespace=_SALES, principal=_SALES_ANALYST, session=session, blob=blob_store
    )
    assert [m.source_term for m in found] == [_SALES_TERM]


@pytest.mark.integration
async def test_無関係な主体は読めない(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await list_mappings(namespace=_SALES, principal=_STRANGER, session=session, blob=blob_store)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_他の名前空間の_owner_は宣言できない(session: AsyncSession) -> None:
    """経理の `owner` は営業の名前空間にマッピングを張れない。"""
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _declare(
            session,
            namespace=_SALES,
            source=_SALES_TERM,
            target=_FINANCE_TERM,
            predicate="closeMatch",
            principal=_FINANCE_OWNER,
        )
    assert exc.value.status_code == 403


# -------------------------------------------------------- 逆向きを作らない


@pytest.mark.integration
async def test_逆向きは自動で作らない(session: AsyncSession) -> None:
    """**決定3 の中心。**

    自動生成すると、**経理が宣言していない主張が経理の名前空間に現れる**。
    決定8 の「意見の相違が消されずに記録される」に正面から反する。
    """
    await _setup(session)
    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="exactMatch",
        principal=_SALES_OWNER,
    )
    # 経理側が「張った」マッピングは 0 件である。
    assert await MappingRepository(session).outgoing(_FINANCE) == []


@pytest.mark.integration
async def test_相手からは_incoming_として見える(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**逆向きを作らない代わりに、両方向から見えるようにする**(決定3)。

    これが無いと相手の主張に気づけない。
    """
    await _setup(session)
    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="exactMatch",
        principal=_SALES_OWNER,
    )
    incoming = await list_mappings(
        namespace=_FINANCE,
        principal=_FINANCE_OWNER,
        session=session,
        blob=blob_store,
        direction=MappingDirection.INCOMING,
    )
    assert [m.namespace for m in incoming] == [_SALES]
    assert incoming[0].target_term == _FINANCE_TERM
    # outgoing には出ない(経理は何も宣言していない)。
    assert (
        await list_mappings(
            namespace=_FINANCE, principal=_FINANCE_OWNER, session=session, blob=blob_store
        )
    ) == []


@pytest.mark.integration
async def test_自分が張ったものは_incoming_に出ない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """自分の名前空間の中で用語同士を結んだ場合に、両方に出てはいけない。"""
    await _setup(session)
    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_SALES_IRI + "PremiumCustomer",
        predicate="closeMatch",
        principal=_SALES_OWNER,
    )
    incoming = await list_mappings(
        namespace=_SALES,
        principal=_SALES_ANALYST,
        session=session,
        blob=blob_store,
        direction=MappingDirection.INCOMING,
    )
    assert incoming == []


# ------------------------------------------------------------ 相互と争い


@pytest.mark.integration
async def test_相互に宣言すれば_reciprocal(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="exactMatch",
        principal=_SALES_OWNER,
    )
    await _declare(
        session,
        namespace=_FINANCE,
        source=_FINANCE_TERM,
        target=_SALES_TERM,
        predicate="exactMatch",
        principal=_FINANCE_OWNER,
    )
    sales = await list_mappings(
        namespace=_SALES, principal=_SALES_ANALYST, session=session, blob=blob_store
    )
    assert sales[0].reciprocal
    assert not sales[0].disputed
    assert sales[0].counterpart_predicate == "exactMatch"


@pytest.mark.integration
async def test_述語が食い違えば_disputed_で両方残る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**決定4 の中心。** 自動で片方に寄せるのは相違を消す実装である。"""
    await _setup(session)
    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="exactMatch",
        principal=_SALES_OWNER,
        reason="営業から見れば同一である",
    )
    await _declare(
        session,
        namespace=_FINANCE,
        source=_FINANCE_TERM,
        target=_SALES_TERM,
        predicate="closeMatch",
        principal=_FINANCE_OWNER,
        reason="経理の定義は与信を含むので同一とは言えない",
    )

    sales = await list_mappings(
        namespace=_SALES, principal=_SALES_ANALYST, session=session, blob=blob_store
    )
    finance = await list_mappings(
        namespace=_FINANCE, principal=_FINANCE_OWNER, session=session, blob=blob_store
    )
    # **両方が残っていて、両方が争いとして見える。**
    assert sales[0].predicate == "exactMatch"
    assert sales[0].disputed
    assert sales[0].counterpart_predicate == "closeMatch"
    assert finance[0].predicate == "closeMatch"
    assert finance[0].disputed
    assert finance[0].counterpart_predicate == "exactMatch"
    # **理由もそれぞれ残る。** 相違の中身が読める。
    assert "与信" in finance[0].reason


@pytest.mark.integration
async def test_broad_と_narrow_の対応は争いではない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """非対称な述語を正しく宣言した状態を争いにしてはいけない。"""
    await _setup(session)
    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="broadMatch",
        principal=_SALES_OWNER,
    )
    await _declare(
        session,
        namespace=_FINANCE,
        source=_FINANCE_TERM,
        target=_SALES_TERM,
        predicate="narrowMatch",
        principal=_FINANCE_OWNER,
    )
    sales = await list_mappings(
        namespace=_SALES, principal=_SALES_ANALYST, session=session, blob=blob_store
    )
    assert sales[0].reciprocal
    assert not sales[0].disputed


# ------------------------------------------------------------ 付け替えと取り消し


@pytest.mark.integration
async def test_同じ用語ペアの再宣言は付け替え(session: AsyncSession) -> None:
    await _setup(session)
    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="exactMatch",
        principal=_SALES_OWNER,
    )
    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="closeMatch",
        principal=_SALES_OWNER,
        reason="考え直した",
    )
    found = await MappingRepository(session).outgoing(_SALES)
    assert len(found) == 1
    assert found[0].predicate == "closeMatch"
    assert found[0].reason == "考え直した"


@pytest.mark.integration
async def test_取り消せる(session: AsyncSession) -> None:
    await _setup(session)
    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="closeMatch",
        principal=_SALES_OWNER,
    )
    await revoke_mapping(
        namespace=_SALES,
        principal=_SALES_OWNER,
        session=session,
        source_term=_SALES_TERM,
        target_term=_FINANCE_TERM,
    )
    assert await MappingRepository(session).outgoing(_SALES) == []


@pytest.mark.integration
async def test_無いマッピングの取り消しは_404(session: AsyncSession) -> None:
    """**「取り消した」と「元から無かった」を混同しない。**"""
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await revoke_mapping(
            namespace=_SALES,
            principal=_SALES_OWNER,
            session=session,
            source_term=_SALES_TERM,
            target_term=_FINANCE_TERM,
        )
    assert exc.value.status_code == 404


@pytest.mark.integration
async def test_相手の主張は取り消せない(session: AsyncSession) -> None:
    """**消せてしまうと「相違が消されずに記録される」が成立しない**(決定3)。"""
    await _setup(session)
    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="exactMatch",
        principal=_SALES_OWNER,
    )
    # 経理の owner が、営業が張ったマッピングを自分の名前空間から消そうとする。
    with pytest.raises(HTTPException) as exc:
        await revoke_mapping(
            namespace=_FINANCE,
            principal=_FINANCE_OWNER,
            session=session,
            source_term=_SALES_TERM,
            target_term=_FINANCE_TERM,
        )
    assert exc.value.status_code == 404
    # 営業側には残っている。
    assert len(await MappingRepository(session).outgoing(_SALES)) == 1


# -------------------------------------------------------------------- 監査


@pytest.mark.integration
async def test_宣言と取り消しが監査に残る(session: AsyncSession) -> None:
    """**説明責任の記録である**(ADR-0015 が張った側に責任を置いた)。"""
    await _setup(session)
    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="closeMatch",
        principal=_SALES_OWNER,
        reason="顧客区分の粒度が近い",
    )
    await revoke_mapping(
        namespace=_SALES,
        principal=_SALES_OWNER,
        session=session,
        source_term=_SALES_TERM,
        target_term=_FINANCE_TERM,
    )
    page = await AuditRepository(session).query(namespace=_SALES)
    actions = [e.action for e in page.events]
    assert "mapping-declared" in actions
    assert "mapping-revoked" in actions
    declared = next(e for e in page.events if e.action == "mapping-declared")
    assert declared.reason == "顧客区分の粒度が近い"
    assert "closeMatch" in declared.subject
    assert declared.actor == _SALES_OWNER.object_id


# -------------------------------------------------------------- 健全性指標


@pytest.mark.integration
async def test_争われているマッピングが健全性指標に出る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**自動で片方に寄せない代わりに、ここで可視化する**(決定4)。"""
    await _setup(session)
    report = await measure_health(
        namespace=_SALES, principal=_SALES_ANALYST, session=session, blob=blob_store
    )
    assert report["disputed_mapping_count"] == 0

    await _declare(
        session,
        namespace=_SALES,
        source=_SALES_TERM,
        target=_FINANCE_TERM,
        predicate="exactMatch",
        principal=_SALES_OWNER,
    )
    # 片側だけでは争いではない。
    report = await measure_health(
        namespace=_SALES, principal=_SALES_ANALYST, session=session, blob=blob_store
    )
    assert report["disputed_mapping_count"] == 0

    await _declare(
        session,
        namespace=_FINANCE,
        source=_FINANCE_TERM,
        target=_SALES_TERM,
        predicate="closeMatch",
        principal=_FINANCE_OWNER,
    )
    report = await measure_health(
        namespace=_SALES, principal=_SALES_ANALYST, session=session, blob=blob_store
    )
    assert report["disputed_mapping_count"] == 1
