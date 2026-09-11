"""領域間マッピング(ADR-0023、`P2B-10`)。

[ADR-0009](../../../../docs/adr/0009-ontology-operations.md) 決定8 は「領域を
またぐ矛盾は統合せずマッピングする」と決めた。その実装である。

## `owl:equivalentClass` を提供しない

ADR-0009 決定8 は SKOS の `*Match` と並べて `owl:equivalentClass` も候補に
挙げていた。**これを却下した**(ADR-0023 決定1)。理由は実測である。

営業(`a:`)と経理(`b:`)の「優良顧客」を、互いに素なクラスの下に置いたまま
結んで ELK にかけた。

| 結んだ述語 | ELK の結論 |
|---|---|
| `owl:equivalentClass` | **充足不能クラス 2 件**(両方が使えなくなった) |
| `skos:exactMatch` | 充足不能クラスなし |

**`equivalentClass` はクラスの外延の同一性を主張するので、推論器が一方の
制約を他方へ流し込む。** マッピングは領域が違うから張るもので、領域が違えば
制約が食い違うのが普通である。つまり「食い違っている 2 つを結ぶ」という
主用途において、論理的帰結を持つ述語は構造的に危ない。

しかも**この壊れ方は承認では止まらない** — 推論器は CI にしか居らず
(ADR-0021 決定5)、CI が検査するのは同梱物だけである。

## 「統合しない」は二重で担保している

1. **述語をここで制限する**(論理的帰結を持つ述語を受け付けない)
2. **マッピングを TTL に書かない**(PostgreSQL に構造化して持つ。ADR-0023
   決定2)。TTL に入らなければ**推論器の視界に入らない**

## 相違を消さない

**逆向きのマッピングを自動生成しない**(決定3)。「A が B に exactMatch と
言っている」と「B が A に exactMatch と言っている」は別の事実であり、
自動生成は**相手が宣言していない主張を相手の名前空間に作る**。

**両側が違う述語を宣言したら、どちらも消さずに「争われている」と報告する**
(決定4)。自動で片方に寄せる実装は、相違を消す実装である。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import quote

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, SKOS, XSD

from ontology_core.iri import TermIriError, validate_term_iri
from ontology_core.models import TermMapping

__all__ = [
    "MAPPING_NODE_BASE",
    "MAPPING_ONT_NAMESPACE",
    "SKOS_NAMESPACE",
    "MappingPredicate",
    "MappingSideBySide",
    "MappingValidationError",
    "compare_with_counterpart",
    "inverse_of",
    "mapping_node_iri",
    "predicate_iri",
    "render_mappings",
    "validate_mapping",
]

SKOS_NAMESPACE = "http://www.w3.org/2004/02/skos/core#"


class MappingPredicate(StrEnum):
    """マッピングに使える述語(ADR-0023 決定1)。

    **SKOS のマッピング述語 5 つだけである。** `owl:equivalentClass` と
    `owl:sameAs` は**意図的に含めない** — 論理的帰結を持つため、互いに素な
    クラスの下にある用語を結ぶと両方が充足不能になる(モジュールの docstring
    に実測を記録した)。

    性質(SKOS の定義):

    | 述語 | 対称か | 推移的か | 逆 |
    |---|---|---|---|
    | `exact_match` | 対称 | **推移的** | 自分 |
    | `close_match` | 対称 | **推移的でない** | 自分 |
    | `broad_match` | 非対称 | — | `narrow_match` |
    | `narrow_match` | 非対称 | — | `broad_match` |
    | `related_match` | 対称 | — | 自分 |

    **推移的閉包は計算しない**(ADR-0023 の却下した代替案)。`exact_match` は
    推移的だが `close_match` は推移的でないので、混ぜて閉包を取ると
    「近いだけの用語を交換可能として扱う」誤った同一視を作る。
    """

    EXACT_MATCH = "exactMatch"
    CLOSE_MATCH = "closeMatch"
    BROAD_MATCH = "broadMatch"
    NARROW_MATCH = "narrowMatch"
    RELATED_MATCH = "relatedMatch"


def predicate_iri(predicate: MappingPredicate) -> str:
    """述語の絶対 IRI。TTL として書き出すとき、および API の表示に使う。"""
    return f"{SKOS_NAMESPACE}{predicate.value}"


# 逆向きの述語。**自動でマッピングを作るためではなく、相手側の宣言と
# 突き合わせるため**に使う(ADR-0023 決定3・4)。
_INVERSE: dict[MappingPredicate, MappingPredicate] = {
    MappingPredicate.EXACT_MATCH: MappingPredicate.EXACT_MATCH,
    MappingPredicate.CLOSE_MATCH: MappingPredicate.CLOSE_MATCH,
    MappingPredicate.RELATED_MATCH: MappingPredicate.RELATED_MATCH,
    MappingPredicate.BROAD_MATCH: MappingPredicate.NARROW_MATCH,
    MappingPredicate.NARROW_MATCH: MappingPredicate.BROAD_MATCH,
}


def inverse_of(predicate: MappingPredicate) -> MappingPredicate:
    """逆向きに宣言されるべき述語。

    対称な述語(`exactMatch` / `closeMatch` / `relatedMatch`)は自分自身、
    `broadMatch` と `narrowMatch` は互いを返す。

    **これは逆向きのマッピングを作るための関数ではない**(ADR-0023 決定3 は
    自動生成を却下した)。相手側が宣言している述語と突き合わせて、
    **相互に宣言されているか / 争われているか**を判定するために使う。
    """
    return _INVERSE[predicate]


class MappingValidationError(ValueError):
    """マッピングとして受け付けられないことを表す。

    ルータがこれを捕まえて 422 にマップする。
    """


@dataclass(frozen=True)
class MappingSideBySide:
    """自分の宣言と、相手側の宣言を突き合わせた結果(ADR-0023 決定3・4)。

    Attributes:
        reciprocal: 相手側も同じ用語ペアを宣言しているか。
            **偽は異常ではない** — 相手がまだ宣言していないだけである
            (初期状態では片側だけが正常なので、これを警告にしない)。
        disputed: 相互に宣言されていて、**述語が食い違っている**か。
            `exactMatch` に対して `closeMatch` が返っている等。
            **どちらも消さない**(決定4)。
        counterpart: 相手側が宣言している述語。無ければ `None`。
    """

    reciprocal: bool
    disputed: bool
    counterpart: MappingPredicate | None


def compare_with_counterpart(
    predicate: MappingPredicate, counterpart: MappingPredicate | None
) -> MappingSideBySide:
    """自分の述語と相手側の述語を突き合わせる。

    **相手が宣言していないことと、相手が違うことを混同しない。**
    前者は `reciprocal=False`(正常)、後者は `disputed=True`(報告対象)である。
    """
    if counterpart is None:
        return MappingSideBySide(reciprocal=False, disputed=False, counterpart=None)
    return MappingSideBySide(
        reciprocal=True,
        disputed=counterpart is not inverse_of(predicate),
        counterpart=counterpart,
    )


def validate_mapping(
    *, source_term: str, target_term: str, predicate: str
) -> tuple[str, str, MappingPredicate]:
    """マッピングの入力を検証して正規化した組を返す。

    Returns:
        `(source_term, target_term, predicate)`。

    Raises:
        MappingValidationError: 述語が使えない、IRI が不正、
            または始点と終点が同じ用語のとき。
    """
    try:
        resolved = MappingPredicate(predicate)
    except ValueError as exc:
        allowed = ", ".join(p.value for p in MappingPredicate)
        # **`owl:equivalentClass` を名指しで説明する。** ADR-0009 決定8 が
        # 候補に挙げていたので、使おうとする人が必ず現れる。単に
        # 「使えません」と言うと設計判断だと分からない。
        hint = ""
        if predicate.endswith(("equivalentClass", "sameAs")):
            hint = (
                "。**論理的帰結を持つ述語はマッピングに使えません** — "
                "互いに素なクラスの下にある用語を結ぶと両方が充足不能になります"
                "(ADR-0023 決定1)"
            )
        raise MappingValidationError(
            f"述語 '{predicate}' は使えません(使えるのは {allowed}){hint}"
        ) from exc

    try:
        source = validate_term_iri(source_term)
        target = validate_term_iri(target_term)
    except TermIriError as exc:
        raise MappingValidationError(str(exc)) from exc

    if source == target:
        # 自分自身へのマッピングは情報を持たない。**黙って受け付けると、
        # 「相互に宣言されている」の判定が自分だけで成立してしまう。**
        raise MappingValidationError("始点と終点が同じ用語です")

    return source, target, resolved


#: 書き出しで使う独自語彙の名前空間(ADR-0031 決定3)。
#: `ontology_core.prov.ONT` と**同じ名前空間を使う** — 出自の書き出しと
#: マッピングの書き出しで接頭辞が 2 つに割れると、両方を読み込んだ側が
#: 「どちらの `ont:` か」を気にすることになる。
MAPPING_ONT_NAMESPACE = "urn:ontology:prov#"

#: 記述ノードの IRI の基底(ADR-0031 決定3)。
#:
#: **空白ノードにしない。** 再取得したときに同じノードだと分からず、
#: 2 回の書き出しを差分比較できない(空白ノードの扱いはこのリポジトリで
#: 既に痛い目を見ている。ADR-0016 決定5)。
MAPPING_NODE_BASE = "urn:ontology:mapping/"


def mapping_node_iri(namespace: str, source_term: str, target_term: str) -> str:
    """マッピングの記述ノードの IRI を返す。

    **一意制約と同じ 3 つ組で決める**(`namespace` / `source_term` /
    `target_term`)。述語は含めない — 付け替え(`declare` のやり直し)で
    ノードの同一性が変わってはいけない。

    **用語 IRI は百分率符号化して埋める。** このプロジェクトの用語 IRI は
    ほぼ必ず `#` を含む(`base_iri` が `…/sales#` の形)ので、そのまま連結すると
    **1 つの IRI に `#` が 3 つ並ぶ** — RFC 3986 では素片は 1 つだけなので
    **これは妥当な IRI ではない**。厳格な IRI 実装は文書ごと拒否する。
    `/` も同じ理由で符号化する(区切りとして使っているので、用語に含まれると
    段の数が変わって分解できなくなる)。

    符号化しておけば**基底の後はちょうど 3 段**になり、素片を持たない。
    そのことは `test_mapping_export.py` が機械的に検査している。

    なお用語 IRI 自体の妥当性は `validate_term_iri` が宣言時に検証している。
    rdflib は不正な IRI の直列化を例外で拒否するので、壊れた Turtle が
    出回ることはない。
    """
    return (
        f"{MAPPING_NODE_BASE}{quote(namespace, safe='')}"
        f"/{quote(source_term, safe='')}/{quote(target_term, safe='')}"
    )


def render_mappings(mappings: Sequence[TermMapping], *, exported_at: datetime) -> str:
    """マッピングを Turtle にする(ADR-0031 決定3)。

    **素の SKOS のトリプルと、記述ノードの両方を出す。**

    - 素のトリプル(`<source> skos:closeMatch <target>`)は**そのまま引ける**。
      ADR-0023 決定1 が述語を SKOS の 5 つに限ったのは論理的帰結を持たない
      からなので、載せても推論器が制約を流し込まない
    - 記述ノードは `reason` / `declared_by` / **終点の生死**を持つ。
      これが無いと「標準に写すために情報を落とす」ことになる
      (ADR-0026 決定4 と同じ判断)

    **`ont:targetStatus` は `unknown` でも必ず出す**(決定4)。省略すると
    「問題なし」と読まれる。

    Args:
        mappings: 書き出すマッピング。**終点の生死が埋まっているものを渡す** —
            既定の `unknown` のまま渡すと「調べていない」として出る。
        exported_at: 書き出した時刻。タイムゾーン付きで渡すこと。

    Returns:
        Turtle。
    """
    graph = Graph()
    graph.bind("skos", SKOS)
    graph.bind("ont", Namespace(MAPPING_ONT_NAMESPACE))
    graph.bind("rdfs", RDFS)
    ont = Namespace(MAPPING_ONT_NAMESPACE)

    bundle = URIRef(f"{MAPPING_NODE_BASE}export")
    graph.add((bundle, RDF.type, ont.MappingExport))
    graph.add((bundle, ont.mappingCount, Literal(len(mappings))))
    graph.add((bundle, ont.exportedAt, Literal(exported_at, datatype=XSD.dateTime)))

    for mapping in mappings:
        source = URIRef(mapping.source_term)
        target = URIRef(mapping.target_term)
        predicate = MappingPredicate(mapping.predicate)
        # **素の SKOS のトリプル。** これがこの書き出しの主目的である。
        graph.add((source, URIRef(predicate_iri(predicate)), target))

        node = URIRef(mapping_node_iri(mapping.namespace, mapping.source_term, mapping.target_term))
        graph.add((bundle, ont.includes, node))
        graph.add((node, RDF.type, ont.Mapping))
        graph.add((node, ont.source, source))
        graph.add((node, ont.target, target))
        graph.add((node, ont.predicate, URIRef(predicate_iri(predicate))))
        graph.add((node, ont.declaredIn, Literal(mapping.namespace)))
        graph.add((node, ont.declaredBy, Literal(mapping.declared_by)))
        graph.add((node, ont.declaredAt, Literal(mapping.declared_at, datatype=XSD.dateTime)))
        if mapping.reason:
            graph.add((node, RDFS.comment, Literal(mapping.reason)))
        graph.add((node, ont.reciprocal, Literal(mapping.reciprocal)))
        graph.add((node, ont.disputed, Literal(mapping.disputed)))
        if mapping.counterpart_predicate is not None:
            graph.add((node, ont.counterpartPredicate, Literal(mapping.counterpart_predicate)))

        # **終点の生死。`unknown` でも出す**(決定4)。
        graph.add((node, ont.targetStatus, Literal(mapping.target_status)))
        if mapping.target_successor is not None:
            graph.add((node, ont.targetSuccessor, URIRef(mapping.target_successor)))
        if mapping.target_status_note:
            graph.add((node, ont.targetStatusNote, Literal(mapping.target_status_note)))

    serialized = graph.serialize(format="turtle")
    return serialized if isinstance(serialized, str) else serialized.decode("utf-8")
