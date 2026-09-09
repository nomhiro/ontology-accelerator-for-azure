"""意味的差分のテスト(P2B-09、ADR-0016)。

**「意味的」の意味をここで固定する。** 接頭辞・トリプルの順序・空白ノードの
ラベルの違いを差分として報告しないこと。テキスト差分ではこれが守れない。

もう 1 つの要点は**廃止と削除の区別**である(ADR-0009 決定3)。廃止は正しい
縮め方、削除は規律違反であり、同じ「減った」に混ぜてはいけない。
"""

from __future__ import annotations

import pytest

from ontology_core.diff import (
    MAX_BLANK_NODES,
    TripleDiffStatus,
    diff_ontologies,
)

_HEAD = """
@prefix ex: <https://e.example/#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""

_PRODUCT = "https://e.example/#Product"
_CUSTOMER = "https://e.example/#Customer"
_ORDER = "https://e.example/#Order"


def _ttl(body: str) -> str:
    return _HEAD + body


# ---------------------------------------------------------------- 「意味的」の定義


def test_接頭辞の違いは差分にならない() -> None:
    base = _ttl('ex:Product a owl:Class ; rdfs:label "商品" .')
    new = """
@prefix other: <https://e.example/#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
other:Product a owl:Class ; rdfs:label "商品" .
"""
    result = diff_ontologies(base, new)
    assert result.is_empty, f"差分が出てはいけない: {result}"


def test_トリプルの順序の違いは差分にならない() -> None:
    base = _ttl('ex:Product a owl:Class ; rdfs:label "商品" .\nex:Customer a owl:Class .')
    new = _ttl('ex:Customer a owl:Class .\nex:Product rdfs:label "商品" ; a owl:Class .')
    assert diff_ontologies(base, new).is_empty


def test_空白ノードのラベルの違いは差分にならない() -> None:
    """**ここがテキスト差分では守れない核心。**

    SHACL の property shape は空白ノードになる。記述の順序を入れ替えただけで
    差分が出るなら、レビューの役に立たない。
    """
    base = _ttl("ex:S a sh:NodeShape ; sh:property [ sh:path ex:name ; sh:minCount 1 ] .")
    new = _ttl("ex:S a sh:NodeShape ; sh:property [ sh:minCount 1 ; sh:path ex:name ] .")
    result = diff_ontologies(base, new)
    assert result.is_empty, f"空白ノードのラベル違いで差分が出た: {result}"
    assert result.triple_status is TripleDiffStatus.EXACT


def test_空白ノードの中身が変わったら差分になる() -> None:
    base = _ttl("ex:S a sh:NodeShape ; sh:property [ sh:path ex:name ; sh:minCount 1 ] .")
    new = _ttl("ex:S a sh:NodeShape ; sh:property [ sh:path ex:name ; sh:minCount 2 ] .")
    result = diff_ontologies(base, new)
    assert not result.is_empty
    assert result.modified_terms == ("https://e.example/#S",)


# ---------------------------------------------------------------- 用語単位


def test_追加された用語を報告する() -> None:
    base = _ttl("ex:Product a owl:Class .")
    new = _ttl("ex:Product a owl:Class .\nex:Order a owl:Class .")
    result = diff_ontologies(base, new)
    assert result.added_terms == (_ORDER,)
    assert result.removed_terms == ()


def test_削除された用語を報告する() -> None:
    """**ADR-0009 決定3 の規律違反はこれで見える。** IRI を削除してはいけない。"""
    base = _ttl("ex:Product a owl:Class .\nex:Customer a owl:Class .")
    new = _ttl("ex:Product a owl:Class .")
    result = diff_ontologies(base, new)
    assert result.removed_terms == (_CUSTOMER,)
    assert result.has_removed_terms is True


def test_廃止は削除と分けて報告する() -> None:
    """**これがこの分類の要点。** 廃止は正しい縮め方、削除は規律違反である
    (ADR-0009 決定3)。同じ「減った」に混ぜてはいけない。
    """
    base = _ttl("ex:Product a owl:Class .\nex:Customer a owl:Class .")
    new = _ttl(
        "ex:Product a owl:Class .\n"
        "ex:Customer a owl:Class ; owl:deprecated true ; rdfs:seeAlso ex:Product ."
    )
    result = diff_ontologies(base, new)
    assert result.deprecated_terms == (_CUSTOMER,)
    assert result.removed_terms == (), "廃止した用語を削除として報告してはいけない"
    assert result.has_removed_terms is False
    # 記述が変わっているので modified でもある。
    assert result.modified_terms is not None
    assert _CUSTOMER in result.modified_terms


def test_廃止を取り消したら_deprecated_に入らない() -> None:
    """`owl:deprecated` が外れた場合。**廃止の取り消しは廃止ではない。**"""
    base = _ttl("ex:Customer a owl:Class ; owl:deprecated true .")
    new = _ttl("ex:Customer a owl:Class .")
    result = diff_ontologies(base, new)
    assert result.deprecated_terms == ()
    assert result.modified_terms == (_CUSTOMER,)


def test_deprecated_false_は廃止として数えない() -> None:
    base = _ttl("ex:Customer a owl:Class .")
    new = _ttl("ex:Customer a owl:Class ; owl:deprecated false .")
    result = diff_ontologies(base, new)
    assert result.deprecated_terms == ()


def test_変更された用語を報告する() -> None:
    base = _ttl('ex:Product a owl:Class ; rdfs:label "商品" .')
    new = _ttl('ex:Product a owl:Class ; rdfs:label "製品" .')
    result = diff_ontologies(base, new)
    assert result.modified_terms == (_PRODUCT,)
    assert result.added_terms == ()
    assert result.removed_terms == ()


def test_追加された用語は_modified_に入らない() -> None:
    """追加と変更を重複して数えない。「増えた」と「変わった」は別の事実である。"""
    base = _ttl("ex:Product a owl:Class .")
    new = _ttl("ex:Product a owl:Class .\nex:Order a owl:Class .")
    result = diff_ontologies(base, new)
    assert result.added_terms == (_ORDER,)
    assert result.modified_terms is not None
    assert _ORDER not in result.modified_terms


def test_削除された用語は_modified_に入らない() -> None:
    base = _ttl("ex:Product a owl:Class .\nex:Customer a owl:Class .")
    new = _ttl("ex:Product a owl:Class .")
    result = diff_ontologies(base, new)
    assert result.modified_terms is not None
    assert _CUSTOMER not in result.modified_terms


def test_入れ子の空白ノードの変更も用語に帰属する() -> None:
    """空白ノードが空白ノードを参照する場合(SHACL の `sh:or` など)。

    **1 段だけ遡る実装では落ちる。** 推移的に遡らなければ、深い構造の変更が
    どの用語のものか分からなくなる。
    """
    base = _ttl(
        "ex:S a sh:NodeShape ; sh:property "
        "[ sh:path ex:name ; sh:or ( [ sh:datatype xsd:string ] [ sh:minCount 1 ] ) ] ."
    )
    new = _ttl(
        "ex:S a sh:NodeShape ; sh:property "
        "[ sh:path ex:name ; sh:or ( [ sh:datatype xsd:integer ] [ sh:minCount 1 ] ) ] ."
    )
    result = diff_ontologies(base, new)
    assert not result.is_empty
    assert result.modified_terms == ("https://e.example/#S",), (
        "入れ子の奥の変更も ex:S の変更として帰属しなければならない"
    )


def test_空白ノードが循環していても止まる() -> None:
    """自己参照する空白ノードで無限再帰しないこと。

    RDF としては合法な構造であり、外部から取り込んだデータに現れうる。
    """
    base = _ttl("ex:S ex:link [ ex:self [ ex:back ex:S ] ] .")
    new = _ttl("ex:S ex:link [ ex:self [ ex:back ex:S ] ] ; ex:extra 1 .")
    result = diff_ontologies(base, new)
    assert result.modified_terms == ("https://e.example/#S",)


def test_空白ノードは用語として数えない() -> None:
    """空白ノードは構造であって用語ではない。IRI を持たないので参照もできない。"""
    base = _ttl("ex:S a sh:NodeShape .")
    new = _ttl("ex:S a sh:NodeShape ; sh:property [ sh:path ex:name ] .")
    result = diff_ontologies(base, new)
    assert result.added_terms == (), "空白ノードを追加された用語として報告してはいけない"
    assert result.modified_terms == ("https://e.example/#S",)


def test_一覧は_IRI_で安定して並ぶ() -> None:
    base = _ttl("ex:A a owl:Class .")
    new = _ttl("ex:A a owl:Class .\nex:Z a owl:Class .\nex:B a owl:Class .\nex:M a owl:Class .")
    result = diff_ontologies(base, new)
    assert list(result.added_terms) == sorted(result.added_terms)


# ---------------------------------------------------------------- トリプル単位


def test_トリプル単位の差分も返す() -> None:
    base = _ttl('ex:Product a owl:Class ; rdfs:label "商品" .')
    new = _ttl('ex:Product a owl:Class ; rdfs:label "製品" .')
    result = diff_ontologies(base, new)
    assert result.removed_triple_count == 1
    assert result.added_triple_count == 1
    assert result.triple_status is TripleDiffStatus.EXACT


def test_空白ノードが上限を超えたらトリプル単位を計算しない() -> None:
    """**「差分が無い」と「計算できなかった」を混同させない**(ADR-0016 決定5)。

    混同すると、IRI の削除という規律違反を見落とす。
    """
    shapes = "\n".join(
        f"ex:S{i} a sh:NodeShape ; sh:property [ sh:path ex:p{i} ; sh:minCount 1 ] ."
        for i in range(MAX_BLANK_NODES + 1)
    )
    base = _ttl(shapes)
    new = _ttl(shapes + "\nex:Extra a owl:Class .")
    result = diff_ontologies(base, new)

    assert result.triple_status is TripleDiffStatus.SKIPPED_TOO_MANY_BLANK_NODES
    assert result.modified_terms is None, "計算できていないのに空の一覧を返してはいけない"
    assert result.added_triple_count is None
    assert result.removed_triple_count is None
    # **上限を超えても added / removed は厳密に返る**(ADR-0016 決定4)。
    assert result.added_terms == ("https://e.example/#Extra",)
    assert result.removed_terms == ()
    assert result.blank_node_count > MAX_BLANK_NODES


def test_上限を超えても削除された用語は検出する() -> None:
    """**差分の最も重要な部分が最も安い**(ADR-0016 決定4)。

    `removed` は「IRI が主語として現れるか」だけで決まり、空白ノードの
    同一性に依存しない。
    """
    shapes = "\n".join(
        f"ex:S{i} a sh:NodeShape ; sh:property [ sh:path ex:p{i} ; sh:minCount 1 ] ."
        for i in range(MAX_BLANK_NODES + 1)
    )
    base = _ttl(shapes + "\nex:Doomed a owl:Class .")
    new = _ttl(shapes)
    result = diff_ontologies(base, new)
    assert result.triple_status is TripleDiffStatus.SKIPPED_TOO_MANY_BLANK_NODES
    assert result.removed_terms == ("https://e.example/#Doomed",)
    assert result.has_removed_terms is True


def test_上限ちょうどなら計算する() -> None:
    """境界を off-by-one で外さない。"""
    shapes = "\n".join(
        f"ex:S{i} a sh:NodeShape ; sh:property [ sh:path ex:p{i} ] ."
        for i in range(MAX_BLANK_NODES)
    )
    result = diff_ontologies(_ttl(shapes), _ttl(shapes))
    assert result.triple_status is TripleDiffStatus.EXACT
    assert result.blank_node_count == MAX_BLANK_NODES


# ---------------------------------------------------------------- 要約


def test_要約は用語の一覧を上限つきで持つ() -> None:
    """**監査行を非有界に育てない**(ADR-0016 決定7)。厳密な差分は不変リビジョン
    から再計算できる(不変条件7)。"""
    base = _ttl("ex:Keep a owl:Class .")
    new = _ttl(
        "ex:Keep a owl:Class .\n" + "\n".join(f"ex:New{i:03d} a owl:Class ." for i in range(80))
    )
    result = diff_ontologies(base, new)
    summary = result.summary(max_terms=50)
    assert len(summary["added_terms"]) == 50
    assert summary["added_term_count"] == 80
    assert summary["truncated"] is True, "黙って切ってはいけない"


def test_切り捨てが無ければ_truncated_は_False() -> None:
    base = _ttl("ex:Keep a owl:Class .")
    new = _ttl("ex:Keep a owl:Class .\nex:One a owl:Class .")
    summary = diff_ontologies(base, new).summary(max_terms=50)
    assert summary["truncated"] is False
    assert summary["added_terms"] == ["https://e.example/#One"]


def test_要約は_JSON_にできる() -> None:
    import json

    base = _ttl("ex:A a owl:Class .")
    new = _ttl("ex:B a owl:Class .")
    text = json.dumps(diff_ontologies(base, new).summary(), ensure_ascii=False)
    restored = json.loads(text)
    assert restored["removed_terms"] == ["https://e.example/#A"]
    assert restored["triple_status"] == TripleDiffStatus.EXACT.value


def test_計算できなかったことが要約に残る() -> None:
    """要約だけを見る呼び出し元(監査の読み手)にも、計算できなかったことが
    伝わらなければならない。"""
    shapes = "\n".join(
        f"ex:S{i} a sh:NodeShape ; sh:property [ sh:path ex:p{i} ] ."
        for i in range(MAX_BLANK_NODES + 1)
    )
    summary = diff_ontologies(_ttl(shapes), _ttl(shapes)).summary()
    assert summary["triple_status"] == TripleDiffStatus.SKIPPED_TOO_MANY_BLANK_NODES.value
    assert summary["modified_terms"] is None
    assert summary["blank_node_count"] > MAX_BLANK_NODES


# ---------------------------------------------------------------- 異常系


def test_同じ内容なら空の差分になる() -> None:
    ttl = _ttl('ex:Product a owl:Class ; rdfs:label "商品" .')
    result = diff_ontologies(ttl, ttl)
    assert result.is_empty
    assert result.summary()["empty"] is True


def test_構文が壊れていれば理由付きで失敗する() -> None:
    """**空の差分を返さない。** 「変更なし」と誤解させる。"""
    from ontology_core.diff import DiffError

    with pytest.raises(DiffError, match="解析"):
        diff_ontologies(_ttl("ex:A a owl:Class ."), "これは Turtle ではない {{{")


def test_空の_TTL_も比較できる() -> None:
    """空のオントロジーは構文としては正当である。"""
    result = diff_ontologies("", _ttl("ex:A a owl:Class ."))
    assert result.added_terms == ("https://e.example/#A",)
