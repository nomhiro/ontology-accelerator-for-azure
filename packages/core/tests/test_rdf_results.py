"""`CONSTRUCT` / `DESCRIBE` の結果の検査と正規化(ADR-0034、`P2A-14`)。

ここで固定するのは 3 つである。

1. **上限を超えたら切り詰めずに例外**(決定4)。**行数(ADR-0025 決定5)とは
   意図的に違う判断である** — RDF には「切り詰めた」と書く封筒が無く、
   ヘッダに書いてもエージェントは見ない(ADR-0017 決定3)
2. **解析できなかったことを「該当なし」に丸めない**。空のグラフを返さない
3. **`0` と `None` を区別する**(`count_triples`)
4. **廃止済み用語を IRI の集合から拾う**
   ([ADR-0038](../../../docs/adr/0038-rdf-deprecation-warning.md)、`P2B-23`)。
   **述語も見るので `SELECT` より網羅的である**

**検査はパースし直したグラフに対して行う**(`test_prov.py` と同じ理由)。

**解析は 1 回だけ行う**(ADR-0038 決定3)。`RdfResult.iris` が集合を持ち、
用語の絞り込みと廃止の検査はどちらもその集合に対する純粋な操作である。
"""

from __future__ import annotations

import pytest
from rdflib import Graph, URIRef

from ontology_core.sparql.rdf_results import (
    RdfParseError,
    TripleLimitExceededError,
    count_triples,
    deprecated_iris_in_graph,
    load_construct_result,
    terms_with_prefix,
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
    found = terms_with_prefix(load_construct_result(_turtle(2), limit=10).iris, base_iri=_BASE)
    assert found == (
        _BASE + "O0",
        _BASE + "O1",
        _BASE + "S0",
        _BASE + "S1",
        _BASE + "p",
    )


def test_他の名前空間の用語は数えない() -> None:
    body = f"@prefix e: <{_BASE}> .\ne:S0 e:p <https://other.example/#X> .\n"
    iris = load_construct_result(body, limit=10).iris
    assert "https://other.example/#X" in iris, "IRI の集合には入る(絞り込みは別の関心)"
    assert "https://other.example/#X" not in terms_with_prefix(iris, base_iri=_BASE)


def test_base_iri_が空なら何も数えない() -> None:
    """**空の接頭辞で全 IRI を数えてしまわないようにする。**

    呼び出し側で `startswith(base_iri)` と書くと、空文字のときに全件一致する。
    **この規則が `terms_with_prefix` の存在理由である**(ADR-0038 決定3)。
    """
    assert terms_with_prefix(load_construct_result(_turtle(3), limit=10).iris, base_iri="") == ()


def test_解析できない本文では用語を数えない() -> None:
    """**記録のために本来の応答を壊さない。**

    解析できない本文は `load_construct_result` が `RdfParseError` で断るので
    (ADR-0034 決定4)、`terms_with_prefix` に壊れた本文は届かない。
    **そのため「解析できないときの分岐」を持たない** — 到達しない分岐を
    作らないため(ADR-0038 決定3)。
    """
    with pytest.raises(RdfParseError):
        load_construct_result("壊れた <<<", limit=10)


def test_リテラルは_IRI_の集合に入らない() -> None:
    """**文字列が偶然一致しても警告しない**(ADR-0038 決定5)。"""
    body = f'@prefix e: <{_BASE}> .\ne:S0 e:label "{_BASE}Fake" .\n'
    iris = load_construct_result(body, limit=10).iris
    assert _BASE + "Fake" not in iris
    assert _BASE + "S0" in iris


def test_空白ノードは_IRI_の集合に入らない() -> None:
    body = f"@prefix e: <{_BASE}> .\ne:S0 e:p [ e:q e:O0 ] .\n"
    iris = load_construct_result(body, limit=10).iris
    assert iris == frozenset({_BASE + "S0", _BASE + "p", _BASE + "q", _BASE + "O0"})


# ------------------------------- 廃止済み用語(ADR-0038、`P2B-23`)


def test_廃止済み用語をグラフから拾う() -> None:
    """**`SELECT` では述語が拾えない**(ADR-0038 決定4)。

    `SELECT ?s ?o` の結果に述語は現れないので、廃止された property を使った
    クエリは `SELECT` では警告が出ない。グラフからなら出る。
    """
    iris = load_construct_result(_turtle(2), limit=10).iris
    found = deprecated_iris_in_graph(iris, frozenset({_BASE + "p", _BASE + "S1"}))
    assert found == (_BASE + "S1", _BASE + "p")


def test_廃止が無ければ空を返す() -> None:
    iris = load_construct_result(_turtle(2), limit=10).iris
    assert deprecated_iris_in_graph(iris, frozenset({_BASE + "NotHere"})) == ()
    assert deprecated_iris_in_graph(iris, frozenset()) == ()


def test_他の名前空間の廃止も警告する() -> None:
    """**`base_iri` で絞らない**(ADR-0038 決定6)。

    取り込んだ外部語彙の廃止は利用者にとって同じだけ重要で、かつ
    **新しい情報の漏れは無い**(呼び出し元は同じデータセットに `SELECT` を
    投げれば読める)。
    """
    other = "https://other.example/#X"
    body = f"@prefix e: <{_BASE}> .\ne:S0 e:p <{other}> .\n"
    iris = load_construct_result(body, limit=10).iris
    assert deprecated_iris_in_graph(iris, frozenset({other})) == (other,)


# ------------------------------------------------------ 数えられない場合


def test_count_triples_は解析できなければ_None() -> None:
    """**`0` を返さない**(ADR-0034 決定8)。

    「トリプルが 0 件」と「数えられなかった」は違う。
    """
    assert count_triples("壊れた <<<") is None
    assert count_triples("") == 0
    assert count_triples(_turtle(3)) == 3
