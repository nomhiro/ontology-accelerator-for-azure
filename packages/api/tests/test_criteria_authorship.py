"""受け入れ基準の出自の検査(ADR-0029、`P2B-16`)。

**不変条件14 を宣言から強制にした。**

> 受け入れ基準を、その基準で審査される側が書き換えられてはならない

[ADR-0022](../../../docs/adr/0022-competency-question-sets.md) 決定6 は
「防止ではなく可視化」に留めていた。四眼原則(ADR-0014 決定4)は
「publish した主体は approve できない」なので、**残っていた穴は
「著者が基準を緩め、同僚が承認する」**である。一人が全部やる形ではないので、
四眼原則の考え方では捉えられない。

ここで固定するのは 4 つである。

1. **著者が publish 後に基準を書き換えたら承認が止まる**(決定2)
2. **版より前に定められた基準は止めない**(決定2)。責任者が基準を定めて
   他人の版を審査するのは**正常な統制**である
3. **`require_two_person_approval` に従う**(決定3)。スイッチを増やさない
4. **止まらない場合でも事実は見える**(決定6)
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.questions import QuestionSetRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.routers.questions import run_question_set
from ontology_api.routers.versions import PublishRequest, approve_version, publish_version
from ontology_api.services.projection import (
    CriteriaSelfRevisionError,
    ProjectionService,
)
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.models import NamespaceRole, OntologyVersionStatus, PlatformRole
from ontology_core.sparql.client import SparqlStore

_NS = "criteria-ns"
_BASE = "https://e.example/#"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_OWNER = Principal(subject="owner", object_id="owner-oid")
_OTHER_OWNER = Principal(subject="owner2", object_id="owner2-oid")
_MAINTAINER = Principal(subject="maintainer", object_id="maintainer-oid")

_TTL = """
@prefix ex: <https://e.example/#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Order a owl:Class ; rdfs:label "注文" .
ex:Customer a owl:Class ; rdfs:label "顧客" .
ex:orderedBy a owl:ObjectProperty ; rdfs:domain ex:Order ; rdfs:range ex:Customer .
"""

# 上の TTL が満たす基準。**検査は基準の内容を見ないので、合否は関係ない** —
# 合格する基準を使うのは、止まった理由が出自であることを明確にするためである。
_SATISFIED = """
questions:
  - id: cq-1
    question: 注文から顧客へ辿れるか
    expect: ask_true
    sparql: |
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      PREFIX ex: <https://e.example/#>
      ASK { ?p rdfs:domain ex:Order ; rdfs:range ex:Customer }
"""


class _NullStore(SparqlStore):
    """射影を行わない代役。**検査は正本だけを見る**(不変条件15)。"""

    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
        raise AssertionError("基準の出自の検査はストアに問い合わせてはいけない")

    async def construct(self, sparql: str, *, dataset: str) -> str:
        return ""

    async def update(self, sparql: str, *, dataset: str) -> None: ...
    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None: ...
    async def put_default_graph(self, turtle: str, *, dataset: str) -> None: ...
    async def delete_graph(self, graph_iri: str, *, dataset: str) -> None: ...
    async def list_graphs(self, dataset: str) -> list[str]:
        return []

    async def has_default_graph_content(self, dataset: str) -> bool:
        return True

    async def list_datasets(self) -> list[str]:
        return []

    async def create_dataset(self, dataset: str) -> None: ...
    async def delete_dataset(self, dataset: str) -> None: ...


async def _setup(session: AsyncSession, *, four_eyes: bool = True) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri=_BASE,
        created_by=_ADMIN.object_id,
        require_two_person_approval=four_eyes,
    )
    repo = RoleRepository(session)
    for principal, role in (
        (_OWNER, NamespaceRole.OWNER),
        (_OTHER_OWNER, NamespaceRole.OWNER),
        (_MAINTAINER, NamespaceRole.MAINTAINER),
    ):
        await repo.grant(
            namespace=_NS,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await session.commit()


def _service(session: AsyncSession, blob: OntologyBlobStore) -> ProjectionService:
    return ProjectionService(
        session=session, blob=blob, store=_NullStore(), graph_iri_base="urn:ontology:graph"
    )


async def _revise(session: AsyncSession, *, actor: Principal, reason: str = "r") -> None:
    """基準を 1 改訂足して commit する。

    **1 改訂ごとに commit する。** `created_at` は `now()`(トランザクション
    開始時刻)なので、まとめて commit すると版と同じ時刻になり、境界の
    検証ができない。
    """
    await QuestionSetRepository(session).add(
        namespace=_NS, content=_SATISFIED, question_count=1, actor=actor.object_id, reason=reason
    )
    await session.commit()


async def _publish_and_submit(
    session: AsyncSession,
    blob: OntologyBlobStore,
    settings: Settings,
    *,
    author: Principal = _OWNER,
    version: str = "1.0.0",
) -> None:
    await publish_version(
        namespace=_NS,
        payload=PublishRequest(version=version, turtle=_TTL),
        principal=author,
        session=session,
        blob=blob,
        store=_NullStore(),
        settings=settings,
        response=Response(),
    )
    await session.commit()
    await _service(session, blob).submit(namespace=_NS, version=version, actor=author.object_id)
    await session.commit()


async def _approve(
    session: AsyncSession,
    blob: OntologyBlobStore,
    settings: Settings,
    *,
    approver: Principal = _MAINTAINER,
    version: str = "1.0.0",
) -> None:
    await approve_version(
        namespace=_NS,
        version=version,
        principal=approver,
        session=session,
        blob=blob,
        store=_NullStore(),
        settings=settings,
        payload=None,
    )
    await session.commit()


# ----------------------------------------------------------------- 止める


@pytest.mark.integration
async def test_著者が_publish_後に基準を書き換えたら承認が止まる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**四眼原則が塞いでいなかった穴である**(ADR-0029 のコンテキスト経路 2)。

    著者と承認者は別人なので四眼原則は通る。しかし**審査される成果物の著者が
    合格条件を書いた**ので、承認は止まる(不変条件14)。
    """
    await _setup(session)
    await _publish_and_submit(session, blob_store, settings, author=_OWNER)
    await _revise(session, actor=_OWNER)

    with pytest.raises(HTTPException) as exc:
        await _approve(session, blob_store, settings, approver=_MAINTAINER)
    assert exc.value.status_code == 422
    assert "受け入れ基準" in exc.value.detail


