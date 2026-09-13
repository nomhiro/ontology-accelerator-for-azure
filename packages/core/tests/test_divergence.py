"""定義と実データの乖離検出(ADR-0047、`P3-05`)。

**このファイルでいちばん重要なのは「0 件の 3 つの意味を分ける」ことである。**

| 何が起きたか | 状態 |
|---|---|
| マッピングがそのクラスを作っていない | `unmapped`(**乖離ではない**) |
| マッピングはあるのに 0 件 | `empty`(**乖離である**) |
| 照会できなかった | `unknown`(**何も言えない**) |

3 つを 1 つの `0` に潰すと、**マッピングの書き忘れも Ontop の停止も
「実データが無い」として報告される**。

ここで固定するのは 5 つである。

1. **形から探りを作れる**(`sh:minCount >= 1` のプロパティだけを必須と見る)
2. **4 状態を混ぜない**
3. **`conclusive` が「全部調べきれたか」を正しく表す** — `unknown` が
   1 つでもあれば偽である
4. **読めなかった応答を `False` / `0` にしない**
5. **上限を超えた分を黙って「乖離なし」にしない**
"""

from __future__ import annotations

import pytest

from ontology_core.divergence import (
    MAX_PROBES,
    ClassDivergence,
    DivergenceReport,
    DivergenceStatus,
    ProbeOutcome,
    PropertyDivergence,
    ShapeTarget,
    boolean_of,
    build_class_divergence,
    class_probe,
    count_of,
    count_planned_probes,
    missing_property_probe,
    plan_probes,
    shape_targets,
    total_probes,
    unmapped_report,
)

_NS = "https://e.example/retail#"

_SHAPES = f"""\
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix ex:   <{_NS}> .

ex:Customer a owl:Class .
ex:Order a owl:Class .

ex:CustomerShape a sh:NodeShape ;
  sh:targetClass ex:Customer ;
  sh:property [
    sh:path ex:fullName ;
    sh:datatype xsd:string ;
    sh:minCount 1 ;
  ] ;
  sh:property [
    sh:path ex:tier ;
    sh:datatype xsd:string ;
  ] .

ex:OrderShape a sh:NodeShape ;
  sh:targetClass ex:Order ;
  sh:property [
    sh:path ex:placedBy ;
    sh:minCount 1 ;
  ] .
"""

_TARGET = ShapeTarget(
    shape=f"{_NS}CustomerShape",
    target_class=f"{_NS}Customer",
    required_paths=(f"{_NS}fullName",),
)


# ------------------------------------- 形から探りを作る(決定4)


def test_targetClass_を持つ形を取り出す() -> None:
    targets = shape_targets(_SHAPES)
    assert [t.target_class for t in targets] == [f"{_NS}Customer", f"{_NS}Order"]
    assert [t.shape for t in targets] == [f"{_NS}CustomerShape", f"{_NS}OrderShape"]


def test_minCount_が_1_以上のプロパティだけを必須と見る() -> None:
    """**任意のプロパティが欠けていることは乖離ではない。**

    `ex:tier` は `sh:minCount` が無いので入らない。
    """
    customer = shape_targets(_SHAPES)[0]
    assert customer.required_paths == (f"{_NS}fullName",)


def test_minCount_が_0_のプロパティは必須ではない() -> None:
    turtle = (
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        f"@prefix ex: <{_NS}> .\n"
        "ex:S a sh:NodeShape ; sh:targetClass ex:C ;\n"
        "  sh:property [ sh:path ex:p ; sh:minCount 0 ] .\n"
    )
    assert shape_targets(turtle)[0].required_paths == ()


def test_minCount_が_型違いのときは必須と見ない() -> None:
    """`sh:minCount "いち"` は SHACL-SHACL が捕まえる(`shacl.py` の段階 1)。

    **ここで例外にしない** — 乖離の測定が SHACL の構文検査を兼ねると、
    どちらの問題なのかが分からなくなる。
    """
    turtle = (
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        f"@prefix ex: <{_NS}> .\n"
        "ex:S a sh:NodeShape ; sh:targetClass ex:C ;\n"
        '  sh:property [ sh:path ex:p ; sh:minCount "いち" ] .\n'
    )
    assert shape_targets(turtle)[0].required_paths == ()


