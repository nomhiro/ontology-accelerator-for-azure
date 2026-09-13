"""R2RML マッピングの検証(ADR-0046、`P3-01`)。

ここで固定するのは 4 つである。

1. **`rr:sqlQuery` を受け付けない**(決定4)。任意の SQL を安全と判定する
   手段が無いので、**境界があるように見えて無い**状態を作らない
2. **主語の IRI の接頭辞が取れる**(決定5)。名前空間の外に出る主語を
   サービス層が弾けるのは、ここで接頭辞が取れるからである
3. **述語をデータから作らせない**(決定6)。列の値が述語になると、
   どの述語が現れるかが実データ次第になり、語彙を検査できない
4. **受け付けない構成は「読めなかった」ではなく例外になる。** 黙って
   空のタプルを返すと、サービス層が「検査した」と誤認する
"""

from __future__ import annotations

import pytest

from ontology_core.r2rml import (
    MAX_MAPPING_LENGTH,
    R2rmlError,
    data_iri_prefix,
    parse_mapping,
)

_PREFIXES = """
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix ex: <https://e.example/retail#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
"""

_GOOD = (
    _PREFIXES
    + """
<#CustomerMap> a rr:TriplesMap ;
  rr:logicalTable [ rr:tableName "\\"sales\\".\\"customer\\"" ] ;
  rr:subjectMap [
    rr:template "https://e.example/retail/data/customer/{id}" ;
    rr:class ex:Customer
  ] ;
  rr:predicateObjectMap [
    rr:predicate ex:fullName ;
    rr:objectMap [ rr:column "full_name" ]
  ] ;
  rr:predicateObjectMap [
    rr:predicate rdfs:label ;
    rr:objectMap [ rr:column "full_name" ]
  ] .

<#OrderMap> a rr:TriplesMap ;
  rr:logicalTable [ rr:tableName "\\"sales\\".\\"order\\"" ] ;
  rr:subjectMap [
    rr:template "https://e.example/retail/data/order/{id}" ;
    rr:class ex:Order
  ] ;
  rr:predicateObjectMap [
    rr:predicate ex:placedBy ;
    rr:objectMap [ rr:template "https://e.example/retail/data/customer/{customer_id}" ]
  ] .
"""
)


def _one(body: str) -> str:
    """1 つの TriplesMap を持つマッピングを組み立てる。"""
    return _PREFIXES + "\n<#M> a rr:TriplesMap ;\n" + body + " .\n"


# ------------------------------------- データ IRI の接頭辞(決定5)


def test_フラグメント区切りの_base_iri_からデータ接頭辞を導く() -> None:
    assert data_iri_prefix("https://e.example/retail#") == "https://e.example/retail/data/"


def test_スラッシュ区切りの_base_iri_でも同じ形になる() -> None:
    """**用語とデータの空間が重なりうることは許容する**(決定5)。

    重なっても境界は破れない — どちらも同じ名前空間の下である。
    """
    assert data_iri_prefix("https://e.example/retail/") == "https://e.example/retail/data/"


def test_区切りが無い_base_iri_でも末尾に_data_が付く() -> None:
    assert data_iri_prefix("https://e.example/retail") == "https://e.example/retail/data/"


# ------------------------------------- 解析できるもの


def test_2_つの_TriplesMap_を数える() -> None:
    mapping = parse_mapping(_GOOD)
    assert mapping.triples_maps == 2


def test_引用付きの表名を_schema_table_に正規化する() -> None:
    """**正規化しないとスキャンの観測と突き合わせられない。**"""
    assert parse_mapping(_GOOD).tables == ("sales.customer", "sales.order")


def test_引用の無い表名もそのまま取れる() -> None:
    mapping = parse_mapping(
        _one(
            'rr:logicalTable [ rr:tableName "sales.customer" ] ;\n'
            '  rr:subjectMap [ rr:template "https://e.example/retail/data/c/{id}" ]'
        )
    )
    assert mapping.tables == ("sales.customer",)


def test_大文字小文字を畳み込まない() -> None:
    """PostgreSQL では引用付きの識別子が大文字小文字を保持する。

    畳み込むと**別の表を同じ表と見なす**。
    """
    mapping = parse_mapping(
        _one(
            'rr:logicalTable [ rr:tableName "\\"Sales\\".\\"Customer\\"" ] ;\n'
            '  rr:subjectMap [ rr:template "https://e.example/retail/data/c/{id}" ]'
        )
    )
    assert mapping.tables == ("Sales.Customer",)


def test_主語のテンプレートから接頭辞を取る() -> None:
    """**`{...}` より前だけを取る。** サービス層がこれで封じ込めを判定する。"""
    assert parse_mapping(_GOOD).subject_prefixes == (
        "https://e.example/retail/data/customer/",
        "https://e.example/retail/data/order/",
    )


def test_プレースホルダが無いテンプレートは全体が接頭辞になる() -> None:
    mapping = parse_mapping(
        _one(
            'rr:logicalTable [ rr:tableName "t" ] ;\n'
            '  rr:subjectMap [ rr:template "https://e.example/retail/data/only" ]'
        )
    )
    assert mapping.subject_prefixes == ("https://e.example/retail/data/only",)


