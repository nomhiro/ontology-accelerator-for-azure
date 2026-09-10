"""正本の TTL に対する想定質問のインメモリ評価(ADR-0022、`P2B-14`)。

ストア(Fuseki)に対する実行は `packages/core/tests/test_competency.py` と
`packages/api/tests/test_competency_run.py` が見る。ここで見るのは
**`approve` の検査に使う経路**である(ADR-0022 決定3)。
"""

from __future__ import annotations

import pytest

from ontology_core.competency import (
    MAX_QUESTION_SET_BYTES,
    MAX_QUESTIONS,
    CompetencyQuestion,
    CompetencyReport,
    QuestionFileError,
    QuestionResult,
    content_hash,
    evaluate_questions_on_turtle,
    parse_questions,
)

_TTL = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex:   <https://e.example/#> .

ex:Product a owl:Class ; rdfs:label "商品" .
ex:Order   a owl:Class ; rdfs:label "注文" .
ex:orderedBy a owl:ObjectProperty ; rdfs:domain ex:Order ; rdfs:range ex:Product .
"""

_GRAPH_IRI = "urn:ontology:graph/retail/1.0.0"


def _questions(body: str) -> list[CompetencyQuestion]:
    return parse_questions(body, where="テスト")


# ------------------------------------------------------------------ 基本


def test_満たしている質問は通る() -> None:
    questions = _questions("""
questions:
  - id: q1
    question: 注文から商品へ辿れるか
    expect: ask_true
    sparql: |
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      PREFIX ex: <https://e.example/#>
      ASK { ?p rdfs:domain ex:Order ; rdfs:range ex:Product }
""")
    report = evaluate_questions_on_turtle(_TTL, questions)
    assert report.conforms
    assert report.evaluated_all
    assert report.failures == ()


def test_満たしていない質問は落ちる() -> None:
    questions = _questions("""
questions:
  - id: q1
    question: 返品の語彙があるか
    expect: ask_true
    sparql: |
      PREFIX ex: <https://e.example/#>
      ASK { ex:Return ?p ?o }
""")
    report = evaluate_questions_on_turtle(_TTL, questions)
    assert not report.conforms
    assert [r.question.id for r in report.failures] == ["q1"]
    assert "ASK -> False" in report.failures[0].detail


def test_規約の質問_empty_が使える() -> None:
    """`expect: empty` が ADR-0009 決定1 の「合意済みの規約」を担う。"""
    questions = _questions("""
questions:
  - id: rule-1
    question: ラベルの無いクラスが無いこと
    expect: empty
    sparql: |
      PREFIX owl:  <http://www.w3.org/2002/07/owl#>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      SELECT ?c WHERE { ?c a owl:Class . FILTER NOT EXISTS { ?c rdfs:label ?l } }
""")
    report = evaluate_questions_on_turtle(_TTL, questions)
    assert report.conforms, report.messages()
    assert report.results[0].detail == "0 行"


def test_規約に反していれば落ちる() -> None:
    ttl = _TTL + "\nex:NoLabel a <http://www.w3.org/2002/07/owl#Class> .\n"
    questions = _questions("""
questions:
  - id: rule-1
    question: ラベルの無いクラスが無いこと
    expect: empty
    sparql: |
      PREFIX owl:  <http://www.w3.org/2002/07/owl#>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      SELECT ?c WHERE { ?c a owl:Class . FILTER NOT EXISTS { ?c rdfs:label ?l } }
""")
    report = evaluate_questions_on_turtle(ttl, questions)
    assert not report.conforms
    # **行数は数えていないので「1 行以上」と出る**(ADR-0022 決定4)。
    assert report.results[0].detail == "1 行以上"


def test_行数は数えない() -> None:
    """判定に必要なのは存在の有無だけである(ADR-0022 決定4)。

    **「3 行」と書けるふりをしない。** 実測で 216,000 行の直積を全部
    materialize すると 9.68 秒、先頭 1 行なら 0.000 秒だった。
    """
    questions = _questions("""
questions:
  - id: q1
    question: トリプルが 1 つ以上あるか
    expect: non_empty
    sparql: SELECT ?s ?p ?o WHERE { ?s ?p ?o }