def test_空白ノードの形は取らない() -> None:
    """**報告に出す識別子が無い。** 運用者が「どの形の話か」を辿れない。"""
    turtle = (
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        f"@prefix ex: <{_NS}> .\n"
        "[] a sh:NodeShape ; sh:targetClass ex:C .\n"
    )
    assert shape_targets(turtle) == ()


def test_targetClass_がリテラルの形は取らない() -> None:
    """どのノードにも当たらない形である(`shacl.py` の段階 1 が報告する)。"""
    turtle = (
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        f"@prefix ex: <{_NS}> .\n"
        'ex:S a sh:NodeShape ; sh:targetClass "C" .\n'
    )
    assert shape_targets(turtle) == ()


def test_形が無い_TTL_は空のタプルを返す() -> None:
    """**例外にしない。** 形が無いのは異常ではない(スキーマだけの版)。"""
    assert shape_targets(f"@prefix ex: <{_NS}> .\nex:C a ex:Thing .\n") == ()


def test_解析できない_TTL_は例外になる() -> None:
    """**空のタプルを返さない。** 「形が無かった」と「読めなかった」を混ぜない。"""
    with pytest.raises(ValueError, match="解析できません"):
        shape_targets("@prefix sh: <http://www.w3.org/ns/shacl#> . ex:S a")


# ------------------------------------- 探りの中身


def test_クラスの探りは_ASK_である() -> None:
    """**件数は要らない。** 要るのは「1 件以上あるか」だけである。"""
    assert class_probe(f"{_NS}Customer") == f"ASK {{ ?s a <{_NS}Customer> }}"


def test_欠けている件数の探りは_OPTIONAL_と_BOUND_で書く() -> None:
    """**`FILTER NOT EXISTS` では書けない。**

    Ontop 5.3.0 がサポートしていない(実測。`OntopUnsupportedKGQueryException`
    で HTTP 500 になる)。`NOT EXISTS` のほうが素直に読めるので、
    **「読みやすくする」変更で戻されないように、ここで禁止を固定する。**
    """
    query = missing_property_probe(f"{_NS}Customer", f"{_NS}fullName")
    assert "COUNT(DISTINCT ?s)" in query
    assert "OPTIONAL" in query
    assert "!BOUND(?v)" in query
    # **戻してはいけない形。** 実物が 500 を返す。
    assert "NOT EXISTS" not in query
    assert f"<{_NS}Customer>" in query
    assert f"<{_NS}fullName>" in query
    # **`LIMIT` を付けない。** 集約に付けると意味が変わる。
    assert "LIMIT" not in query


# ------------------------------------- 応答の読み取り(決定7)


def test_ASK_の応答から真偽を読む() -> None:
    assert boolean_of({"boolean": True}) is True
    assert boolean_of({"boolean": False}) is False


def test_読めない_ASK_の応答を_False_にしない() -> None:
    """**「答えが偽だった」と「応答を読めなかった」は違う。**"""
    assert boolean_of({}) is None
    assert boolean_of({"boolean": "yes"}) is None
    assert boolean_of({"results": {"bindings": []}}) is None


def test_COUNT_の応答から件数を読む() -> None:
    payload = {"results": {"bindings": [{"missing": {"type": "literal", "value": "3"}}]}}
    assert count_of(payload) == 3


def test_読めない_COUNT_の応答を_0_にしない() -> None:
    """**「0 件だった」と「数えられなかった」は違う**(ADR-0041 決定2 と同じ)。"""
    assert count_of({}) is None
    assert count_of({"results": {"bindings": []}}) is None
    assert count_of({"results": {"bindings": [{}]}}) is None
    assert count_of({"results": {"bindings": [{"missing": {"value": "たくさん"}}]}}) is None
    assert count_of({"boolean": True}) is None


def test_測った_0_は_0_として読む() -> None:
    payload = {"results": {"bindings": [{"missing": {"value": "0"}}]}}
    assert count_of(payload) == 0


