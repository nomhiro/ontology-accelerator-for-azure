"""SHACL 検証のテスト(P2A-05、ADR-0005 決定1)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ontology_core.shacl import ShaclValidationError, validate_turtle_with_shacl

_PREFIXES = """
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix ex:   <https://e.example/#> .
"""

_SCHEMA_ONLY = (
    _PREFIXES
    + """
ex:Product a owl:Class ; rdfs:label "商品"@ja .
ex:ProductShape a sh:NodeShape ;
    sh:targetClass ex:Product ;
    sh:property [
        sh:path ex:sku ;
        sh:datatype xsd:string ;
        sh:minCount 1 ;
        sh:pattern "^[A-Z]{3}-[0-9]{6}$" ;
        sh:message "SKU は 'ABC-123456' の形式である必要があります"@ja ;
    ] .
"""
)


def test_schema_only_ontology_conforms() -> None:
    """スキーマだけの TTL は適合する(対象ノードが無いため)。

    これは「検証が甘い」のではなく正しい挙動である。実データへの適用は
    Ontop 経由の連邦クエリが前提で Phase 3(ADR-0009 の未解決事項)。
    """
    report = validate_turtle_with_shacl(_SCHEMA_ONLY)
    assert report.conforms is True
    assert report.shapes_conform is True
    assert report.data_conform is True
    assert report.messages() == []


def test_data_violation_is_detected_with_its_message() -> None:
    """TTL 自身がインスタンスを含む場合、制約違反を検出する。"""
    ttl = (
        _SCHEMA_ONLY
        + """
ex:p1 a ex:Product ; ex:sku "bad-sku" .
"""
    )
    report = validate_turtle_with_shacl(ttl)
    assert report.conforms is False
    assert report.data_conform is False
    assert report.shapes_conform is True
    # 定義に書いた sh:message が報告に含まれること(何を直すべきかが分かる)。
    assert "ABC-123456" in report.data_report
    assert any("違反" in m for m in report.messages())


def test_conforming_data_passes() -> None:
    ttl = (
        _SCHEMA_ONLY
        + """
ex:p1 a ex:Product ; ex:sku "ABC-123456" .
"""
    )
    assert validate_turtle_with_shacl(ttl).conforms is True


def test_shape_with_a_wrong_datatype_is_reported_not_crashed() -> None:
    """型が違う制約は、例外ではなく**違反として報告**される。

    段階 2(データ検証)に投げると pyshacl は読み込み時に例外を投げるため、
    「検証できなかった」になって違反箇所が失われる。段階 1 を先に走らせて
    そこで止めることで、どこがどう悪いかを報告できる。
    """
    ttl = (
        _PREFIXES
        + """
ex:Product a owl:Class .
ex:ProductShape a sh:NodeShape ;
    sh:targetClass ex:Product ;
    sh:property [ sh:path ex:sku ; sh:minCount "いち" ] .
"""
    )
    report = validate_turtle_with_shacl(ttl)
    assert report.conforms is False
    assert report.shapes_conform is False
    assert report.shapes_report != ""
    assert any("shapes 自体" in m for m in report.messages())


def test_shape_targeting_a_literal_is_only_caught_by_stage_one() -> None:
    """**段階 1 だけが捕まえるケース。** ここが段階 1 を置く理由である。

    `sh:targetClass` がリテラルを指している shape は、どのノードにも
    当たらない。そのためデータ検証(段階 2)は「違反ゼロ」を返す
    ——制約を書いたつもりが何も検査していない状態である。
    実測で段階 2 が適合を返すことを確認した上でこのテストを書いている。
    """
    ttl = (
        _PREFIXES
        + """
ex:Product a owl:Class .
ex:p1 a ex:Product .
ex:ProductShape a sh:NodeShape ;
    sh:targetClass "Product" ;
    sh:property [ sh:path ex:sku ; sh:minCount 1 ] .
"""
    )
    report = validate_turtle_with_shacl(ttl)
    assert report.shapes_conform is False, "段階 1 が検出する"
    assert report.conforms is False
    # 段階 2 は実行していない(実行しても適合を返し、何も分からない)。
    assert report.data_report == ""


def test_a_typo_in_a_constraint_name_is_a_known_limitation() -> None:
    """**制約名のタイプミスは両段階とも検出できない。** 既知の限界。

    SHACL の処理系は知らない述語を単に無視する仕様なので、`sh:minCoun` は
    「制約が 1 つも書かれていない shape」と区別がつかない。
    通ってしまうことを**テストとして固定しておく**。将来 SHACL 側に
    仕組みが入って検出できるようになったら、このテストが落ちて気づける。
    """
    ttl = (
        _PREFIXES
        + """
ex:Product a owl:Class .
ex:p1 a ex:Product .
ex:ProductShape a sh:NodeShape ;
    sh:targetClass ex:Product ;
    sh:property [ sh:path ex:sku ; sh:minCoun 1 ] .
"""
    )
    report = validate_turtle_with_shacl(ttl)
    assert report.conforms is True, "検出できないことを既知の限界として固定する"


def test_bundled_sample_conforms() -> None:
    """同梱サンプルが常に SHACL に適合すること。

    ここが落ちるのは、サンプルの shapes を壊したまま気づいていない状態である。
    """
    root = Path(__file__).resolve().parents[3]
    ttl = (root / "samples" / "retail-core.ttl").read_text(encoding="utf-8")
    report = validate_turtle_with_shacl(ttl)
    assert report.conforms is True, report.shapes_report + report.data_report


def test_broken_turtle_raises_validation_error_not_a_violation() -> None:
    """**「検証できなかった」を「違反ゼロ」と混同しない。**

    構文が壊れた TTL は `publish` の手前で `TurtleSyntaxError` に落ちる
    (P1-C2) が、万一ここへ来た場合も違反ゼロを返してはならない。
    """
    with pytest.raises(ShaclValidationError):
        validate_turtle_with_shacl("これは Turtle ではない <<<")
