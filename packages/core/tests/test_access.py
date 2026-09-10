"""アクセスログの材料を作る部分のテスト(P2B-05、ADR-0018)。

**要点は 2 つ。**

1. **「返した用語」はその名前空間が発行した IRI に限る**(決定6)。
   `rdf:type` の参照回数は指標にならないし、外部語彙の IRI は自分が廃止を
   判断できる対象ではない
2. **`GRAPH` 句の有無を記録する**(決定7)。明示したクエリは他の版を読みうる
   ので、版の記録が不完全であることを読み手に伝えなければならない
"""

from __future__ import annotations

from ontology_core.access import (
    MAX_QUERY_TEXT,
    AccessRecord,
    build_access_record,
    query_fingerprint,
    returned_terms,
    uses_graph_clause,
)

_BASE = "https://e.example/#"


def _results(*iris: str) -> dict[str, object]:
    return {
        "head": {"vars": ["s"]},
        "results": {"bindings": [{"s": {"type": "uri", "value": iri}} for iri in iris]},
    }


# ---------------------------------------------------------------- 返した用語


def test_名前空間の用語だけを数える() -> None:
    """**決定6 の本題。** 自分のオントロジーの用語が使われているかを測る。"""
    results = _results(
        _BASE + "Product",
        "http://www.w3.org/2002/07/owl#Class",
        "http://www.w3.org/2004/02/skos/core#Concept",
    )
    assert returned_terms(results, base_iri=_BASE) == (_BASE + "Product",)


def test_外部語彙の_IRI_は数えない() -> None:
    """**使われていない外部 IRI を「縮める」ことはできない**(決定6)。

    責任者(ADR-0015 決定3)は外部 IRI にも置けるようにしたのと**逆の判断**で、
    理由も逆である。
    """
    results = _results("https://schema.org/Product")
    assert returned_terms(results, base_iri=_BASE) == ()


def test_リテラルは_IRI_として扱わない() -> None:
    results = {
        "head": {"vars": ["s"]},
        "results": {"bindings": [{"s": {"type": "literal", "value": _BASE + "Product"}}]},
    }
    assert returned_terms(results, base_iri=_BASE) == ()


def test_重複は_1_件にまとめる() -> None:
    """参照回数は**クエリ 1 回で 1 回**である。同じ用語が結果に何行現れても
    「1 回参照された」とする。行数を数えると、`LIMIT` の違いで指標が動く。
    """
    results = _results(_BASE + "A", _BASE + "A", _BASE + "A")
    assert returned_terms(results, base_iri=_BASE) == (_BASE + "A",)


def test_列挙は安定して並ぶ() -> None:
    results = _results(_BASE + "Z", _BASE + "A", _BASE + "M")
    found = returned_terms(results, base_iri=_BASE)
    assert list(found) == sorted(found)


def test_複数の変数を横断して集める() -> None:
    results = {
        "head": {"vars": ["s", "o"]},
        "results": {
            "bindings": [
                {
                    "s": {"type": "uri", "value": _BASE + "A"},
                    "o": {"type": "uri", "value": _BASE + "B"},
                }
            ]
        },
    }
    assert returned_terms(results, base_iri=_BASE) == (_BASE + "A", _BASE + "B")


def test_ASK_の結果でも落ちない() -> None:
    assert returned_terms({"head": {}, "boolean": True}, base_iri=_BASE) == ()


def test_想定外の形でも落ちない() -> None:
    """ストアを差し替えられる設計なので(ADR-0001)、結果の形に前提を置かない。
    **記録のために本来の応答を壊してはいけない。**"""
    for bad in (
        {},
        {"results": None},
        {"results": {"bindings": 5}},
        {"results": {"bindings": [None]}},
    ):
        assert returned_terms(bad, base_iri=_BASE) == ()


def test_base_iri_が空なら何も数えない() -> None:
    """`base_iri` が設定されていない名前空間で、全 IRI を数えてしまわないこと。"""
    assert returned_terms(_results(_BASE + "A"), base_iri="") == ()


# ---------------------------------------------------------------- GRAPH 句


def test_GRAPH_句を検出する() -> None:
    assert uses_graph_clause("SELECT ?s WHERE { GRAPH <urn:g> { ?s ?p ?o } }") is True


def test_GRAPH_句が無ければ_False() -> None:
    assert uses_graph_clause("SELECT ?s WHERE { ?s ?p ?o }") is False


