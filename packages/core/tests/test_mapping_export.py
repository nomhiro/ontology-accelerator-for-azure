"""領域間マッピングの Turtle 書き出し(ADR-0031、`P2B-17`)。

ADR-0023 決定7 は「トリプルストアには射影しない」と決め、その結果
「**SPARQL だけを使うクライアントからは見えない**」というコストを受け入れて
いた。射影しない判断は維持したまま(ADR-0031 決定1)、**標準の RDF として
取り出せる**ようにした(決定2)。ADR-0026 の PROV-O 書き出しと同じ形である。

ここで固定するのは 4 つである。

1. **素の SKOS のトリプルが出る**(決定3)。そのまま引ける。ADR-0023 決定1 が
   述語を SKOS の 5 つに限ったのは論理的帰結を持たないからで、載せても
   推論器が制約を流し込まない
2. **記述ノードにメタデータが載る**(決定3)。`reason` と終点の生死は
   SKOS のトリプルには載らない。標準に写すために情報を落とさない
3. **`ont:targetStatus` は `unknown` でも出る**(決定4)
4. **記述ノードは空白ノードではない**(決定3)。2 回の書き出しを差分比較できる

**検査はパースし直したグラフに対して行う**(`test_prov.py` と同じ理由)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import unquote

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, SKOS

from ontology_core.mapping import (
    MAPPING_NODE_BASE,
    MAPPING_ONT_NAMESPACE,
    mapping_node_iri,
    render_mappings,
)
from ontology_core.models import TermMapping

_NS = "sales-ns"
_SOURCE = "https://e.example/sales#Gold"
_TARGET = "https://e.example/finance#Retired"
_SUCCESSOR = "https://e.example/finance#KeyAccount"
_AT = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
_EXPORTED = datetime(2026, 9, 12, tzinfo=UTC)

ONT = URIRef(MAPPING_ONT_NAMESPACE)


def _ont(local: str) -> URIRef:
    return URIRef(MAPPING_ONT_NAMESPACE + local)


def _mapping(
    *,
    source: str = _SOURCE,
    target: str = _TARGET,
    predicate: str = "closeMatch",
    reason: str = "同じ顧客区分を指している",
    reciprocal: bool = False,
    disputed: bool = False,
    counterpart: str | None = None,
    status: str = "active",
    successor: str | None = None,
    note: str = "",
) -> TermMapping:
    return TermMapping(
        namespace=_NS,
        source_term=source,
        target_term=target,
        predicate=predicate,
        predicate_iri=f"http://www.w3.org/2004/02/skos/core#{predicate}",
        reason=reason,
        declared_by="s-owner-oid",
        declared_at=_AT,
        reciprocal=reciprocal,
        disputed=disputed,
        counterpart_predicate=counterpart,
        target_status=status,
        target_successor=successor,
        target_status_note=note,
    )


def _render(*mappings: TermMapping) -> Graph:
    """書き出してパースし直す。**壊れた Turtle はここで例外になる。**"""
    graph = Graph()
    graph.parse(data=render_mappings(mappings, exported_at=_EXPORTED), format="turtle")
    return graph


def _node(source: str = _SOURCE, target: str = _TARGET) -> URIRef:
    return URIRef(mapping_node_iri(_NS, source, target))


# ------------------------------------------------------- 素の SKOS トリプル


@pytest.mark.parametrize(
    "predicate",
    ["exactMatch", "closeMatch", "broadMatch", "narrowMatch", "relatedMatch"],
)
def test_素の_SKOS_トリプルが出る(predicate: str) -> None:
    """**これが書き出しの主目的である**(ADR-0031 決定3)。

    `<source> skos:closeMatch <target>` がそのまま引ける。SKOS の述語は
    論理的帰結を持たない(ADR-0023 決定1)ので、載せても推論器が制約を
    流し込まない。
    """
    graph = _render(_mapping(predicate=predicate))
    assert (URIRef(_SOURCE), SKOS[predicate], URIRef(_TARGET)) in graph


def test_逆向きのトリプルは出さない() -> None:
    """**逆向きは自動で作らない**(ADR-0023 決定3)。

    書き出しで作ってしまうと、**相手が宣言していない主張が RDF として
    出回る** — 決定3 が最も避けたい形である。
    """
    graph = _render(_mapping(predicate="exactMatch"))
    assert (URIRef(_TARGET), SKOS.exactMatch, URIRef(_SOURCE)) not in graph


# ----------------------------------------------------------- 記述ノード


def test_理由は記述ノードに出る() -> None:
    """**標準に写すために情報を落とさない**(ADR-0026 決定4 と同じ判断)。

    ADR-0023 決定5 は `reason` を必須にした。「なぜ同じ(近い)と言えるのか」が
    無いマッピングは根拠にならない。
    """
    graph = _render(_mapping())
    assert (_node(), RDF.type, _ont("Mapping")) in graph
    assert (_node(), RDFS.comment, Literal("同じ顧客区分を指している")) in graph
    assert (_node(), _ont("declaredIn"), Literal(_NS)) in graph
    assert (_node(), _ont("declaredBy"), Literal("s-owner-oid")) in graph
    assert (_node(), _ont("source"), URIRef(_SOURCE)) in graph
    assert (_node(), _ont("target"), URIRef(_TARGET)) in graph


def test_記述ノードは空白ノードではない() -> None:
    """**2 回の書き出しを差分比較できるようにする**(ADR-0031 決定3)。

    空白ノードだと再取得したときに同じノードだと分からない。空白ノードの
    扱いでこのリポジトリは既に痛い目を見ている(ADR-0016 決定5)。
    """
    first = _render(_mapping())
    second = _render(_mapping())
    assert set(first.subjects(RDF.type, _ont("Mapping"))) == {_node()}
    assert set(first.subjects(RDF.type, _ont("Mapping"))) == set(
        second.subjects(RDF.type, _ont("Mapping"))
    )


def test_述語を変えてもノードの同一性は変わらない() -> None:
    """**一意制約と同じ 3 つ組で決める。**

    付け替え(`declare` のやり直し)でノードの同一性が変わると、
    差分比較が「削除 + 追加」に見える。
    """
    assert mapping_node_iri(_NS, _SOURCE, _TARGET) == mapping_node_iri(_NS, _SOURCE, _TARGET)
    close = _render(_mapping(predicate="closeMatch"))
    exact = _render(_mapping(predicate="exactMatch"))
    assert set(close.subjects(RDF.type, _ont("Mapping"))) == set(
        exact.subjects(RDF.type, _ont("Mapping"))
    )


def test_相互の宣言と争いが出る() -> None:
    graph = _render(_mapping(reciprocal=True, disputed=True, counterpart="exactMatch"))
    assert (_node(), _ont("reciprocal"), Literal(True)) in graph
    assert (_node(), _ont("disputed"), Literal(True)) in graph
    assert (_node(), _ont("counterpartPredicate"), Literal("exactMatch")) in graph


def test_相手の宣言が無ければ_counterpart_は出さない() -> None:
    graph = _render(_mapping(counterpart=None))
    assert not list(graph.triples((_node(), _ont("counterpartPredicate"), None)))
    # **`reciprocal` は偽でも出す。** 「相手が宣言していない」は事実である。
    assert (_node(), _ont("reciprocal"), Literal(False)) in graph


# --------------------------------------------------------- 終点の生死


def test_終点の生死は必ず出る() -> None:
    """**`unknown` でも出す**(ADR-0031 決定4)。

    省略すると「問題なし」と読まれる。ADR-0016 決定5 / ADR-0020 決定3 /
    ADR-0021 決定1 / ADR-0022 決定5 / ADR-0025 決定3 / ADR-0026 決定5 /
    ADR-0027 決定5 / ADR-0030 決定1 と同じ原則の 9 例目。
    """
    graph = _render(_mapping(status="unknown", note="権限がありません"))
    assert (_node(), _ont("targetStatus"), Literal("unknown")) in graph
    assert (_node(), _ont("targetStatusNote"), Literal("権限がありません")) in graph


def test_廃止されていれば後継も出る() -> None:
    """**後継が無いと警告が使えない。** 張り替える先を知る必要がある。"""
    graph = _render(_mapping(status="deprecated", successor=_SUCCESSOR))
    assert (_node(), _ont("targetStatus"), Literal("deprecated")) in graph
    assert (_node(), _ont("targetSuccessor"), URIRef(_SUCCESSOR)) in graph


def test_後継が無ければ出さない() -> None:
    graph = _render(_mapping(status="deprecated", successor=None))
    assert not list(graph.triples((_node(), _ont("targetSuccessor"), None)))


def test_理由が空なら_note_を出さない() -> None:
    graph = _render(_mapping(status="active", note=""))
    assert not list(graph.triples((_node(), _ont("targetStatusNote"), None)))


# --------------------------------------------------------------- 束


def test_件数と書き出した時刻が出る() -> None:
    graph = _render(_mapping(), _mapping(source="https://e.example/sales#Other"))
    bundle = URIRef("urn:ontology:mapping/export")
    assert (bundle, _ont("mappingCount"), Literal(2)) in graph
    assert (bundle, _ont("exportedAt"), Literal(_EXPORTED)) in graph
    assert len(set(graph.objects(bundle, _ont("includes")))) == 2


def test_マッピングが無くても束は出る() -> None:
    """**「0 件」と「書き出していない」を区別する**(ADR-0026 決定5 と同じ)。"""
    graph = _render()
    bundle = URIRef("urn:ontology:mapping/export")
    assert (bundle, _ont("mappingCount"), Literal(0)) in graph
    assert not list(graph.triples((None, RDF.type, _ont("Mapping"))))


# --------------------------------------------------------- 壊れた入力


def test_ノードの_IRI_は素片を持たず_3_段になる() -> None:
    """**用語 IRI を符号化する理由はこれである**(ADR-0031 決定3)。

    このプロジェクトの用語 IRI はほぼ必ず `#` を含む(`base_iri` が
    `…/sales#` の形)。そのまま連結すると **1 つの IRI に `#` が 3 つ並び**、
    RFC 3986 では素片は 1 つだけなので**妥当な IRI ではなくなる**。
    厳格な IRI 実装は文書ごと拒否する。

    `/` も区切りに使っているので、符号化しないと**段の数が変わって分解
    できなくなる**。

    **構文の破壊ではなく識別子の妥当性を守っている** — rdflib は緩いので
    黙って通してしまい、受け取った側で初めて壊れる。だから出す側で検査する。
    """
    iri = mapping_node_iri(_NS, _SOURCE, _TARGET)
    assert iri.startswith(MAPPING_NODE_BASE)
    rest = iri[len(MAPPING_NODE_BASE) :]
    assert "#" not in rest, f"素片が混ざっている: {iri}"
    assert "?" not in rest, f"クエリが混ざっている: {iri}"
    assert len(rest.split("/")) == 3, f"段の数が 3 ではない: {iri}"
    # 元の値は復元できる。
    namespace, source, target = (unquote(part) for part in rest.split("/"))
    assert (namespace, source, target) == (_NS, _SOURCE, _TARGET)


def test_主体の文字列は_literal_に入る() -> None:
    """`declared_by` は IRI にしない。**任意の文字列が入りうる。**"""
    graph = _render(_mapping())
    assert (_node(), _ont("declaredBy"), Literal("s-owner-oid")) in graph
