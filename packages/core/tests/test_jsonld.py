"""RDF の JSON-LD 直列化と `Accept` の解釈(ADR-0036、`P2A-17`)。

ここで固定するのは 4 つである。

1. **`@context` が文書に埋め込まれる**(決定2)。外部 URL を指さない —
   この API のコンテキスト URL は認証が要り、デプロイごとに違う
2. **使っていない語彙を並べない**(決定4)。rdflib の既定は 30 件の接頭辞を
   書き出し、その文書に現れない語彙が混ざる
3. **形は常に `@graph` の配列**(決定7)。空の書き出しだけ形が変わらない
4. **`Accept` の解釈が既定を壊さない**(決定5)。`q=0` を尊重し、
   `application/json` では切り替えず、解釈できなければ Turtle に落ちる

**同型性の検査は `test_prov.py` / `test_mapping_export.py` 側ではなくここに
置く。** 2 つの直列化が同じグラフから出ることは JSON-LD 側の責務である。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import PROV, RDF, XSD

from ontology_core.jsonld import (
    JSONLD_MEDIA_TYPE,
    MAPPING_CONTEXT,
    PROVENANCE_CONTEXT,
    prefers_jsonld,
    render_jsonld,
)
from ontology_core.mapping import MappingPredicate, mapping_graph, predicate_iri
from ontology_core.models import ActorType, AuditEvent, TermMapping
from ontology_core.prov import ONT, provenance_graph

_NS = "retail-core"
_AT = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
_EXPORTED = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)


def _events(count: int = 1) -> list[AuditEvent]:
    return [
        AuditEvent(
            id=40 + i,
            namespace=_NS,
            action="published",
            actor=f"actor-{i}",
            occurred_at=_AT,
            subject=f"{_NS}@{i + 1}.0.0",
            # 日本語を入れる(`ensure_ascii=False` を固定するため)。
            reason="初版を公開した",
            actor_type=ActorType.USER,
        )
        for i in range(count)
    ]


def _render(count: int) -> dict[str, Any]:
    graph = provenance_graph(_events(count), namespace=_NS, truncated=False, exported_at=_EXPORTED)
    document: dict[str, Any] = json.loads(render_jsonld(graph, context=PROVENANCE_CONTEXT))
    return document


# ------------------------------------------------- `@context` は埋め込む


def test_context_が文書に埋め込まれる() -> None:
    """**外部 URL を指さない**(ADR-0036 決定2)。

    この API のコンテキスト URL は認証が要る(JSON-LD プロセッサは Bearer
    トークンを持たない)うえ、デプロイごとに違う。**埋め込めば文書が
    自己完結する。**
    """
    context = _render(1)["@context"]
    assert isinstance(context, dict), "`@context` が URL(文字列)になっている"
    assert context["prov"] == str(PROV)
    assert context["ont"] == str(ONT)


def test_使っていない語彙を並べない() -> None:
    """**rdflib の既定の接頭辞表を出さない**(決定4)。

    既定では Brick / GeoSPARQL / SOSA など**この文書に一切現れない語彙**が
    30 件並ぶ。「この文書はそれらについて何か言っている」と読める余地を
    作らない。
    """
    context = _render(1)["@context"]
    assert set(context) == set(PROVENANCE_CONTEXT), (
        "コンテキストに宣言していない接頭辞が混ざっている"
    )
    for unwanted in ("brick", "geo", "sosa", "odrl", "dcat", "schema"):
        assert unwanted not in context


def test_接頭辞は実際に使われているものだけ() -> None:
    """**コンテキストの側も膨らませない。**

    `PROVENANCE_CONTEXT` に使わない接頭辞を足すと、決定4 の検査は通るのに
    文書には現れない語彙が並ぶ。
    """
    graph = provenance_graph(_events(2), namespace=_NS, truncated=True, exported_at=_EXPORTED)
    used = {str(term).rsplit("#", 1)[0] + "#" for term in graph.predicates()}
    declared = {value for key, value in PROVENANCE_CONTEXT.items() if key != "xsd"}
    # `rdf:type` は JSON-LD が `@type` として扱うので述語に現れない。
    assert declared <= used | {str(PROV)}, f"使われていない接頭辞がある: {declared - used}"


def test_用語の別名を作らない() -> None:
    """**キーは `ont:action` のまま**(決定3)。

    別名は 2 つ目の語彙であり、Turtle の述語名と同期させ続ける義務が
    生まれる。**情報は 1 つも増えない。**
    """
    context = _render(1)["@context"]
    assert all(isinstance(value, str) for value in context.values()), (
        "コンテキストに用語の定義(オブジェクト)が入っている"
    )
    node = next(n for n in _render(1)["@graph"] if "ont:action" in n)
    assert node["ont:action"] == "published"


# ------------------------------------------------------- 形を揃える


@pytest.mark.parametrize("count", [0, 1, 3])
def test_形は常に_graph_の配列(count: int) -> None:
    """**空の書き出しだけ形が変わらない**(決定7)。

    rdflib は主語が 1 つのときだけ平らなノードオブジェクトを返す。
    イベントが 0 件の書き出しは束のノードしか無いため、そのまま返すと
    形が変わる。
    """
    document = _render(count)
    assert isinstance(document["@graph"], list)
    assert len(document["@graph"]) >= 1  # 束のノードは必ず居る


def test_空のグラフは空の配列になる() -> None:
    """「ノードが 1 つある」と「ノードが無い」を同じ形で表す(決定7)。

    **これは rdflib 自身の振る舞いに依存している**(空のグラフには
    `"@graph": []` を出す。実測)。依存していることをここで固定しておく —
    将来 rdflib がここを変えたら、平らなノードを返す経路が空のときにも
    使われるようになる。
    """
    document = json.loads(render_jsonld(Graph(), context=PROVENANCE_CONTEXT))
    assert document["@graph"] == []
    assert document["@context"] == dict(PROVENANCE_CONTEXT)


def test_日本語をエスケープしない() -> None:
    """`\\uXXXX` でも正しい JSON だが、運用者が目で読めなくなる。"""
    graph = provenance_graph(_events(1), namespace=_NS, truncated=False, exported_at=_EXPORTED)
    text = render_jsonld(graph, context=PROVENANCE_CONTEXT)
    assert "初版を公開した" in text


# ------------------------------ 2 つの直列化が同じグラフから出る(決定6)


@pytest.mark.parametrize("count", [0, 1, 5])
def test_JSON_LD_と_Turtle_が同型である(count: int) -> None:
    """**この決定の実効部分である**(ADR-0036 決定6)。

    経路ごとにトリプルを組み立てると、片方だけ直したときに**表現によって
    内容が違う**という静かな不整合になり、どちらが正しいのかを判定する
    手段が無くなる。
    """
    graph = provenance_graph(_events(count), namespace=_NS, truncated=False, exported_at=_EXPORTED)
    round_trip = Graph()
    round_trip.parse(data=render_jsonld(graph, context=PROVENANCE_CONTEXT), format="json-ld")
    assert isomorphic(graph, round_trip), "JSON-LD と Turtle が違うグラフになっている"


def test_データ型が落ちない() -> None:
    """`xsd:dateTime` と `xsd:boolean` が文字列に潰れないこと。

    **潰れると「2026-09-12 は文字列である」と主張する文書になる。**
    """
    graph = provenance_graph(_events(1), namespace=_NS, truncated=True, exported_at=_EXPORTED)
    round_trip = Graph()
    round_trip.parse(data=render_jsonld(graph, context=PROVENANCE_CONTEXT), format="json-ld")
    bundle = next(round_trip.subjects(RDF.type, PROV.Bundle))
    assert (bundle, ONT.truncated, Literal(True)) in round_trip
    exported = next(round_trip.objects(bundle, ONT.exportedAt))
    assert isinstance(exported, Literal)
    assert exported.datatype == XSD.dateTime


def _mapping() -> TermMapping:
    return TermMapping(
        namespace="sales",
        source_term="https://e.example/sales#GoodCustomer",
        target_term="https://e.example/fin#PreferredAccount",
        predicate=MappingPredicate.CLOSE_MATCH.value,
        predicate_iri=predicate_iri(MappingPredicate.CLOSE_MATCH),
        declared_by="alice",
        declared_at=_AT,
        reason="近い概念だが同一ではない",
    )


def test_マッピングの書き出しも同型である() -> None:
    """**RDF を返す口はすべて同じ規則で交渉する**(決定8)。"""
    graph = mapping_graph([_mapping()], exported_at=_EXPORTED)
    round_trip = Graph()
    round_trip.parse(data=render_jsonld(graph, context=MAPPING_CONTEXT), format="json-ld")
    assert isomorphic(graph, round_trip)


def test_マッピングのコンテキストに_skos_が入っている() -> None:
    """素の SKOS のトリプルがこの書き出しの主目的である(ADR-0031 決定3)。"""
    assert MAPPING_CONTEXT["skos"] == "http://www.w3.org/2004/02/skos/core#"


def test_マッピングの書き出しはマッピングのコンテキストを使う() -> None:
    """**コンテキストを取り違えても同型性は壊れない**(圧縮しか変わらない)。

    だから同型性のテストでは捕まらない。**変異テストで見つけた穴である** —
    マッピングの書き出しに PROV-O のコンテキストを渡す変異が生き残った。
    """
    graph = mapping_graph([_mapping()], exported_at=_EXPORTED)
    document = json.loads(render_jsonld(graph, context=MAPPING_CONTEXT))
    assert document["@context"] == dict(MAPPING_CONTEXT)
    # 素の SKOS のトリプルが `skos:` に圧縮されていること
    # (= 正しいコンテキストが渡っていること)。
    assert any("skos:closeMatch" in node for node in document["@graph"])


def test_百分率符号化した_IRI_が壊れない() -> None:
    """マッピングのノード IRI は `#` を含む用語を符号化して埋めている。

    **JSON-LD の圧縮で接頭辞に化けないこと**を確かめる(`urn:ontology:` を
    接頭辞として宣言していないので化けないが、構造で固定しておく)。
    """
    graph = Graph()
    ont = Namespace("urn:ontology:prov#")
    node = URIRef("urn:ontology:mapping/sales/https%3A%2F%2Fe.example%2Fs%23A/x")
    graph.add((node, RDF.type, ont.Mapping))
    round_trip = Graph()
    round_trip.parse(data=render_jsonld(graph, context=MAPPING_CONTEXT), format="json-ld")
    assert (node, RDF.type, ont.Mapping) in round_trip


# ---------------------------------------------- `Accept` の解釈(決定5)


@pytest.mark.parametrize(
    "accept",
    [
        "application/ld+json",
        "application/ld+json; charset=utf-8",
        "APPLICATION/LD+JSON",
        "application/ld+json;q=0.9, text/turtle;q=0.5",
        "application/*",
    ],
)
def test_JSON_LD_を望むと判定する(accept: str) -> None:
    assert prefers_jsonld(accept)


@pytest.mark.parametrize(
    "accept",
    [
        None,
        "",
        "*/*",
        "text/turtle",
        "text/*",
        "application/xml",
    ],
)
def test_既定は_Turtle_である(accept: str | None) -> None:
    """**内容交渉は足すだけにする**(ADR-0036 決定5)。

    いまは `Accept` を完全に無視しているので、既定を変えると既存の
    クライアントが黙って壊れる。
    """
    assert not prefers_jsonld(accept)


def test_application_json_では切り替えない() -> None:
    """**既定で `application/json` を送る HTTP クライアントは多い**(決定5)。

    そこで切り替えると、いまこの口を Turtle として使っているクライアントが
    黙って壊れる。JSON-LD が欲しければ `application/ld+json` を明示する。
    """
    assert not prefers_jsonld("application/json")
    assert not prefers_jsonld("application/json, */*")


def test_q_が_0_なら返さない() -> None:
    """**明示的な拒否を肯定として読まない**(決定5)。

    部分文字列一致で判定するとここが通ってしまう。**最も悪い誤読である。**
    """
    assert not prefers_jsonld("application/ld+json;q=0")
    assert not prefers_jsonld("application/ld+json;q=0.0, text/turtle")
    assert not prefers_jsonld("application/ld+json; q=0")


def test_品質値が同じなら_Turtle_を返す() -> None:
    """HTTP は `Accept` の並び順に優先順位を持たせていない。

    どちらも等しく受け付けると言われたなら、**既定を変えない**。
    """
    assert not prefers_jsonld("text/turtle, application/ld+json")
    assert not prefers_jsonld("application/ld+json, text/turtle")


def test_より具体的な範囲が勝つ() -> None:
    """RFC 9110 の優先順位(`application/ld+json` > `application/*` > `*/*`)。

    **ワイルドカードで拒否しておいて、1 つだけ明示的に許す**という書き方が
    成立する。具体性を無視して最大値を取ると、この 2 つが同じ結果になる。
    """
    assert prefers_jsonld("application/*;q=0, application/ld+json")
    assert not prefers_jsonld("application/*, application/ld+json;q=0")


def test_品質値で_Turtle_が勝てる() -> None:
    assert not prefers_jsonld("application/ld+json;q=0.5, text/turtle;q=0.9")


@pytest.mark.parametrize(
    "accept",
    [
        "application/ld+json;q=abc",
        "application/ld+json;q=",
        "application/ld+json;q=2",
        "application/ld+json;q=-1",
        "text/turtle;q=nan-ish, application/ld+json",
    ],
)
def test_解釈できない_Accept_は既定に落とす(accept: str) -> None:
    """**迷ったら既存の振る舞いを変えない側に寄せる**(ADR-0036 決定5)。

    壊れた `q` を無視して先に進むと、壊れ方によって結果が変わる。
    """
    assert not prefers_jsonld(accept)


def test_媒体型の定数に_charset_を付けない() -> None:
    """`application/*` の既定は UTF-8 と定まっている(`text/*` と違う)。"""
    assert JSONLD_MEDIA_TYPE == "application/ld+json"
