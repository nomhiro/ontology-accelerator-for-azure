"""TTL(Turtle)の構文検証(P1-C2)。

`ProjectionService.publish` が Blob(正本)へ書く前に呼ぶ検証そのもの。
ここではストア・DB を介さない純粋なパース挙動だけを確認する
(`packages/api/tests/test_projection.py` / `test_versions_router.py` が
publish 経路への組み込みを確認する)。
"""

from __future__ import annotations

import pytest

from ontology_core.turtle import (
    TurtleSyntaxError,
    iri_subjects,
    term_iris_with_prefix,
    validate_turtle,
)

VALID_TTL = "@prefix ex: <https://e.example/#> .\nex:A a ex:Class .\n"


def test_valid_turtle_is_accepted() -> None:
    validate_turtle(VALID_TTL)  # 例外が起きなければ成功


@pytest.mark.parametrize(
    "broken",
    [
        # 述語だけで終わる。実測では rdflib は BadSyntax ではなく IndexError を
        # 投げる(ブリーフに挙げられた例そのもの)。
        "@prefix ex: <http://e/> . ex:A a",
        # 角括弧が閉じていない。rdflib.plugins.parsers.notation3.BadSyntax の経路。
        "@prefix ex: <http://e/> .\nex:A ex:p [ ex:q ex:r .",
        # TTL として全く体をなしていない。
        "this is not turtle at all !!! ###",
        # 文字列リテラルが閉じていない。実測では AssertionError の経路。
        '@prefix ex: <http://e/> .\nex:A ex:p "unterminated .',
    ],
)
def test_broken_turtle_is_rejected(broken: str) -> None:
    """rdflib は構文エラーの型が一貫していない(BadSyntax・IndexError・
    AssertionError のいずれも投げる、実測で確認済み)。型を絞ると検証を
    すり抜けるため、どの経路でも TurtleSyntaxError に正規化されることを確認する。
    """
    with pytest.raises(TurtleSyntaxError):
        validate_turtle(broken)


# ---------------------------------------------------------------------------
# 用語の抽出 (P2B-06、ADR-0020)
# ---------------------------------------------------------------------------

_TERMS_HEAD = """
@prefix ex: <https://e.example/#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
"""
_PREFIX = "https://e.example/#"


def test_接頭辞を持つ用語だけを返す() -> None:
    """**外部語彙が主語として現れても数えない**(ADR-0020 決定1)。

    ここが本題。`owl:Thing` を**主語**にした行を入れてある — 目的語に
    しか現れないと、`iri_subjects` の側で落ちるため接頭辞の絞り込みを
    検証できない(実際に変異テストで見逃した)。
    """
    ttl = _TERMS_HEAD + (
        "ex:Product a owl:Class .\n"
        "ex:Customer a owl:Class .\n"
        'owl:Thing rdfs:label "すべてのもの" .\n'
        '<https://schema.org/Product> rdfs:label "外部の商品" .\n'
    )
    assert term_iris_with_prefix(ttl, _PREFIX) == {
        _PREFIX + "Product",
        _PREFIX + "Customer",
    }


def test_接頭辞が空なら空集合を返す() -> None:
    """`base_iri` が設定されていない名前空間で全 IRI を数えてしまわないこと。

    `rdf:type` まで用語に数えると指標が意味を失う。
    """
    ttl = _TERMS_HEAD + "ex:Product a owl:Class .\n"
    assert term_iris_with_prefix(ttl, "") == set()


def test_空白ノードは用語に数えない() -> None:
    """空白ノードは構造であって用語ではない。IRI を持たないので参照もできない。"""
    ttl = _TERMS_HEAD + "ex:S a sh:NodeShape ; sh:property [ sh:path ex:name ] .\n"
    assert term_iris_with_prefix(ttl, _PREFIX) == {_PREFIX + "S"}


def test_目的語にしか現れない_IRI_は用語に数えない() -> None:
    """用語は「定義されているもの」であり、参照されているだけのものではない。"""
    ttl = _TERMS_HEAD + "ex:Product rdfs:subClassOf ex:Thing .\n"
    assert term_iris_with_prefix(ttl, _PREFIX) == {_PREFIX + "Product"}


def test_空の_TTL_は空集合() -> None:
    assert term_iris_with_prefix("", _PREFIX) == set()
    assert term_iris_with_prefix("   \n  ", _PREFIX) == set()


def test_壊れた_TTL_は例外にする() -> None:
    """**空集合を返さない。** 「用語が無い」と「解析できなかった」を混同すると、
    健全性指標が障害時に「健全」と報告する(ADR-0020 決定3)。
    """
    with pytest.raises(TurtleSyntaxError, match="解析できません"):
        term_iris_with_prefix("これは Turtle ではない {{{", _PREFIX)


def test_iri_subjects_は空白ノードを含めない() -> None:
    from rdflib import Graph

    graph = Graph()
    graph.parse(
        data=_TERMS_HEAD + "ex:S a sh:NodeShape ; sh:property [ sh:path ex:name ] .\n",
        format="turtle",
    )
    assert iri_subjects(graph) == {_PREFIX + "S"}