""")
    report = evaluate_questions_on_turtle(_TTL, questions)
    assert report.conforms
    assert report.results[0].detail == "1 行以上"
    assert "行" in report.results[0].detail
    # 行数が入っていたら数えてしまっている。
    assert not any(ch.isdigit() and ch != "1" for ch in report.results[0].detail)


# ------------------------------------------------ 形が噛み合わないとき


def test_ASK_を期待して_SELECT_を書いたら落ちる() -> None:
    """**偶然の合否で隠さない。** 通したり落としたりせず「形が違う」とする。"""
    questions = _questions("""
questions:
  - id: q1
    question: 何か
    expect: ask_true
    sparql: SELECT ?s WHERE { ?s ?p ?o }
""")
    report = evaluate_questions_on_turtle(_TTL, questions)
    assert not report.conforms
    assert "ASK の応答ではありません" in report.results[0].detail


def test_SELECT_を期待して_ASK_を書いたら落ちる() -> None:
    questions = _questions("""
questions:
  - id: q1
    question: 何か
    expect: empty
    sparql: ASK { ?s ?p ?o }
""")
    report = evaluate_questions_on_turtle(_TTL, questions)
    assert not report.conforms
    assert "SELECT の応答ではありません" in report.results[0].detail


def test_クエリが実行できなくても合格にしない() -> None:
    """**評価できなかったことを合格に丸めない。**

    `parse_questions` は構文を検査しないので(SPARQL の構文検証は rdflib に
    任せる)、実行時に落ちる質問は入りうる。
    """
    questions = _questions("""
