"""想定質問の集合と承認ゲートのテスト(P2B-14、ADR-0022)。

**中心にあるのは 3 つである。**

1. **受け入れ基準を書き換えられるなら通ったことは保証にならない**(決定2・6)
   — 書き込みは `owner` に限り、改訂は不変で、減った質問が監査に出る
2. **承認時の評価はストアに問い合わせない**(決定3) — その時点でその版は
   まだ射影されていない。`_NullStore` で回るのがその証拠になる
3. **「評価していない」を合格に丸めない**(決定5)
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.questions import QuestionSetRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.routers.health import measure_health
from ontology_api.routers.questions import (
    QuestionSetRevise,
    get_question_set,
    list_question_set_revisions,
    revise_question_set,
    run_question_set,
)
from ontology_api.routers.versions import PublishRequest, publish_version
from ontology_api.services.projection import (
    CompetencyEvaluationError,
    CompetencyViolationError,
    ProjectionService,
)
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.models import NamespaceRole, PlatformRole
from ontology_core.sparql.client import SparqlStore

_NS = "questions-ns"
_BASE = "https://e.example/#"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_OWNER = Principal(subject="owner", object_id="owner-oid")
_MAINTAINER = Principal(subject="maintainer", object_id="maintainer-oid")
_STEWARD = Principal(subject="steward", object_id="steward-oid")
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")

_TTL = """
@prefix ex: <https://e.example/#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Order a owl:Class ; rdfs:label "注文" .
ex:Customer a owl:Class ; rdfs:label "顧客" .
ex:orderedBy a owl:ObjectProperty ; rdfs:domain ex:Order ; rdfs:range ex:Customer .
"""

# 「注文から顧客へ辿れるか」。上の TTL は満たす。
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

# 「返品の語彙があるか」。上の TTL は満たさない。
_UNSATISFIED = """
questions:
  - id: cq-1
    question: 注文から顧客へ辿れるか
    expect: ask_true
    sparql: |
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      PREFIX ex: <https://e.example/#>
      ASK { ?p rdfs:domain ex:Order ; rdfs:range ex:Customer }
  - id: cq-2
    question: 返品の語彙があるか
    expect: ask_true
    sparql: |
      PREFIX ex: <https://e.example/#>
      ASK { ex:Return ?p ?o }
