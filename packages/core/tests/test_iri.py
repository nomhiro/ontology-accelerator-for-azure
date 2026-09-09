"""用語 IRI の検証のテスト(ADR-0015 決定3、`P2B-04`)。

**何を通し、何を弾くかが要点**である。ADR-0015 決定3 は
「絶対 IRI であればよく、`base_iri` 配下に限らない」と決めた
(外部語彙へのマッピングの責任者を記録できなくなるため)。
一方、空白や制御文字は弾く — 将来 SPARQL に埋め込むときの事故を
入口で潰しておく。
"""

from __future__ import annotations

import pytest

from ontology_core.iri import TermIriError, validate_term_iri


@pytest.mark.parametrize(
    "iri",
    [
        "https://example.com/ontology/retail#Product",
        "http://example.com/ontology/retail/Product",
        "urn:example:retail:Product",
        # **外部語彙も通す。** ADR-0015 決定3。外部 IRI へのマッピングの
        # 責任者を記録するために必要である。
        "http://www.w3.org/2004/02/skos/core#Concept",
        "https://schema.org/Product",
        # 国際化された IRI。RDF は IRI を許すので ASCII に限らない。
        "https://example.com/オントロジー#商品",
        # クエリや括弧を含む IRI も RDF としては合法。
        "https://example.com/t?a=1&b=2",
    ],
)
def test_通す(iri: str) -> None:
    assert validate_term_iri(iri) == iri


@pytest.mark.parametrize(
    ("iri", "reason"),
    [
        ("", "空"),
        ("   ", "空白のみ"),
        ("Product", "スキームが無い(相対 IRI)"),
        ("/ontology/retail#Product", "スキームが無い(絶対パス)"),
        ("#Product", "スキームが無い(フラグメントのみ)"),
        ("https://example.com/a b", "空白を含む"),
        ("https://example.com/a\tb", "タブを含む"),
        ("https://example.com/a\nb", "改行を含む"),
        ("https://example.com/a\x00b", "NUL を含む"),
        ("https://example.com/<a>", "山括弧を含む"),
        ('https://example.com/"a"', "二重引用符を含む"),
        ("https://example.com/{a}", "波括弧を含む"),
        ("1https://example.com/a", "スキームが英字で始まっていない"),
    ],
)
def test_弾く(iri: str, reason: str) -> None:
    with pytest.raises(TermIriError):
        validate_term_iri(iri)


def test_前後の空白は許さない() -> None:
    """**黙って strip しない。** 「見た目が同じで別の行」を作らないため。

    strip すると、同じ用語に対して空白違いの 2 行が入りうる状態を
    「入らない」と誤認させる。一意制約は正規化された値に対して働くので、
    正規化を入口で分散させてはいけない。
    """
    with pytest.raises(TermIriError):
        validate_term_iri(" https://example.com/a")
    with pytest.raises(TermIriError):
        validate_term_iri("https://example.com/a ")


def test_長すぎる_IRI_は弾く() -> None:
    """DB の列長(1024)を超える値を、DB のエラーではなく検証で弾く。

    DB 側のエラーは 500 になり、呼び出し元に何が悪いか伝わらない。
    """
    with pytest.raises(TermIriError, match="長すぎ"):
        validate_term_iri("https://example.com/" + "a" * 1024)


def test_理由が例外メッセージに入る() -> None:
    """「使えません」だけでは、呼び出し元が何を直すか分からない。"""
    with pytest.raises(TermIriError, match="絶対 IRI"):
        validate_term_iri("Product")
    with pytest.raises(TermIriError, match="使えない文字"):
        validate_term_iri("https://example.com/a b")