def test_大文字小文字を区別しない() -> None:
    """SPARQL のキーワードは大文字小文字を区別しない。"""
    for text in ("graph <urn:g>", "Graph <urn:g>", "GRAPH <urn:g>"):
        assert uses_graph_clause(f"SELECT ?s WHERE {{ {text} {{ ?s ?p ?o }} }}") is True


def test_単語の一部は誤検出しない() -> None:
    """`?graphName` や `ex:graphOf` を `GRAPH` 句と誤認しないこと。

    誤検出すると、**`GRAPH` 句なしのクエリの版の記録まで「不完全」と
    報告されてしまう**(正しい記録が信用できなくなる)。
    """
    assert uses_graph_clause("SELECT ?graphName WHERE { ?s ex:graphOf ?graphName }") is False
    assert uses_graph_clause("SELECT ?s WHERE { ?s <urn:paragraph> ?o }") is False


# ---------------------------------------------------------------- クエリの指紋


def test_ハッシュは全文から作る() -> None:
    """切り詰めた文字列が一致しても元のクエリが同じとは限らない。
    **同じクエリをまとめるためにハッシュが要る。**"""
    long_a = "SELECT ?s WHERE { ?s ?p ?o } # " + "a" * MAX_QUERY_TEXT
    long_b = "SELECT ?s WHERE { ?s ?p ?o } # " + "a" * (MAX_QUERY_TEXT - 1) + "b"
    fa, fb = query_fingerprint(long_a), query_fingerprint(long_b)
    assert fa.text == fb.text, "切り詰めた結果は同じになる(前提の確認)"
    assert fa.hash != fb.hash, "ハッシュは全文から作るので違う値になる"


def test_短いクエリは切り詰めない() -> None:
    f = query_fingerprint("SELECT ?s WHERE { ?s ?p ?o }")
    assert f.truncated is False
    assert f.text == "SELECT ?s WHERE { ?s ?p ?o }"


def test_長いクエリは切り詰めて印を付ける() -> None:
    """**黙って切らない。** 記録を読む人が「これが全文だ」と誤解しないため。"""
    f = query_fingerprint("x" * (MAX_QUERY_TEXT + 100))
    assert f.truncated is True
    assert len(f.text) == MAX_QUERY_TEXT


def test_ハッシュは_64_文字の_16_進数() -> None:
    """列長 64(SHA-256 の hexdigest)に収まること。"""
    f = query_fingerprint("SELECT ?s WHERE { ?s ?p ?o }")
    assert len(f.hash) == 64
    assert all(c in "0123456789abcdef" for c in f.hash)


# ---------------------------------------------------------------- 記録の組み立て


def test_記録を組み立てる() -> None:
    record = build_access_record(
        namespace="retail",
        actor="agent-oid",
        query="SELECT ?s WHERE { ?s ?p ?o }",
        results=_results(_BASE + "A", "https://schema.org/X"),
        base_iri=_BASE,
        default_graph_version="2.0.0",
    )
    assert isinstance(record, AccessRecord)
    assert record.namespace == "retail"
    assert record.actor == "agent-oid"
    assert record.returned_row_count == 2, "行数は結果の行数(用語の数ではない)"
    assert record.terms == (_BASE + "A",)
    assert record.returned_term_count == 1
    assert record.used_graph_clause is False
    assert record.default_graph_version == "2.0.0"


def test_GRAPH_句を使ったクエリでは版の記録が不完全である() -> None:
    """**`used_graph_clause` が真なら、`default_graph_version` は
    「既定グラフの版」でしかない**(決定7)。読み手にそれを伝えるための欄である。
    """
    record = build_access_record(
        namespace="retail",
        actor="agent-oid",
        query="SELECT ?s WHERE { GRAPH <urn:ontology:graph/retail/1.0.0> { ?s ?p ?o } }",
        results=_results(_BASE + "A"),
        base_iri=_BASE,
        default_graph_version="2.0.0",
    )
    assert record.used_graph_clause is True
    assert record.default_graph_version == "2.0.0"


def test_承認済みの版が無ければ版は_None() -> None:
    record = build_access_record(
        namespace="retail",
        actor="agent-oid",
        query="SELECT ?s WHERE { ?s ?p ?o }",
        results=_results(),
        base_iri=_BASE,
        default_graph_version=None,
    )
    assert record.default_graph_version is None
    assert record.returned_row_count == 0
    assert record.terms == ()