# ------------------------------------- 4 状態(決定2)


def test_実データがあれば_matched() -> None:
    result = build_class_divergence(_TARGET, class_outcome=ProbeOutcome(value=True))
    assert result.status is DivergenceStatus.MATCHED


def test_マッピングはあるのに_0_件なら_empty() -> None:
    """**これが乖離である。**"""
    result = build_class_divergence(_TARGET, class_outcome=ProbeOutcome(value=False))
    assert result.status is DivergenceStatus.EMPTY
    assert "実データが 1 件もありません" in result.note


def test_聞けなかったら_unknown_で理由が入る() -> None:
    """**`empty` にしない。** 「実データが無い」と「聞けなかった」は対処が違う。"""
    result = build_class_divergence(
        _TARGET, class_outcome=ProbeOutcome(value=None, note="到達できません", failed=True)
    )
    assert result.status is DivergenceStatus.UNKNOWN
    assert result.note == "到達できません"


def test_unknown_の理由は空にしない() -> None:
    """**理由が無い `unknown` は「問題なし」と読まれる。**"""
    result = build_class_divergence(_TARGET, class_outcome=ProbeOutcome(value=None))
    assert result.status is DivergenceStatus.UNKNOWN
    assert result.note


def test_マッピングが作っていないクラスは_unmapped() -> None:
    """**乖離ではない。** そもそも実データへの入口が無い。"""
    result = unmapped_report(_TARGET, mapped_predicates=(), note="入口がありません")
    assert result.status is DivergenceStatus.UNMAPPED
    assert result.properties == ()


def test_クラスが_matched_でなければプロパティを見ない() -> None:
    """**インスタンスが無いクラスで「プロパティが欠けている」と言っても意味がない。**"""
    result = build_class_divergence(
        _TARGET,
        class_outcome=ProbeOutcome(value=False),
        property_outcomes={f"{_NS}fullName": ProbeOutcome(value=5)},
    )
    assert result.status is DivergenceStatus.EMPTY
    assert result.properties == ()


def test_欠けている件数が_0_ならプロパティは_matched() -> None:
    result = build_class_divergence(
        _TARGET,
        class_outcome=ProbeOutcome(value=True),
        property_outcomes={f"{_NS}fullName": ProbeOutcome(value=0)},
    )
    assert result.properties[0].status is DivergenceStatus.MATCHED
    assert result.properties[0].missing_count == 0


def test_欠けている件数が_1_以上ならプロパティは_empty() -> None:
    result = build_class_divergence(
        _TARGET,
        class_outcome=ProbeOutcome(value=True),
        property_outcomes={f"{_NS}fullName": ProbeOutcome(value=7)},
    )
    assert result.properties[0].status is DivergenceStatus.EMPTY
    assert result.properties[0].missing_count == 7


def test_数えられなかったプロパティは_unknown_で件数は_None() -> None:
    result = build_class_divergence(
        _TARGET,
        class_outcome=ProbeOutcome(value=True),
        property_outcomes={
            f"{_NS}fullName": ProbeOutcome(value=None, note="数えられません", failed=True)
        },
    )
    assert result.properties[0].status is DivergenceStatus.UNKNOWN
    assert result.properties[0].missing_count is None


def test_マッピングが作っていない述語は_unmapped() -> None:
    result = build_class_divergence(
        _TARGET,
        class_outcome=ProbeOutcome(value=True),
        unmapped_paths=(f"{_NS}fullName",),
        unmapped_note="述語の入口がありません",
    )
    assert result.properties[0].status is DivergenceStatus.UNMAPPED
    assert result.properties[0].note == "述語の入口がありません"


# ------------------------------------- 探りの計画(決定3・5)


def test_マッピングが作っていないクラスには探りを投げない() -> None:
    targets = shape_targets(_SHAPES)
    plan = plan_probes(
        targets, mapped_classes={f"{_NS}Customer"}, mapped_predicates={f"{_NS}fullName"}
    )
    assert [t.target_class for t, _ in plan] == [f"{_NS}Customer"]