def test_rr_constant_の主語も接頭辞として取れる() -> None:
    mapping = parse_mapping(
        _one(
            'rr:logicalTable [ rr:tableName "t" ] ;\n'
            "  rr:subjectMap [ rr:constant <https://e.example/retail/data/fixed> ]"
        )
    )
    assert mapping.subject_prefixes == ("https://e.example/retail/data/fixed",)


def test_短縮形の_rr_subject_も取れる() -> None:
    """R2RML は `rr:subjectMap [ rr:constant X ]` を `rr:subject X` と書ける。

    **短縮形を読み落とすと、封じ込めの検査をすり抜ける。**
    """
    mapping = parse_mapping(
        _one(
            'rr:logicalTable [ rr:tableName "t" ] ;\n  rr:subject <https://elsewhere.example/thing>'
        )
    )
    assert mapping.subject_prefixes == ("https://elsewhere.example/thing",)


def test_クラスと述語を集める() -> None:
    mapping = parse_mapping(_GOOD)
    assert mapping.classes == (
        "https://e.example/retail#Customer",
        "https://e.example/retail#Order",
    )
    assert mapping.predicates == (
        "http://www.w3.org/2000/01/rdf-schema#label",
        "https://e.example/retail#fullName",
        "https://e.example/retail#placedBy",
    )


def test_asserted_iris_に主語の接頭辞は入らない() -> None:
    """**主語とクラス・述語では守る規則が違う**(決定5・6)。

    主語はデータ接頭辞の下に閉じ込め、クラス・述語は外部語彙を許す。
    混ぜると `rdfs:label` を使えなくなる。
    """
    mapping = parse_mapping(_GOOD)
    assert set(mapping.asserted_iris) == set(mapping.classes) | set(mapping.predicates)
    for prefix in mapping.subject_prefixes:
        assert prefix not in mapping.asserted_iris


def test_rr_predicateMap_の_rr_constant_も述語として取れる() -> None:
    mapping = parse_mapping(
        _one(
            'rr:logicalTable [ rr:tableName "t" ] ;\n'
            '  rr:subjectMap [ rr:template "https://e.example/retail/data/c/{id}" ] ;\n'
            "  rr:predicateObjectMap [\n"
            "    rr:predicateMap [ rr:constant ex:code ] ;\n"
            '    rr:objectMap [ rr:column "code" ]\n'
            "  ]"
        )
    )
    assert mapping.predicates == ("https://e.example/retail#code",)


def test_型宣言が無くても_logicalTable_を持つ主語は_TriplesMap_として数える() -> None:
    """R2RML は `a rr:TriplesMap` を省略できる。

    **省略された TriplesMap を見落とすと、検査していない入口が通る。**
    """
    mapping = parse_mapping(
        _PREFIXES
        + """
<#M> rr:logicalTable [ rr:tableName "t" ] ;
  rr:subjectMap [ rr:template "https://e.example/retail/data/c/{id}" ] .
"""
    )
    assert mapping.triples_maps == 1
    assert mapping.tables == ("t",)


# ------------------------------------- 受け付けないもの


def test_空のマッピングを拒否する() -> None:
    with pytest.raises(R2rmlError, match="空です"):
        parse_mapping("   \n  ")


def test_大きすぎるマッピングを拒否する() -> None:
    with pytest.raises(R2rmlError, match="大きすぎます"):
        parse_mapping("#" + "x" * MAX_MAPPING_LENGTH)


def test_Turtle_として壊れているものを拒否する() -> None:
    with pytest.raises(R2rmlError, match="解析できません"):
        parse_mapping("@prefix rr: <http://www.w3.org/ns/r2rml#> . <#M> rr:logicalTable")


def test_TriplesMap_が_1_つも無いものを拒否する() -> None:
    with pytest.raises(R2rmlError, match="1 つもありません"):
        parse_mapping(_PREFIXES + "\nex:Customer a <http://www.w3.org/2002/07/owl#Class> .\n")


def test_rr_sqlQuery_を拒否する() -> None:
    """**これがこのモジュールのいちばん重要な判断である**(決定4)。

    任意の SQL を安全と判定する手段が無い。正規表現で判定すると
    **境界があるように見えて無い**状態を作る。
    """
    with pytest.raises(R2rmlError, match="rr:sqlQuery は受け付けません"):
        parse_mapping(
            _one(
                'rr:logicalTable [ rr:sqlQuery "SELECT * FROM sales.customer" ] ;\n'
                '  rr:subjectMap [ rr:template "https://e.example/retail/data/c/{id}" ]'
            )
        )


def test_tableName_も_sqlQuery_も無い_logicalTable_を拒否する() -> None:
    with pytest.raises(R2rmlError, match="rr:tableName がありません"):
        parse_mapping(
            _one(
                'rr:logicalTable [ rr:column "x" ] ;\n'
                '  rr:subjectMap [ rr:template "https://e.example/retail/data/c/{id}" ]'
            )
        )


