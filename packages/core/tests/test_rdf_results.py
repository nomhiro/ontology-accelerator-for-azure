"""`CONSTRUCT` / `DESCRIBE` の結果の検査と正規化(ADR-0034、`P2A-14`)。

ここで固定するのは 3 つである。

1. **上限を超えたら切り詰めずに例外**(決定4)。**行数(ADR-0025 決定5)とは
   意図的に違う判断である** — RDF には「切り詰めた」と書く封筒が無く、
   ヘッダに書いてもエージェントは見ない(ADR-0017 決定3)
2. **解析できなかったことを「該当なし」に丸めない**。空のグラフを返さない
3. **`0` と `None` を区別する**(`count_triples`)

**検査はパースし直したグラフに対して行う**(`test_prov.py` と同じ理由)。
"""

from __future__ import annotations

import pytest
from rdflib import Graph, URIRef

from ontology_core.sparql.rdf_results import (
    RdfParseError,
    TripleLimitExceededError,
    count_triples,
    load_construct_result,
    terms_in_graph,
)

_BASE = "https://e.example/#"


def _turtle(count: int) -> str:
    lines = [f"@prefix e: <{_BASE}> ."]
    lines += [f"e:S{i} e:p e:O{i} ." for i in range(count)]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 上限


def test_上限以下ならそのまま返る() -> None:
    result = load_construct_result(_turtle(5), limit=10)
    assert result.triple_count == 5
    graph = Graph()
    graph.parse(data=result.turtle, format="turtle")
    assert len(graph) == 5


def test_上限ちょうどは通す() -> None:
    """**境界で断ると 1 件足りない結果で拒否する**(`cap_bindings` と同じ判断)。"""
    assert load_construct_result(_turtle(10), limit=10).triple_count == 10


def test_上限を超えたら切り詰めずに例外() -> None:
    """**ADR-0025 決定5 とは意図的に違う判断である**(ADR-0034 決定4)。

    行数は切り詰めて旗を立てるが、RDF には**旗を立てる場所が無い**。
    ヘッダに書いてもエージェントは見ない(ADR-0017 決定3)ので、
    **切り詰めた RDF は、切り詰めたと言えないまま完全な RDF として届く**。
    """
    with pytest.raises(TripleLimitExceededError) as exc:
        load_construct_result(_turtle(11), limit=10)
    assert exc.value.triples == 11
    assert exc.value.limit == 10
    # **実数を返す。** 「多すぎます」だけでは、どれだけ絞ればよいか分からない。
    assert "11" in str(exc.value)
    assert "切り詰めて返しません" in str(exc.value)


def test_上限_1_でも動く() -> None:
    assert load_construct_result(_turtle(1), limit=1).triple_count == 1
    with pytest.raises(TripleLimitExceededError):
        load_construct_result(_turtle(2), limit=1)


def test_空のグラフは_0_トリプル() -> None:
    """**「0 件」は正常な結果である。** 例外にしない。"""
    result = load_construct_result("", limit=10)
    assert result.triple_count == 0


# ------------------------------------------------------------ 解析の失敗


def test_解析できない本文は例外にする() -> None:
    """**空のグラフを返さない**(ADR-0034 決定6 の裏返し)。

    「解析できなかった」を「該当なし」と混同すると、エージェントが
    誤った結論を出す。
    """
    with pytest.raises(RdfParseError):
        load_construct_result("これは Turtle ではない <<<", limit=10)


# -------------------------------------------------------------- 正規化


def test_解析し直した_Turtle_を返す() -> None:
    """**ストアの本文をそのまま転送しない**(ADR-0034 決定6)。

    上限の検査のためにどうせ解析するので、**形を 1 つに決める**ほうがよい。
    持ち込みストアが Turtle の書き方を変えても、この API の応答は変わらない。
    """
    # 接頭辞を使わない書き方で渡しても、同じグラフが返る。
    body = f"<{_BASE}S0> <{_BASE}p> <{_BASE}O0> .\n"
    result = load_construct_result(body, limit=10)
    graph = Graph()
    graph.parse(data=result.turtle, format="turtle")
    assert (URIRef(_BASE + "S0"), URIRef(_BASE + "p"), URIRef(_BASE + "O0")) in graph


# --------------------------------------------------------- 用語の数え方


def test_グラフの主語述語目的語から用語を数える() -> None:
    """**`SELECT` の束縛より正確である**(ADR-0034 決定7)。

    束縛に現れない IRI も拾える。
    """
    found = terms_in_graph(_turtle(2), base_iri=_BASE)
    assert found == (
        _BASE + "O0",
        _BASE + "O1",
        _BASE + "S0",
        _BASE + "S1",
        _BASE + "p",
    )


def test_他の名前空間の用語は数えない() -> None:
    body = f"@prefix e: <{_BASE}> .\ne:S0 e:p <https://other.example/#X> .\n"
    assert "https://other.example/#X" not in terms_in_graph(body, base_iri=_BASE)


def test_base_iri_が空なら何も数えない() -> None:
    """**空の接頭辞で全 IRI を数えてしまわないようにする。**"""
    assert terms_in_graph(_turtle(3), base_iri="") == ()


def test_解析できない本文では用語を数えない() -> None:
    """**記録のために本来の応答を壊さない。** 例外にせず空を返す。"""
    assert terms_in_graph("壊れた <<<", base_iri=_BASE) == ()


# ------------------------------------------------------ 数えられない場合


def test_count_triples_は解析できなければ_None() -> None:
    """**`0` を返さない**(ADR-0034 決定8)。

    「トリプルが 0 件」と「数えられなかった」は違う。
    """
    assert count_triples("壊れた <<<") is None
    assert count_triples("") == 0
    assert count_triples(_turtle(3)) == 3