def test_マッピングが作っていない述語には探りを投げない() -> None:
    targets = shape_targets(_SHAPES)
    plan = plan_probes(targets, mapped_classes={f"{_NS}Customer"}, mapped_predicates=set())
    assert plan[0][1] == ()


def test_上限を超えたら計画に入れない() -> None:
    targets = shape_targets(_SHAPES)
    plan = plan_probes(
        targets,
        mapped_classes={f"{_NS}Customer", f"{_NS}Order"},
        mapped_predicates={f"{_NS}fullName", f"{_NS}placedBy"},
        limit=2,
    )
    # Customer のクラス 1 本 + fullName 1 本で予算を使い切る。
    assert count_planned_probes(plan) == 2
    assert [t.target_class for t, _ in plan] == [f"{_NS}Customer"]


def test_必要な探りの本数を上限と別に数える() -> None:
    """**「上限に達した」だけでは、どれだけ見ていないのかが分からない。**"""
    targets = shape_targets(_SHAPES)
    needed = total_probes(
        targets,
        mapped_classes={f"{_NS}Customer", f"{_NS}Order"},
        mapped_predicates={f"{_NS}fullName", f"{_NS}placedBy"},
    )
    assert needed == 4  # クラス 2 本 + 必須プロパティ 2 本


def test_上限の既定は_100_である() -> None:
    assert MAX_PROBES == 100


# ------------------------------------- 報告の読み方(決定6)


def _report(*classes: ClassDivergence, skipped: int = 0) -> DivergenceReport:
    return DivergenceReport(classes=classes, skipped_probes=skipped)


def test_empty_なクラスは乖離として数える() -> None:
    report = _report(ClassDivergence(shape="s", target_class="c", status=DivergenceStatus.EMPTY))
    assert len(report.diverged) == 1


def test_欠けている必須プロパティがあれば乖離として数える() -> None:
    report = _report(
        ClassDivergence(
            shape="s",
            target_class="c",
            status=DivergenceStatus.MATCHED,
            properties=(
                PropertyDivergence(path="p", status=DivergenceStatus.EMPTY, missing_count=2),
            ),
        )
    )
    assert len(report.diverged) == 1


def test_unmapped_は乖離として数えない() -> None:
    """**そもそも入口が無い。** 直すのはマッピングであって定義ではない。"""
    report = _report(ClassDivergence(shape="s", target_class="c", status=DivergenceStatus.UNMAPPED))
    assert report.diverged == ()
    assert report.unknown == ()


def test_unknown_は乖離として数えないが_conclusive_を偽にする() -> None:
    """**これがこのファイルの主題である。**

    「調べられなかった」を「乖離なし」に丸めない。
    """
    report = _report(
        ClassDivergence(shape="s", target_class="c", status=DivergenceStatus.UNKNOWN, note="x")
    )
    assert report.diverged == ()
    assert len(report.unknown) == 1
    assert report.conclusive is False


def test_プロパティが_unknown_でも_conclusive_は偽になる() -> None:
    report = _report(
        ClassDivergence(
            shape="s",
            target_class="c",
            status=DivergenceStatus.MATCHED,
            properties=(PropertyDivergence(path="p", status=DivergenceStatus.UNKNOWN, note="x"),),
        )
    )
    assert report.conclusive is False


def test_上限に達していたら_conclusive_は偽になる() -> None:
    report = _report(
        ClassDivergence(shape="s", target_class="c", status=DivergenceStatus.MATCHED),
        skipped=3,
    )
    assert report.conclusive is False


def test_全部調べきれて乖離が無ければ_conclusive_は真() -> None:
    report = _report(
        ClassDivergence(
            shape="s",
            target_class="c",
            status=DivergenceStatus.MATCHED,
            properties=(
                PropertyDivergence(path="p", status=DivergenceStatus.MATCHED, missing_count=0),
            ),
        )
    )
    assert report.conclusive is True
    assert report.diverged == ()


def test_形が無かったことは乖離が無かったこととは違う() -> None:
    report = DivergenceReport(no_shapes=True)
    assert report.no_shapes is True
    assert report.diverged == ()
    # **形が無いだけなら調べきれている**(調べるものが無かった)。
    assert report.conclusive is True
