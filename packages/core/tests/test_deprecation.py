"""廃止のライフサイクルのテスト(P2B-03、ADR-0017)。

**要点は「何をブロックし、何を報告に留めるか」の線引き**である
(ADR-0017 決定2)。決定可能であることは、ブロックすべきであることを
意味しない。ブロックが妥当なのは、規則が一義的で**かつ従う正当な手段が
常にある**ときだけである。
"""

from __future__ import annotations

import pytest

from ontology_core.deprecation import (
    DeprecationProblem,
    ProblemKind,
    check_deprecation,
    deprecated_iris_in_results,
    deprecated_terms,
)

_HEAD = """
@prefix ex: <https://e.example/#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
"""

_OLD = "https://e.example/#Old"
_NEW = "https://e.example/#New"
_LIVE = "https://e.example/#Live"


def _ttl(body: str) -> str:
    return _HEAD + body


def _kinds(problems: list[DeprecationProblem]) -> list[ProblemKind]:
    return [p.kind for p in problems]


# ---------------------------------------------------------------- 廃止の抽出


def test_廃止された用語を抽出する() -> None:
    ttl = _ttl(
        "ex:Old a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:New .\n"
        "ex:New a owl:Class .\n"
    )
    assert deprecated_terms(ttl) == frozenset({_OLD})


def test_deprecated_false_は廃止ではない() -> None:
    ttl = _ttl("ex:Old a owl:Class ; owl:deprecated false .")
    assert deprecated_terms(ttl) == frozenset()


def test_廃止が無ければ空集合() -> None:
    assert deprecated_terms(_ttl("ex:Live a owl:Class .")) == frozenset()


# ---------------------------------------------------------------- ブロックする検査


def test_後継があれば問題なし() -> None:
    ttl = _ttl(
        "ex:Old a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:New .\n"
        "ex:New a owl:Class .\n"
    )
    assert check_deprecation(ttl) == []


def test_理由があれば後継は不要() -> None:
    """**後継が無いこと自体は正当である**(ADR-0017 決定2)。

    間違って作った用語や、統合されずに消える概念には後継が無い。必須にすると
    存在しない後継を捏造させることになる。
    """
    ttl = _ttl('ex:Old a owl:Class ; owl:deprecated true ; rdfs:comment "誤って作成したため廃止" .')
    assert check_deprecation(ttl) == []


def test_skos_historyNote_も理由として認める() -> None:
    ttl = _ttl('ex:Old a owl:Class ; owl:deprecated true ; skos:historyNote "2026 年に統合" .')
    assert check_deprecation(ttl) == []


def test_後継も理由も無い廃止は問題になる() -> None:
    """「この用語は使うな」だけでは、利用者は代わりに何を使えばよいか分からない。"""
    ttl = _ttl("ex:Old a owl:Class ; owl:deprecated true .")
    problems = check_deprecation(ttl)
    assert _kinds(problems) == [ProblemKind.NO_SUCCESSOR_OR_REASON]
    assert problems[0].term == _OLD
    assert problems[0].blocking is True


def test_空の理由は理由として認めない() -> None:
    """空文字列の `rdfs:comment` で検査をすり抜けられないこと。"""
    ttl = _ttl('ex:Old a owl:Class ; owl:deprecated true ; rdfs:comment "" .')
    assert _kinds(check_deprecation(ttl)) == [ProblemKind.NO_SUCCESSOR_OR_REASON]


def test_空白だけの理由も認めない() -> None:
    ttl = _ttl('ex:Old a owl:Class ; owl:deprecated true ; rdfs:comment "   " .')
    assert _kinds(check_deprecation(ttl)) == [ProblemKind.NO_SUCCESSOR_OR_REASON]


# ---------------------------------------------------------------- 報告に留める検査