questions:
  - id: q1
    question: 壊れたクエリ
    expect: ask_true
    sparql: ASK { ?s ?p
""")
    report = evaluate_questions_on_turtle(_TTL, questions)
    assert not report.conforms
    assert not report.results[0].passed


def test_TTL_が壊れていれば評価しない() -> None:
    """**「解析できなかった」を「全部通った」にしない。**"""
    questions = _questions("""
questions:
  - id: q1
    question: 何か
    expect: ask_true
    sparql: ASK { ?s ?p ?o }
""")
    with pytest.raises(QuestionFileError, match="TTL を解析できない"):
        evaluate_questions_on_turtle("これは Turtle ではない {{{", questions)


# ------------------------------------------------------ グラフの見え方


def test_名前付きグラフにも同じTTLを載せる() -> None:
    """射影後の姿を写す(ADR-0010 決定5・6、ADR-0022 決定3)。

    承認済み現行版は既定グラフと名前付きグラフの両方に載るので、
    `GRAPH <...>` を書いた質問がストアと同じように動く必要がある。
    """
    questions = _questions(f"""
questions:
  - id: q1
    question: 名前付きグラフから引けるか
    expect: ask_true
    sparql: |
      PREFIX owl: <http://www.w3.org/2002/07/owl#>
      ASK {{ GRAPH <{_GRAPH_IRI}> {{ ?s a owl:Class }} }}
""")
    assert evaluate_questions_on_turtle(_TTL, questions, graph_iri=_GRAPH_IRI).conforms
    # `graph_iri` を渡さなければ名前付きグラフは無いので落ちる。
    assert not evaluate_questions_on_turtle(_TTL, questions).conforms


def test_既定グラフは名前付きグラフの和集合ではない() -> None:
    """`GRAPH` 句なしのクエリが名前付きグラフを拾ってはいけない。

    ADR-0009 決定2 / ADR-0010 決定6 が `unionDefaultGraph` をやめた理由と
    同じである。**ここが和集合だと、評価環境がストアと食い違う。**
    """
    questions = _questions("""
questions:
  - id: q1
    question: 既定グラフに何かあるか
    expect: non_empty
    sparql: SELECT ?s WHERE { ?s ?p ?o }
""")
    # 既定グラフにも載せているので通る(和集合かどうかの判定はできないが、
    # 既定グラフが空でないことは確かめられる)。
    assert evaluate_questions_on_turtle(_TTL, questions, graph_iri=_GRAPH_IRI).conforms


# ---------------------------------------------------------------- 予算


def test_予算を超えたら残りを未評価として返す() -> None:
    """**「評価していない」を「通った」に丸めない**(ADR-0022 決定5)。"""
    questions = _questions("""
questions:
  - id: q1
    question: 1 件目
    expect: ask_true
    sparql: ASK { ?s ?p ?o }
  - id: q2
    question: 2 件目
    expect: ask_true
    sparql: ASK { ?s ?p ?o }
  - id: q3
    question: 3 件目
    expect: ask_true
    sparql: ASK { ?s ?p ?o }
""")
    # 予算 0 なら 1 件目だけ評価して打ち切る(**1 件目は必ず評価する** —
    # 0 件で返すと「質問が無かった」と区別がつかない)。
    report = evaluate_questions_on_turtle(_TTL, questions, budget_seconds=0.0)
    assert len(report.results) == 1
    assert report.not_evaluated == ("q2", "q3")
    assert not report.evaluated_all
    # **全部通っていても conforms は偽である。**
    assert all(r.passed for r in report.results)
    assert not report.conforms


def test_未評価があることがメッセージに出る() -> None:
    report = CompetencyReport(not_evaluated=("q9",))
    assert any("q9" in m and "評価していません" in m for m in report.messages())


def test_予算が足りていれば全件評価する() -> None:
    questions = _questions("""
questions:
  - id: q1
    question: 1 件目
    expect: ask_true
    sparql: ASK { ?s ?p ?o }
  - id: q2
    question: 2 件目
    expect: ask_true
    sparql: ASK { ?s ?p ?o }
""")
    report = evaluate_questions_on_turtle(_TTL, questions, budget_seconds=60.0)
    assert report.evaluated_all
    assert len(report.results) == 2
    assert report.conforms


# ------------------------------------------------------ parse_questions


def test_質問が多すぎれば拒否する() -> None:
    """**承認経路で全件を評価する**ので件数に上限が要る(ADR-0022 決定5)。"""
    body = "questions:\n" + "".join(
        f"  - id: q{i}\n    question: q\n    expect: ask_true\n    sparql: ASK {{ ?s ?p ?o }}\n"
        for i in range(MAX_QUESTIONS + 1)
    )
    with pytest.raises(QuestionFileError, match="質問が多すぎます"):
        parse_questions(body)


def test_上限ちょうどは通る() -> None:
    body = "questions:\n" + "".join(
        f"  - id: q{i}\n    question: q\n    expect: ask_true\n    sparql: ASK {{ ?s ?p ?o }}\n"
        for i in range(MAX_QUESTIONS)
    )
    assert len(parse_questions(body)) == MAX_QUESTIONS


def test_本文が大きすぎれば拒否する() -> None:
    body = "questions:\n  - id: q1\n    question: " + ("あ" * MAX_QUESTION_SET_BYTES)
    with pytest.raises(QuestionFileError, match="本文が大きすぎます"):
        parse_questions(body)


def test_SERVICE_句は拒否する() -> None:
    """**インメモリ評価では一層重要である。** rdflib も `SERVICE` で外部へ
    HTTP を投げるため、承認経路が SSRF の入口になる。
    """
    body = """
questions:
  - id: q1
    question: 外部を引く
    expect: ask_true
    sparql: |
      ASK { SERVICE <http://169.254.169.254/> { ?s ?p ?o } }
"""
    with pytest.raises(QuestionFileError):
        parse_questions(body)


def test_更新クエリは拒否する() -> None:
    body = """
questions:
  - id: q1
    question: 書き換える
    expect: ask_true
    sparql: |
      INSERT DATA { <urn:a> <urn:b> <urn:c> }
"""
    with pytest.raises(QuestionFileError):
        parse_questions(body)


def test_同じ本文なら同じハッシュ() -> None:
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")
    assert len(content_hash("abc")) == 64


# ------------------------------------------------------------ conforms


def test_conforms_は全件評価と全件合格の両方を要求する() -> None:
    """片方だけだと、評価していないものを合格に丸める実装が通ってしまう。"""
    from ontology_core.competency import Expectation

    q = CompetencyQuestion(id="q1", question="q", expect=Expectation.ASK_TRUE, sparql="ASK {}")
    passed = QuestionResult(q, True, "ASK -> True")
    failed = QuestionResult(q, False, "ASK -> False")

    assert CompetencyReport(results=(passed,)).conforms
    assert not CompetencyReport(results=(failed,)).conforms
    assert not CompetencyReport(results=(passed,), not_evaluated=("q2",)).conforms
    # 質問が 1 件も無いときは真(質問集合が無い名前空間の扱いは呼び出し側)。
    assert CompetencyReport().conforms