"""


class _NullStore(SparqlStore):
    """**射影を一切行わないストア。**

    このテストが `_NullStore` で通ることが「承認時の評価がストアに問い合わせ
    ていない」ことの証拠になる(ADR-0022 決定3)。
    """

    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        raise AssertionError("承認時の想定質問の評価はストアに問い合わせてはいけない")

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


async def _setup(session: AsyncSession) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri=_BASE,
        created_by=_ADMIN.object_id,
        require_two_person_approval=False,
    )
    repo = RoleRepository(session)
    for principal, role in (
        (_OWNER, NamespaceRole.OWNER),
        (_MAINTAINER, NamespaceRole.MAINTAINER),
        (_STEWARD, NamespaceRole.DATA_STEWARD),
        (_ANALYST, NamespaceRole.DATA_ANALYST),
    ):
        await repo.grant(
            namespace=_NS,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await session.commit()


def _service(blob_store: OntologyBlobStore, session: AsyncSession) -> ProjectionService:
    return ProjectionService(
        session=session,
        blob=blob_store,
        store=_NullStore(),
        graph_iri_base="urn:ontology:graph",
    )


async def _publish(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    settings: Settings,
    *,
    version: str = "1.0.0",
    turtle: str = _TTL,
) -> None:
    await publish_version(
        namespace=_NS,
        payload=PublishRequest(version=version, turtle=turtle),
        principal=_STEWARD,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
        response=Response(),
    )
    await _service(blob_store, session).submit(
        namespace=_NS, version=version, actor=_STEWARD.object_id
    )
    await session.commit()


async def _revise(session: AsyncSession, content: str, *, principal: Principal = _OWNER) -> None:
    await revise_question_set(
        namespace=_NS,
        payload=QuestionSetRevise(content=content, reason="テスト"),
        principal=principal,
        session=session,
    )


# ------------------------------------------------------------ 改訂の書き込み


@pytest.mark.integration
async def test_owner_は改訂を足せる(session: AsyncSession) -> None:
    await _setup(session)
    created = await revise_question_set(
        namespace=_NS,
        payload=QuestionSetRevise(content=_SATISFIED, reason="最初の基準"),
        principal=_OWNER,
        session=session,
    )
    assert created.revision == 1
    assert created.question_count == 1
    assert created.created_by == _OWNER.object_id
    assert created.reason == "最初の基準"


@pytest.mark.integration
async def test_maintainer_は改訂を足せない(session: AsyncSession) -> None:
    """**承認する主体が合格条件も書き換えられると検査が意味を失う**(決定6)。

    `approve` は `maintainer` 以上なので、書き込みは 1 段上に置く。
    """
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _revise(session, _SATISFIED, principal=_MAINTAINER)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_無関係な主体は読めない(session: AsyncSession) -> None:
    await _setup(session)
    await _revise(session, _SATISFIED)
    with pytest.raises(HTTPException) as exc:
        await get_question_set(namespace=_NS, principal=_STRANGER, session=session)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_analyst_は読める(session: AsyncSession) -> None:
    """基準は「何に答えられなければならないか」なので、分析者が読めるべきである。"""
    await _setup(session)
    await _revise(session, _SATISFIED)
    got = await get_question_set(namespace=_NS, principal=_ANALYST, session=session)
    assert got.revision == 1
    assert "cq-1" in got.content


@pytest.mark.integration
async def test_改訂は積み上がり有効なのは最大の改訂(session: AsyncSession) -> None:
    """**改訂は不変である**(決定1)。書き換える口を持たない。"""
    await _setup(session)
    await _revise(session, _SATISFIED)
    await _revise(session, _UNSATISFIED)

    active = await QuestionSetRepository(session).active(_NS)
    assert active is not None
    assert active.revision == 2
    assert active.question_count == 2

    revisions = await list_question_set_revisions(
        namespace=_NS, principal=_ANALYST, session=session
    )
    # 新しい順。**古い改訂が消えていないことが履歴の価値である。**
    assert [r.revision for r in revisions] == [2, 1]
    assert revisions[1].question_count == 1


@pytest.mark.integration
async def test_理由は必須(session: AsyncSession) -> None:
    """**基準を緩めたことが理由なしに起きてはいけない**(決定6)。"""
    await _setup(session)
    with pytest.raises(ValueError):
        QuestionSetRevise(content=_SATISFIED, reason="")


@pytest.mark.integration
async def test_不正な質問集合は保存しない(session: AsyncSession) -> None:
    """**保存する前に検証する。**

    検証を通らない質問集合を保存すると、`approve` が「評価できなかった」で
    永久に止まる名前空間ができる。
    """
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _revise(session, "questions: これは配列ではない")
    assert exc.value.status_code == 422
    assert await QuestionSetRepository(session).active(_NS) is None


@pytest.mark.integration
async def test_減った質問が監査に残る(session: AsyncSession) -> None:
    """**防止ではなく可視化である**(決定6)。

    `owner` は基準を緩めて自分で承認できる。それを止める仕組みは無いので、
    **緩めたことが一覧できなければならない。**
    """
    await _setup(session)
    await _revise(session, _UNSATISFIED)  # cq-1, cq-2
    await _revise(session, _SATISFIED)  # cq-1 のみ = cq-2 を削除

    events = await AuditRepository(session).list_for_subject(_NS, f"{_NS}#questions@2")
    assert len(events) == 1
    assert events[0].action == "questions-revised"
    assert "削除された質問: cq-2" in events[0].reason


@pytest.mark.integration
async def test_質問を減らしていなければ削除の記録は出ない(session: AsyncSession) -> None:
    await _setup(session)
    await _revise(session, _SATISFIED)
    await _revise(session, _UNSATISFIED)  # 増えただけ
    events = await AuditRepository(session).list_for_subject(_NS, f"{_NS}#questions@2")
    assert "削除された質問" not in events[0].reason


@pytest.mark.integration
async def test_質問集合が無ければ_404(session: AsyncSession) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await get_question_set(namespace=_NS, principal=_ANALYST, session=session)
    assert exc.value.status_code == 404
    assert "基準を定めていない" in exc.value.detail


# -------------------------------------------------------------- 承認ゲート


@pytest.mark.integration
async def test_基準を満たしていれば承認できる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _revise(session, _SATISFIED)
    await _publish(session, blob_store, settings)
    updated = await _service(blob_store, session).approve(
        namespace=_NS, version="1.0.0", actor=_MAINTAINER.object_id
    )
    assert updated.status.value == "approved"


@pytest.mark.integration
async def test_基準を満たしていなければ承認を止める(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """ADR-0009 決定1: 合意済みの規約はブロッキングである。"""
    await _setup(session)
    await _revise(session, _UNSATISFIED)
    await _publish(session, blob_store, settings)
    with pytest.raises(CompetencyViolationError) as exc:
        await _service(blob_store, session).approve(
            namespace=_NS, version="1.0.0", actor=_MAINTAINER.object_id
        )
    # 落ちた質問だけが挙がる(通った cq-1 は挙がらない)。
    assert "cq-2" in str(exc.value)
    assert "cq-1:" not in str(exc.value)
    assert "改訂 1" in str(exc.value)
    # **状態は変わっていない**(検査は遷移より前にある)。
    from ontology_api.repositories.versions import VersionRepository

    row = await VersionRepository(session).get(_NS, "1.0.0")
    assert row is not None
    assert row.status.value == "in-review"


@pytest.mark.integration
async def test_質問集合が無ければ承認を止めない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**「基準を定めていない」は「基準を満たしていない」ではない**(決定7)。

    ここでブロックすると、この機能を入れた瞬間に既存のすべての名前空間が
    承認不能になる。
    """
    await _setup(session)
    await _publish(session, blob_store, settings)
    updated = await _service(blob_store, session).approve(
        namespace=_NS, version="1.0.0", actor=_MAINTAINER.object_id
    )
    assert updated.status.value == "approved"