@pytest.mark.integration
async def test_platform_admin_でも止まる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**不変条件12 の理由がそのまま当てはまる**(ADR-0029 決定4)。

    管理者が飛び越えられるなら、四眼原則が「管理者以外への制約」に成り下がる。
    """
    await _setup(session)
    await _publish_and_submit(session, blob_store, settings, author=_OWNER)
    await _revise(session, actor=_OWNER)

    with pytest.raises(HTTPException) as exc:
        await _approve(session, blob_store, settings, approver=_ADMIN)
    assert exc.value.status_code == 422


@pytest.mark.integration
async def test_止まった版は_in_review_のままである(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**検査は状態を変える前に行う**(SHACL 検証と同じ位置)。"""
    await _setup(session)
    await _publish_and_submit(session, blob_store, settings, author=_OWNER)
    await _revise(session, actor=_OWNER)
    with pytest.raises(HTTPException):
        await _approve(session, blob_store, settings)

    from ontology_api.repositories.versions import VersionRepository

    row = await VersionRepository(session).get(_NS, "1.0.0")
    assert row is not None
    assert row.status is OntologyVersionStatus.IN_REVIEW


# ----------------------------------------------------------------- 通す


@pytest.mark.integration
async def test_別の主体が書いた基準なら通る(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish_and_submit(session, blob_store, settings, author=_OWNER)
    await _revise(session, actor=_OTHER_OWNER)
    await _approve(session, blob_store, settings)

    from ontology_api.repositories.versions import VersionRepository

    row = await VersionRepository(session).get(_NS, "1.0.0")
    assert row is not None
    assert row.status is OntologyVersionStatus.APPROVED


@pytest.mark.integration
async def test_版より前に定められた基準は止めない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**これは正常な統制である**(ADR-0029 のコンテキスト経路 3)。

    名前空間の責任者が基準を定め、他人の成果物をその基準で審査するのは
    統制のあるべき姿である。止めてはいけない。
    """
    await _setup(session)
    await _revise(session, actor=_OWNER)
    await _publish_and_submit(session, blob_store, settings, author=_OWNER)
    await _approve(session, blob_store, settings)

    from ontology_api.repositories.versions import VersionRepository

    row = await VersionRepository(session).get(_NS, "1.0.0")
    assert row is not None
    assert row.status is OntologyVersionStatus.APPROVED


@pytest.mark.integration
async def test_質問集合が無ければ止めない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """「基準を定めていない」は「基準を満たしていない」ではない(ADR-0022 決定7)。"""
    await _setup(session)
    await _publish_and_submit(session, blob_store, settings, author=_OWNER)
    await _approve(session, blob_store, settings)

    from ontology_api.repositories.versions import VersionRepository

    row = await VersionRepository(session).get(_NS, "1.0.0")
    assert row is not None
    assert row.status is OntologyVersionStatus.APPROVED


@pytest.mark.integration
async def test_別の主体が基準を再確認すれば通る(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**逃げ道は「基準を消す」ではなく「別の主体の署名を付ける」**(決定5)。

    内容が同じ改訂でよい。改訂は不変で `reason` が必須なので、
    「別の主体が確認した」という行為が記録される。
    """
    await _setup(session)
    await _publish_and_submit(session, blob_store, settings, author=_OWNER)
    await _revise(session, actor=_OWNER)
    with pytest.raises(HTTPException):
        await _approve(session, blob_store, settings)

    # 別の主体が同じ内容で改訂し直す。
    await _revise(session, actor=_OTHER_OWNER, reason="基準を確認した")
    await _approve(session, blob_store, settings)

    from ontology_api.repositories.versions import VersionRepository

    row = await VersionRepository(session).get(_NS, "1.0.0")
    assert row is not None
    assert row.status is OntologyVersionStatus.APPROVED


# -------------------------------------------------- 四眼原則の設定に従う


@pytest.mark.integration
async def test_四眼原則が無効なら止めない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**新しいスイッチを作らない**(ADR-0029 決定3)。

    運用者 1 人のデプロイ(postdeploy の経路)では、版を書いた人が基準も書く。
    ADR-0014 決定4 が四眼原則を名前空間ごとにした理由と同じ制約である。
    """
    await _setup(session, four_eyes=False)
    await _publish_and_submit(session, blob_store, settings, author=_OWNER)
    await _revise(session, actor=_OWNER)
    await _approve(session, blob_store, settings, approver=_OWNER)

    from ontology_api.repositories.versions import VersionRepository

    row = await VersionRepository(session).get(_NS, "1.0.0")
    assert row is not None
    assert row.status is OntologyVersionStatus.APPROVED


@pytest.mark.integration
async def test_四眼原則が無効でも事実は見える(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**事実の検出と、止めるかの判断を分ける**(ADR-0029 決定6)。

    四眼原則を切っている運用者にも、承認する前に何が起きたかは見えるべきである。
    ADR-0022 決定6 の「可視化」は、防止が入った後も価値を持つ。
    """
    await _setup(session, four_eyes=False)
    await _publish_and_submit(session, blob_store, settings, author=_OWNER)
    await _revise(session, actor=_OWNER)

    report = await run_question_set(
        namespace=_NS,
        version="1.0.0",
        principal=_MAINTAINER,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
    )
    assert report.criteria_self_revised is not None
    assert _OWNER.object_id in report.criteria_self_revised
    # 基準そのものは満たしている。**止める理由は合否ではなく出自である。**
    assert report.conforms is True


@pytest.mark.integration
async def test_問題が無ければ欄は_None_のまま(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _revise(session, actor=_OWNER)
    await _publish_and_submit(session, blob_store, settings, author=_OWNER)

    report = await run_question_set(
        namespace=_NS,
        version="1.0.0",
        principal=_MAINTAINER,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
    )
    assert report.criteria_self_revised is None


# --------------------------------------------------------- 検査そのもの


@pytest.mark.integration
async def test_検査は状態を変えずに呼べる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """レビュー用の口と `approve` の両方から呼ぶので、何度呼んでも同じ。"""
    await _setup(session)
    await _publish_and_submit(session, blob_store, settings, author=_OWNER)
    await _revise(session, actor=_OWNER)
    service = _service(session, blob_store)
    first = await service.check_criteria_authorship(namespace=_NS, version="1.0.0")
    second = await service.check_criteria_authorship(namespace=_NS, version="1.0.0")
    assert first == second
    assert first is not None
    assert first.revision == 1


@pytest.mark.integration
async def test_無い版は_404_相当の例外(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    from ontology_api.services.projection import UnknownVersionError

    await _setup(session)
    with pytest.raises(UnknownVersionError):
        await _service(session, blob_store).check_criteria_authorship(
            namespace=_NS, version="9.9.9"
        )


def test_例外は説明と詳細の両方を持つ() -> None:
    """**運用者が取るべき対処が違うので `CompetencyViolationError` と分ける。**

    あちらは「オントロジーか基準を直す」、こちらは「**別の主体に基準を
    確認してもらう**」である。混ぜると、基準をさらに緩めて解決しようとする。
    """
    from datetime import UTC, datetime

    from ontology_api.services.projection import CriteriaSelfRevision

    detail = CriteriaSelfRevision(
        revision=3,
        author="alice-oid",
        revised_at=datetime(2026, 9, 2, tzinfo=UTC),
        version_created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    exc = CriteriaSelfRevisionError("止めた", detail=detail)
    assert exc.detail.revision == 3
    assert "alice-oid" in detail.message()
    assert "別の主体" in detail.message()


@pytest.mark.integration
async def test_版と同時刻の改訂も止める(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**境界は「以降」である**(ADR-0029 決定2 の `>=`)。

    `created_at` は `now()`(トランザクション開始時刻)なので、版と改訂を
    同一トランザクションで作ると**同じ値になる**(PostgreSQL の性質)。
    そのとき著者は両方を書いているので、止めるのが正しい。

    **厳格な不等号にすると、この場合が通る** — 変異テストで実際に生き残った
    ので、境界を固定するテストを足した。
    """
    from sqlalchemy import select

    from ontology_api.repositories.versions import VersionRepository
    from ontology_core.db import CompetencyQuestionSetRow

    await _setup(session)
    await _publish_and_submit(session, blob_store, settings, author=_OWNER)
    await _revise(session, actor=_OWNER)

    version = await VersionRepository(session).get(_NS, "1.0.0")
    assert version is not None
    row = (
        await session.execute(
            select(CompetencyQuestionSetRow).where(CompetencyQuestionSetRow.namespace == _NS)
        )
    ).scalar_one()
    # 同一トランザクションで作られた状況を再現する。
    row.created_at = version.created_at
    await session.flush()

    found = await _service(session, blob_store).check_criteria_authorship(
        namespace=_NS, version="1.0.0"
    )
    assert found is not None, "版と同時刻の改訂を通してはいけない"
    assert found.revised_at == found.version_created_at
