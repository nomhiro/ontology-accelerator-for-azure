"""同梱サンプルの想定質問を実 Fuseki に対して回す(P2B-07、ADR-0009 決定6)。

**これが CI で回る本体である。** 「このオントロジーは○○に答えられなければ
ならない」を主観なしに判定するのが決定6 の目的なので、フェイクではなく
実際に承認・射影された既定グラフに対して実行する。

`packages/core/tests/test_competency.py` はファイルの検証と判定ロジックを
ストア抜きで見ている。ここは経路全体を見る。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.services.projection import ProjectionService
from ontology_core.blob import OntologyBlobStore
from ontology_core.competency import Expectation, load_questions, run_questions
from ontology_core.sparql.client import FusekiStore

pytestmark = pytest.mark.integration

_PORT = os.environ.get("FUSEKI_PORT", "3030")
_BASE = f"http://localhost:{_PORT}"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SAMPLE_TTL = _REPO_ROOT / "samples" / "retail-core.ttl"
_SAMPLE_QUESTIONS = _REPO_ROOT / "samples" / "retail-core.questions.yaml"


@pytest.fixture
async def store() -> AsyncIterator[FusekiStore]:
    s = FusekiStore(
        query_endpoint=_BASE + "/{dataset}/sparql",
        update_endpoint=_BASE + "/{dataset}/update",
        gsp_endpoint=_BASE + "/{dataset}/data",
        admin_endpoint=_BASE + "/$/",
        admin_auth=("admin", os.environ.get("FUSEKI_ADMIN_PASSWORD", "localdev")),
    )
    yield s
    await s.aclose()


@pytest.fixture
async def approved_sample(
    session: AsyncSession, blob_store: OntologyBlobStore, store: FusekiStore
) -> str:
    """同梱サンプルを承認済みにして、既定グラフに載った状態を作る。"""
    name = "cq-sample"
    # 前のテストの内容が残らないよう作り直す(test_state_projection.py と同じ理由)。
    if name in await store.list_datasets():
        await store.delete_dataset(name)
    await store.create_dataset(name)
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri="https://example.com/ontology/retail#",
        created_by="t",
    )
    await session.commit()

    svc = ProjectionService(
        session=session, blob=blob_store, store=store, graph_iri_base="urn:ontology:graph"
    )
    turtle = _SAMPLE_TTL.read_text(encoding="utf-8")
    draft = await svc.publish(
        namespace=name, turtle=turtle, actor="t", reason="想定質問テストのため"
    )
    await svc.submit(namespace=name, version=draft.version, actor="t")
    # 四眼原則(ADR-0014)があるため承認は別の主体で行う。
    await svc.approve(namespace=name, version=draft.version, actor="reviewer")
    return name


async def test_bundled_sample_answers_all_its_competency_questions(
    store: FusekiStore, approved_sample: str
) -> None:
    """同梱サンプルが自分の想定質問すべてに答えられること。

    **落ちたら、落ちた質問と理由がそのまま出る。** ADR-0009 が品質スコア型の
    評価を採らなかったのはこのためで、点数ではなく「何を直すべきか」が分かる。
    """
    questions = load_questions(_SAMPLE_QUESTIONS)
    results = await run_questions(store, approved_sample, questions)

    failed = [r for r in results if not r.passed]
    assert not failed, "\n".join(
        f"  {r.question.id}: {r.question.question} -> {r.detail}" for r in failed
    )
    # 3 つの判定モードのうち、このサンプルでは 2 つを実際に使っている
    # (データが無いので non_empty は Phase 3 まで書けない)。
    used = {r.question.expect for r in results}
    assert Expectation.ASK_TRUE in used
    assert Expectation.EMPTY in used


async def test_a_question_fails_when_the_ontology_does_not_answer_it(
    store: FusekiStore, approved_sample: str
) -> None:
    """答えられない質問はちゃんと落ちること(テストに歯があることの確認)。

    通るだけのテストを書かないため、**このオントロジーが満たさない要求**を
    わざと投げて失敗を確認する。
    """
    questions = load_questions(_SAMPLE_QUESTIONS)
    from ontology_core.competency import CompetencyQuestion

    impossible = CompetencyQuestion(
        id="cq-does-not-hold",
        question="返品を表現できるか(このサンプルには無い概念)",
        expect=Expectation.ASK_TRUE,
        sparql=(
            "PREFIX retail: <https://example.com/ontology/retail#>\n"
            "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
            "ASK { retail:Return a owl:Class }"
        ),
    )
    results = await run_questions(store, approved_sample, [*questions, impossible])

    failed = [r for r in results if not r.passed]
    assert [r.question.id for r in failed] == ["cq-does-not-hold"]