def test_生きている用語からの参照は報告に留める() -> None:
    """**ブロックしない**(ADR-0017 決定2)。

    SHACL の形状やマッピングが廃止された用語を正当に参照する。ブロックすると
    移行のための記述が書けなくなる。
    """
    ttl = _ttl(
        "ex:Old a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:New .\n"
        "ex:New a owl:Class .\n"
        "ex:Live a owl:Class ; rdfs:subClassOf ex:Old .\n"
    )
    problems = check_deprecation(ttl)
    assert _kinds(problems) == [ProblemKind.REFERENCES_DEPRECATED]
    assert problems[0].blocking is False, "移行のための記述を禁止してはいけない"
    assert problems[0].term == _LIVE
    assert problems[0].referenced == _OLD


def test_SHACL_の形状が廃止された用語を対象にできる() -> None:
    """旧データを検証する形状は書けなければならない。"""
    ttl = _ttl(
        "ex:Old a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:New .\n"
        "ex:New a owl:Class .\n"
        "ex:OldShape a sh:NodeShape ; sh:targetClass ex:Old .\n"
    )
    problems = check_deprecation(ttl)
    assert all(not p.blocking for p in problems), "形状の記述をブロックしてはいけない"


def test_dcterms_replaces_による参照は問題にしない() -> None:
    """**歴史的参照**である(ADR-0017 決定2)。「昔これがあった」を記録する
    ための述語であり、参照しているのが目的である。"""
    ttl = _ttl(
        "ex:Old a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:New .\n"
        "ex:New a owl:Class ; dcterms:replaces ex:Old .\n"
    )
    assert check_deprecation(ttl) == []


def test_rdfs_seeAlso_による参照は問題にしない() -> None:
    ttl = _ttl(
        "ex:Old a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:New .\n"
        "ex:New a owl:Class ; rdfs:seeAlso ex:Old .\n"
    )
    assert check_deprecation(ttl) == []


def test_廃止された用語同士の参照は問題にしない() -> None:
    """廃止された側からの参照は「生きている用語からの参照」ではない。"""
    ttl = _ttl(
        "ex:Old a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:New .\n"
        "ex:Older a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:Old .\n"
        "ex:New a owl:Class .\n"
    )
    assert check_deprecation(ttl) == []


def test_廃止された用語自身への記述は参照ではない() -> None:
    """`ex:Old rdfs:label "旧"` は `ex:Old` **についての**記述であって、
    生きている用語からの参照ではない。"""
    ttl = _ttl(
        'ex:Old a owl:Class ; owl:deprecated true ; rdfs:label "旧" ; '
        "dcterms:isReplacedBy ex:New .\nex:New a owl:Class .\n"
    )
    assert check_deprecation(ttl) == []


# ---------------------------------------------------------------- 削除の検出


def test_削除された用語をブロック対象として報告する() -> None:
    base = _ttl("ex:Old a owl:Class .\nex:Live a owl:Class .")
    new = _ttl("ex:Live a owl:Class .")
    problems = check_deprecation(new, base_turtle=base)
    assert _kinds(problems) == [ProblemKind.REMOVED]
    assert problems[0].term == _OLD
    assert problems[0].blocking is True


def test_廃止して残せば削除にならない() -> None:
    """**これが「縮める正当な手段」である**(ADR-0009 決定3)。"""
    base = _ttl("ex:Old a owl:Class .\nex:Live a owl:Class .")
    new = _ttl(
        "ex:Old a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:New .\n"
        "ex:New a owl:Class .\nex:Live a owl:Class .\n"
    )
    assert check_deprecation(new, base_turtle=base) == []


def test_基準が無ければ削除は検出しない() -> None:
    """最初の承認では基準が無い(ADR-0017 決定4)。"""
    assert check_deprecation(_ttl("ex:Live a owl:Class ."), base_turtle=None) == []


def test_追加は問題にならない() -> None:
    base = _ttl("ex:Live a owl:Class .")
    new = _ttl("ex:Live a owl:Class .\nex:Added a owl:Class .")
    assert check_deprecation(new, base_turtle=base) == []


# ---------------------------------------------------------------- 複合


