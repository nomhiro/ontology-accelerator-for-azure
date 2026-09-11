"""想定質問ファイルの読み込みと判定のテスト(P2B-07、ADR-0009 決定6)。

ストアを必要としない部分(ファイルの検証と応答の判定)をここで見る。
実 Fuseki に対する実行は `packages/api/tests/test_competency_run.py`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ontology_core.competency import (
    CompetencyQuestion,
    Expectation,
    QuestionFileError,
    load_questions,
    run_questions,
)
from ontology_core.sparql.client import SparqlStore, SparqlStoreError

_OK_QUESTION = """
questions:
  - id: q1
    question: クラスが 1 つ以上あるか
    expect: ask_true
    sparql: |
      ASK { ?s a ?o }
"""


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "questions.yaml"
    p.write_text(body, encoding="utf-8")
    return p


# ---- 読み込みと検証 ----


def test_load_questions_reads_a_valid_file(tmp_path: Path) -> None:
    questions = load_questions(_write(tmp_path, _OK_QUESTION))
    assert [q.id for q in questions] == ["q1"]
    assert questions[0].expect is Expectation.ASK_TRUE


def test_bundled_sample_questions_are_valid() -> None:
    """同梱サンプルの質問ファイルが常に読めること。

    ここが落ちるのは、質問ファイルを壊したまま気づいていない状態である。
    """
    root = Path(__file__).resolve().parents[3]
    questions = load_questions(root / "samples" / "retail-core.questions.yaml")
    assert len(questions) >= 6
    assert len({q.id for q in questions}) == len(questions)


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(QuestionFileError):
        load_questions(tmp_path / "does-not-exist.yaml")


def test_broken_yaml_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(QuestionFileError):
        load_questions(_write(tmp_path, "questions: [unclosed"))


def test_missing_questions_key_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(QuestionFileError):
        load_questions(_write(tmp_path, "other: 1\n"))


def test_empty_question_list_is_an_error(tmp_path: Path) -> None:
    """0 件は「通った」ではなく「何も検証していない」なので失敗させる。"""
    with pytest.raises(QuestionFileError):
        load_questions(_write(tmp_path, "questions: []\n"))


def test_duplicate_id_is_an_error(tmp_path: Path) -> None:
    body = (
        _OK_QUESTION
        + """
  - id: q1
    question: 別の質問だが id が重複
    expect: ask_true
    sparql: |
      ASK { ?s a ?o }
"""
    )
    with pytest.raises(QuestionFileError) as exc_info:
        load_questions(_write(tmp_path, body))
    assert "重複" in str(exc_info.value)


def test_unknown_expect_is_an_error(tmp_path: Path) -> None:
    body = """
questions:
  - id: q1
    question: 判定モードが未知
    expect: probably_fine
    sparql: |
      ASK { ?s a ?o }
"""
    with pytest.raises(QuestionFileError) as exc_info:
        load_questions(_write(tmp_path, body))
    # 使える値を示して、書いた人が自力で直せるようにする。
    assert "ask_true" in str(exc_info.value)


@pytest.mark.parametrize("missing", ["id", "question", "expect", "sparql"])
def test_missing_required_field_is_an_error(tmp_path: Path, missing: str) -> None:
    fields = {
        "id": "q1",
        "question": "何か",
        "expect": "ask_true",
        "sparql": "ASK { ?s a ?o }",
    }
    del fields[missing]
    body = "questions:\n  - " + "\n    ".join(f"{k}: {v}" for k, v in fields.items()) + "\n"
    with pytest.raises(QuestionFileError):
        load_questions(_write(tmp_path, body))


def test_update_query_is_rejected(tmp_path: Path) -> None:
    """想定質問がストアを書き換えてよい理由は無い。"""
    body = """
questions:
  - id: q1
    question: 書き換えようとする質問
    expect: ask_true
    sparql: |
      INSERT DATA { <urn:a> <urn:b> <urn:c> }
"""
    with pytest.raises(QuestionFileError):
        load_questions(_write(tmp_path, body))


def test_service_clause_is_rejected(tmp_path: Path) -> None:
    """SERVICE 句は SSRF の経路になる(設計原則のセキュリティ対策)。"""
    body = """
questions:
  - id: q1
    question: 外部を呼ぶ質問
    expect: ask_true
    sparql: |
      ASK { SERVICE <http://169.254.169.254/> { ?s ?p ?o } }
"""
    with pytest.raises(QuestionFileError):
        load_questions(_write(tmp_path, body))


# ---- 判定 ----


class _CannedStore(SparqlStore):
    """あらかじめ決めた応答を返すストア。"""

    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self._payload = payload

    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

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


def _q(expect: Expectation) -> CompetencyQuestion:
    return CompetencyQuestion(id="q", question="何か", expect=expect, sparql="ASK {}")


def _rows(n: int) -> dict[str, Any]:
    return {"head": {"vars": ["s"]}, "results": {"bindings": [{"s": {}} for _ in range(n)]}}


@pytest.mark.parametrize(
    ("expect", "payload", "passed"),
    [
        (Expectation.ASK_TRUE, {"boolean": True}, True),
        (Expectation.ASK_TRUE, {"boolean": False}, False),
        (Expectation.NON_EMPTY, _rows(1), True),
        (Expectation.NON_EMPTY, _rows(0), False),
        (Expectation.EMPTY, _rows(0), True),
        (Expectation.EMPTY, _rows(3), False),
    ],
)
async def test_judgement(expect: Expectation, payload: dict[str, Any], passed: bool) -> None:
    results = await run_questions(_CannedStore(payload), "ds", [_q(expect)])
    assert results[0].passed is passed


async def test_ask_expectation_with_a_select_response_fails_loudly() -> None:
    """応答の形が期待と噛み合わないときは、偶然の合否で隠さず失敗させる。"""
    results = await run_questions(_CannedStore(_rows(5)), "ds", [_q(Expectation.ASK_TRUE)])
    assert results[0].passed is False
    assert "ask_true" in results[0].detail


async def test_select_expectation_with_an_ask_response_fails_loudly() -> None:
    results = await run_questions(_CannedStore({"boolean": True}), "ds", [_q(Expectation.EMPTY)])
    assert results[0].passed is False
    assert "SELECT" in results[0].detail


async def test_store_failure_is_reported_as_that_question_failing() -> None:
    store = _CannedStore(SparqlStoreError("接続できません(テスト)"))
    results = await run_questions(store, "ds", [_q(Expectation.ASK_TRUE)])
    assert results[0].passed is False
    assert "実行に失敗" in results[0].detail


async def test_one_failure_does_not_stop_the_rest() -> None:
    """1 件の失敗で打ち切らない。どの質問が落ちたかを全部知りたい。"""
    questions = [
        CompetencyQuestion(id="a", question="1", expect=Expectation.ASK_TRUE, sparql="ASK {}"),
        CompetencyQuestion(id="b", question="2", expect=Expectation.EMPTY, sparql="SELECT * {}"),
    ]
    results = await run_questions(_CannedStore({"boolean": False}), "ds", questions)
    assert [r.question.id for r in results] == ["a", "b"]
    assert [r.passed for r in results] == [False, False]