@pytest.mark.integration
async def test_壊れた質問集合は承認を止める(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**壊れた質問集合を「基準なし」に丸めない。**

    丸めると、質問集合を壊すことが検査を無効化する手段になる。
    """
    await _setup(session)
    await _revise(session, _SATISFIED)
    # API を通さずに壊した本文を入れる(API は保存前に検証するため)。
    from sqlalchemy import update

    from ontology_core.db import CompetencyQuestionSetRow

    await session.execute(
        update(CompetencyQuestionSetRow)
        .where(CompetencyQuestionSetRow.namespace == _NS)
        .values(content="questions: 壊れている")
    )
    await session.commit()

    await _publish(session, blob_store, settings)
    with pytest.raises(CompetencyEvaluationError):
        await _service(blob_store, session).approve(
            namespace=_NS, version="1.0.0", actor=_MAINTAINER.object_id
        )


@pytest.mark.integration
async def test_どの改訂で通ったかが監査に残る(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**基準は改訂されうるので、版だけでは復元できない**(決定8)。"""
    await _setup(session)
    await _revise(session, _SATISFIED)
    await _publish(session, blob_store, settings)
    await _service(blob_store, session).approve(
        namespace=_NS, version="1.0.0", actor=_MAINTAINER.object_id, reason="レビュー済み"
    )
    events = await AuditRepository(session).list_for_subject(_NS, f"{_NS}@1.0.0")
    approved = [e for e in events if e.action == "approved"]
    assert len(approved) == 1
    assert "レビュー済み" in approved[0].reason
    assert "質問集合の改訂 1" in approved[0].reason


@pytest.mark.integration
async def test_質問集合が無ければ監査に改訂を書かない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """基準を定めていないのに「改訂 0 で合格」と書いてはいけない。"""
    await _setup(session)
    await _publish(session, blob_store, settings)
    await _service(blob_store, session).approve(
        namespace=_NS, version="1.0.0", actor=_MAINTAINER.object_id, reason="理由"
    )
    events = await AuditRepository(session).list_for_subject(_NS, f"{_NS}@1.0.0")
    approved = next(e for e in events if e.action == "approved")
    assert approved.reason == "理由"
    assert "質問集合" not in approved.reason


# ------------------------------------------------------------ 試す口


@pytest.mark.integration
async def test_状態を変えずに試せる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**これが無いと、承認を試して 422 を食らうまで分からない**(決定9)。"""
    await _setup(session)
    await _revise(session, _UNSATISFIED)
    await _publish(session, blob_store, settings)
    report = await run_question_set(
        namespace=_NS,
        version="1.0.0",
        principal=_ANALYST,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
    )
    assert report.revision == 1
    assert not report.conforms
    assert [r.id for r in report.results if not r.passed] == ["cq-2"]
    assert report.not_evaluated == ()

    from ontology_api.repositories.versions import VersionRepository

    row = await VersionRepository(session).get(_NS, "1.0.0")
    assert row is not None
    assert row.status.value == "in-review"


@pytest.mark.integration
async def test_質問集合が無ければ試す口は改訂を_None_で返す(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish(session, blob_store, settings)
    report = await run_question_set(
        namespace=_NS,
        version="1.0.0",
        principal=_ANALYST,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
    )
    assert report.revision is None
    assert report.conforms
    assert report.results == ()


@pytest.mark.integration
async def test_存在しない版は_404(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _revise(session, _SATISFIED)
    with pytest.raises(HTTPException) as exc:
        await run_question_set(
            namespace=_NS,
            version="9.9.9",
            principal=_ANALYST,
            session=session,
            blob=blob_store,
            store=_NullStore(),
            settings=settings,
        )
    assert exc.value.status_code == 404


# -------------------------------------------------------------- 健全性指標


@pytest.mark.integration
async def test_健全性指標に質問の件数が出る(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**定めていないことが見える唯一の経路である**(決定7)。"""
    await _setup(session)
    report = await measure_health(
        namespace=_NS, principal=_ANALYST, session=session, blob=blob_store
    )
    # 定めていない = 0。**`None`(測れなかった)ではない。**
    assert report["competency_question_count"] == 0

    await _revise(session, _UNSATISFIED)
    report = await measure_health(
        namespace=_NS, principal=_ANALYST, session=session, blob=blob_store
    )
    assert report["competency_question_count"] == 2


@pytest.mark.integration
async def test_評価しきれなければ承認を止める(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**「評価していない」を合格に丸めない**(ADR-0022 決定5)。

    予算超過を無視して承認を通す変異が、これが無いと生き残った(実際に変異
    テストで見つけた穴である)。**全部通っていても未評価があれば止める。**

    予算そのものの挙動は `packages/core/tests/test_competency_memory.py` が
    見るので、ここでは**ゲートだけ**を固定する。
    """
    from ontology_core.competency import (
        CompetencyQuestion,
        CompetencyReport,
        Expectation,
        QuestionResult,
    )

    await _setup(session)
    await _revise(session, _SATISFIED)
    await _publish(session, blob_store, settings)

    question = CompetencyQuestion(
        id="cq-1", question="q", expect=Expectation.ASK_TRUE, sparql="ASK { ?s ?p ?o }"
    )
    passing = QuestionResult(question, True, "ASK -> True")

    async def _partial(*, namespace: str, version: str) -> tuple[int, CompetencyReport]:
        return 1, CompetencyReport(results=(passing,), not_evaluated=("cq-2",))

    service = _service(blob_store, session)
    monkeypatch.setattr(service, "evaluate_competency_questions", _partial)
    with pytest.raises(CompetencyEvaluationError, match="評価しきれませんでした"):
        await service.approve(namespace=_NS, version="1.0.0", actor=_MAINTAINER.object_id)

    from ontology_api.repositories.versions import VersionRepository

    row = await VersionRepository(session).get(_NS, "1.0.0")
    assert row is not None
    assert row.status.value == "in-review"


# --------------------------------------------------------- HTTP の対応付け


@pytest.mark.integration
async def test_基準を満たさない承認は_422(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**運用者が見るのは HTTP の状態コードである。**

    「基準を満たしていない」(422)と「確かめられなかった」(502)を混ぜない。
    """
    from ontology_api.routers.versions import approve_version

    await _setup(session)
    await _revise(session, _UNSATISFIED)
    await _publish(session, blob_store, settings)
    with pytest.raises(HTTPException) as exc:
        await approve_version(
            namespace=_NS,
            version="1.0.0",
            principal=_MAINTAINER,
            session=session,
            blob=blob_store,
            store=_NullStore(),
            settings=settings,
        )
    assert exc.value.status_code == 422
    assert "cq-2" in exc.value.detail


@pytest.mark.integration
async def test_評価できなかった承認は_502(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """壊れた質問集合は 422 ではなく 502 である(対処が違う)。"""
    from ontology_api.routers.versions import approve_version

    await _setup(session)
    await _revise(session, _SATISFIED)
    from sqlalchemy import update

    from ontology_core.db import CompetencyQuestionSetRow

    await session.execute(
        update(CompetencyQuestionSetRow)
        .where(CompetencyQuestionSetRow.namespace == _NS)
        .values(content="questions: 壊れている")
    )
    await session.commit()
    await _publish(session, blob_store, settings)

    with pytest.raises(HTTPException) as exc:
        await approve_version(
            namespace=_NS,
            version="1.0.0",
            principal=_MAINTAINER,
            session=session,
            blob=blob_store,
            store=_NullStore(),
            settings=settings,
        )
    assert exc.value.status_code == 502
    assert "承認前の検証を実行できませんでした" in exc.value.detail