def test_問題は種類と用語で安定して並ぶ() -> None:
    base = _ttl("ex:A a owl:Class .\nex:B a owl:Class .\nex:Live a owl:Class .")
    new = _ttl(
        "ex:Live a owl:Class ; rdfs:subClassOf ex:C .\nex:C a owl:Class ; owl:deprecated true .\n"
    )
    problems = check_deprecation(new, base_turtle=base)
    kinds = _kinds(problems)
    assert ProblemKind.REMOVED in kinds
    assert ProblemKind.NO_SUCCESSOR_OR_REASON in kinds
    assert ProblemKind.REFERENCES_DEPRECATED in kinds
    # 2 回呼んでも同じ順序であること(報告が安定しないと差分レビューが読めない)。
    assert [(p.kind, p.term) for p in problems] == [
        (p.kind, p.term) for p in check_deprecation(new, base_turtle=base)
    ]


def test_ブロックすべき問題があるかを判定できる() -> None:
    from ontology_core.deprecation import has_blocking

    only_report = _ttl(
        "ex:Old a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:New .\n"
        "ex:New a owl:Class .\nex:Live a owl:Class ; rdfs:subClassOf ex:Old .\n"
    )
    assert has_blocking(check_deprecation(only_report)) is False

    blocking = _ttl("ex:Old a owl:Class ; owl:deprecated true .")
    assert has_blocking(check_deprecation(blocking)) is True


def test_構文が壊れていれば理由付きで失敗する() -> None:
    """**問題なしと返さない。** 「検証できなかった」を「適合」と混同しない。"""
    from ontology_core.deprecation import DeprecationCheckError

    with pytest.raises(DeprecationCheckError, match="解析"):
        check_deprecation("これは Turtle ではない {{{")


# ---------------------------------------------------------------- クエリの警告


def _bindings(*iris: str) -> dict[str, object]:
    return {
        "head": {"vars": ["s"]},
        "results": {"bindings": [{"s": {"type": "uri", "value": iri}} for iri in iris]},
    }


def test_結果に現れた廃止済み_IRI_を列挙する() -> None:
    found = deprecated_iris_in_results(_bindings(_OLD, _LIVE), frozenset({_OLD}))
    assert found == (_OLD,)


def test_廃止が無ければ空を返す() -> None:
    assert deprecated_iris_in_results(_bindings(_LIVE), frozenset({_OLD})) == ()


def test_リテラルは_IRI_として扱わない() -> None:
    """`type` が `literal` の値は IRI ではない。文字列が偶然一致しても
    警告を出してはいけない。"""
    results = {
        "head": {"vars": ["s"]},
        "results": {"bindings": [{"s": {"type": "literal", "value": _OLD}}]},
    }
    assert deprecated_iris_in_results(results, frozenset({_OLD})) == ()


def test_重複は_1_件にまとめる() -> None:
    found = deprecated_iris_in_results(_bindings(_OLD, _OLD, _OLD), frozenset({_OLD}))
    assert found == (_OLD,)


def test_列挙は安定して並ぶ() -> None:
    found = deprecated_iris_in_results(_bindings(_NEW, _OLD), frozenset({_OLD, _NEW}))
    assert list(found) == sorted(found)


def test_ASK_の結果でも落ちない() -> None:
    """`ASK` の結果に `results` は無く `boolean` がある。"""
    assert deprecated_iris_in_results({"head": {}, "boolean": True}, frozenset({_OLD})) == ()


def test_想定外の形でも落ちない() -> None:
    """ストアを差し替えられる設計なので(ADR-0001)、結果の形に過度な前提を
    置かない。**警告のために本来の応答を壊してはいけない。**"""
    assert deprecated_iris_in_results({}, frozenset({_OLD})) == ()
    assert deprecated_iris_in_results({"results": None}, frozenset({_OLD})) == ()
    assert deprecated_iris_in_results({"results": {"bindings": "x"}}, frozenset({_OLD})) == ()
    # **反復できない値**でも例外にしない(ガードを外すと TypeError で落ちる)。
    assert deprecated_iris_in_results({"results": {"bindings": 5}}, frozenset({_OLD})) == ()
    assert deprecated_iris_in_results({"results": {"bindings": [None]}}, frozenset({_OLD})) == ()


def test_廃止の集合が空なら走査しない() -> None:
    """廃止が 1 件も無い名前空間で結果を走査するのは無駄である。"""
    assert deprecated_iris_in_results(_bindings(_OLD), frozenset()) == ()