def test_空の_tableName_を拒否する() -> None:
    with pytest.raises(R2rmlError, match="rr:tableName が空です"):
        parse_mapping(
            _one(
                'rr:logicalTable [ rr:tableName "   " ] ;\n'
                '  rr:subjectMap [ rr:template "https://e.example/retail/data/c/{id}" ]'
            )
        )


def test_logicalTable_が無い_TriplesMap_を拒否する() -> None:
    with pytest.raises(R2rmlError, match="rr:logicalTable が無い"):
        parse_mapping(_one('rr:subjectMap [ rr:template "https://e.example/retail/data/c/{id}" ]'))


def test_空白ノードの主語を拒否する() -> None:
    """**行を引き直したときに同じ主語になる保証が無い**(決定7)。

    エージェントが参照を持ち回せない。
    """
    with pytest.raises(R2rmlError, match="空白ノード"):
        parse_mapping(
            _one(
                'rr:logicalTable [ rr:tableName "t" ] ;\n'
                '  rr:subjectMap [ rr:template "{id}" ; rr:termType rr:BlankNode ]'
            )
        )


def test_主語の定義が無い_TriplesMap_を拒否する() -> None:
    with pytest.raises(R2rmlError, match="主語の定義"):
        parse_mapping(
            "@prefix rr: <http://www.w3.org/ns/r2rml#> .\n"
            "<#M> a rr:TriplesMap ;\n"
            '  rr:logicalTable [ rr:tableName "t" ] .\n'
        )


def test_IRI_を決められない_subjectMap_を拒否する() -> None:
    """`rr:column` だけの `subjectMap` は列の値をそのまま IRI にする。

    **どんな IRI になるかが実データ次第**なので、封じ込めを検査できない。
    """
    with pytest.raises(R2rmlError, match="主語の IRI を決められない"):
        parse_mapping(
            _one('rr:logicalTable [ rr:tableName "t" ] ;\n  rr:subjectMap [ rr:column "iri" ]')
        )


def test_述語が無い_predicateObjectMap_を拒否する() -> None:
    with pytest.raises(R2rmlError, match="述語の定義"):
        parse_mapping(
            _one(
                'rr:logicalTable [ rr:tableName "t" ] ;\n'
                '  rr:subjectMap [ rr:template "https://e.example/retail/data/c/{id}" ] ;\n'
                '  rr:predicateObjectMap [ rr:objectMap [ rr:column "x" ] ]'
            )
        )


def test_述語をテンプレートで組み立てるものを拒否する() -> None:
    """**どの述語が現れるかが実データ次第になる**(決定6)。

    承認済み版に実在するかの検査が原理的にできなくなる。
    """
    with pytest.raises(R2rmlError, match="rr:template で組み立てる"):
        parse_mapping(
            _one(
                'rr:logicalTable [ rr:tableName "t" ] ;\n'
                '  rr:subjectMap [ rr:template "https://e.example/retail/data/c/{id}" ] ;\n'
                "  rr:predicateObjectMap [\n"
                '    rr:predicateMap [ rr:template "https://e.example/retail#{attr}" ] ;\n'
                '    rr:objectMap [ rr:column "v" ]\n'
                "  ]"
            )
        )


def test_rr_graph_を拒否する() -> None:
    """**版のグラフ IRI を名乗るマッピングを作れてしまう**(決定8)。"""
    with pytest.raises(R2rmlError, match="rr:graph"):
        parse_mapping(
            _one(
                'rr:logicalTable [ rr:tableName "t" ] ;\n'
                '  rr:subjectMap [ rr:template "https://e.example/retail/data/c/{id}" ;\n'
                "    rr:graph <urn:ontology:graph/retail/1.0.0> ]"
            )
        )


def test_rr_graphMap_を拒否する() -> None:
    with pytest.raises(R2rmlError, match="rr:graph"):
        parse_mapping(
            _one(
                'rr:logicalTable [ rr:tableName "t" ] ;\n'
                '  rr:subjectMap [ rr:template "https://e.example/retail/data/c/{id}" ;\n'
                '    rr:graphMap [ rr:template "urn:ontology:graph/retail/{v}" ] ]'
            )
        )


def test_rr_parentTriplesMap_を拒否する() -> None:
    """**借りた先の検査結果に依存する**(決定9)。

    `rr:template` で相手の IRI を組み立てる形に寄せる(実測でその形が動く)。
    """
    with pytest.raises(R2rmlError, match="rr:parentTriplesMap"):
        parse_mapping(
            _PREFIXES
            + """
<#A> a rr:TriplesMap ;
  rr:logicalTable [ rr:tableName "a" ] ;
  rr:subjectMap [ rr:template "https://e.example/retail/data/a/{id}" ] .
<#B> a rr:TriplesMap ;
  rr:logicalTable [ rr:tableName "b" ] ;
  rr:subjectMap [ rr:template "https://e.example/retail/data/b/{id}" ] ;
  rr:predicateObjectMap [
    rr:predicate ex:ref ;
    rr:objectMap [ rr:parentTriplesMap <#A> ]
  ] .
"""
        )
