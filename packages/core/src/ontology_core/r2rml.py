"""R2RML マッピングの検証(ADR-0046、`P3-01`)。

## 何のためにあるか

R2RML は「リレーショナルの行を RDF のトリプルに読み替える規則」である。
Ontop はこれを受け取り、**SPARQL を SQL に書き換えて実データへ流す**
(実体化しない。[ADR-0002](../../../../docs/adr/0002-triple-store-as-rebuildable-projection.md)
が成立する前提)。

つまりマッピングは**実データへの入口の定義**であり、書き手が
次の 2 つを自由に決められる。

1. **どの関係を読むか**(`rr:tableName` / `rr:sqlQuery`)
2. **どんな IRI を発行するか**(`rr:template` / `rr:constant`)

1 は「読んでよいデータの範囲」、2 は「誰の名前で主張するか」である。
後者は**不変条件5(名前空間名はセキュリティ境界)に直接触る** — 名前空間 A の
マッピングが名前空間 B の IRI を主語にできるなら、A は B の用語について
B が宣言していない事実を作れる。

## ここは境界ではない。境界は DB の権限である

**このモジュールで SQL の安全性を判定しない。** そのために
`rr:sqlQuery` を**受け付けない**(ADR-0046 決定4)。

`ontology_core.scan.referenced_relations` は正規表現であり、自身の
docstring が「完全な SQL パーサではない」と書いている。固定の
`CATALOG_QUERIES` に対する検査にしか使えないものを任意の SQL に向けると、
**境界があるように見えて無い**状態を作る。それはこの製品がいちばん
避けたい形である(不変条件11 と同じ論点)。

**実データに対する本当の境界は、接続する DB ユーザの権限である。**
Ontop はそのユーザに見える関係をすべて見る(実測: 不正なマッピングの
エラーが `information_schema` 相当の全関係を列挙した)。だから運用の
手順として最小権限のユーザを使う(`P1-11` と同じ向き)。

ここでできるのは、**取り違えを入口で捕まえる**ことだけである。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rdflib import Graph, URIRef
from rdflib.term import BNode, Literal, Node

__all__ = [
    "MAX_MAPPING_LENGTH",
    "R2rmlError",
    "R2rmlMapping",
    "data_iri_prefix",
    "parse_mapping",
]

#: マッピング TTL の上限。**オントロジーの TTL より小さくしてある** —
#: マッピングは 1 ソースあたりの規則であって、語彙の集合ではない。
#: 超えるほど大きいマッピングは、ソースを分けるべき兆候である。
MAX_MAPPING_LENGTH = 256 * 1024

_RR = "http://www.w3.org/ns/r2rml#"

RR_TRIPLES_MAP = URIRef(f"{_RR}TriplesMap")
RR_LOGICAL_TABLE = URIRef(f"{_RR}logicalTable")
RR_TABLE_NAME = URIRef(f"{_RR}tableName")
RR_SQL_QUERY = URIRef(f"{_RR}sqlQuery")
RR_SUBJECT_MAP = URIRef(f"{_RR}subjectMap")
RR_SUBJECT = URIRef(f"{_RR}subject")
RR_TEMPLATE = URIRef(f"{_RR}template")
RR_CONSTANT = URIRef(f"{_RR}constant")
RR_CLASS = URIRef(f"{_RR}class")
RR_TERM_TYPE = URIRef(f"{_RR}termType")
RR_BLANK_NODE = URIRef(f"{_RR}BlankNode")
RR_PREDICATE_OBJECT_MAP = URIRef(f"{_RR}predicateObjectMap")
RR_PREDICATE = URIRef(f"{_RR}predicate")
RR_PREDICATE_MAP = URIRef(f"{_RR}predicateMap")
RR_GRAPH = URIRef(f"{_RR}graph")
RR_GRAPH_MAP = URIRef(f"{_RR}graphMap")
RR_PARENT_TRIPLES_MAP = URIRef(f"{_RR}parentTriplesMap")

_RDF_TYPE = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")

#: `rr:template` の中の `{列名}`。IRI の接頭辞を取り出すために使う。
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")

#: `"schema"."table"` のような R2RML の引用付き表名。
_QUOTED_PART = re.compile(r'"([^"]*)"')


class R2rmlError(ValueError):
    """R2RML マッピングとして受け付けられないことを表す。"""


@dataclass(frozen=True)
class R2rmlMapping:
    """解析した R2RML マッピングの要約。

    **「無かった」と「読めなかった」を分けるために、解析の結果は
    すべて明示的に持つ。** 空のタプルは「1 つも無かった」であり、
    解析に失敗したときは `R2rmlError` が飛ぶ(タプルが空にはならない)。
    """

    #: `rr:TriplesMap` の個数。
    triples_maps: int
    #: 参照している関係名。`"schema"."table"` は `schema.table` に正規化する。
    tables: tuple[str, ...] = field(default=())
    #: 主語として発行される IRI の接頭辞(`{...}` より前の部分)。
    subject_prefixes: tuple[str, ...] = field(default=())
    #: `rr:class` で付けるクラスの IRI。
    classes: tuple[str, ...] = field(default=())
    #: `rr:predicate` で使う述語の IRI。
    predicates: tuple[str, ...] = field(default=())

    @property
    def asserted_iris(self) -> tuple[str, ...]:
        """このマッピングが**自分の語彙として使う** IRI(クラスと述語)。

        主語の接頭辞は含めない。**主語とクラス・述語では守る規則が違う**
        (ADR-0046 決定5・6) — 主語は名前空間のデータ接頭辞の下に閉じ込め、
        クラスと述語は外部語彙を許す。
        """
        return (*self.classes, *self.predicates)


def data_iri_prefix(base_iri: str) -> str:
    """名前空間の `base_iri` から、インスタンスの IRI の接頭辞を導く。

    `https://example.com/ontology/retail#` → `https://example.com/ontology/retail/data/`

    **新しい列を作らない。** `base_iri` から決まるようにしておけば、
    「データの接頭辞だけ別の名前空間を指している」状態を作れない。

    **用語の IRI とは重ならない。** 用語は `base_iri` の直下
    (`...retail#Customer`)、インスタンスは `.../data/...` に入る。
    ただし `base_iri` がスラッシュ区切り(`.../retail/`)のときは、
    `.../retail/data` という名前の**用語**を作れば重なりうる。
    重なっても境界は破れない — どちらも同じ名前空間の下である。
    """
    return f"{base_iri.rstrip('#/')}/data/"


def _iri_prefix_of_template(template: str) -> str:
    """`rr:template` の、最初の `{...}` より前の部分を返す。

    `https://ex.org/data/customer/{id}` → `https://ex.org/data/customer/`

    **`{...}` が無ければテンプレート全体が接頭辞である**(全行が同じ
    主語になる異常なマッピングだが、解析としては接頭辞が取れる)。
    """
    match = _PLACEHOLDER.search(template)
    return template if match is None else template[: match.start()]


def _normalize_table_name(raw: str) -> str:
    """R2RML の `rr:tableName` を `schema.table` に正規化する。

    R2RML は SQL の識別子をそのまま書く形式なので、引用付き
    (`"public"."order"`)と引用なし(`public.order`)の両方が来る。
    **引用を外して比較できる形に揃える** — 揃えないと、スキャンが
    観測した表名と突き合わせられない。

    **大文字小文字は変えない。** PostgreSQL では引用付きの識別子が
    大文字小文字を保持するため、畳み込むと別の表を同じ表と見なす。
    """
    parts = _QUOTED_PART.findall(raw)
    if parts:
        return ".".join(parts)
    return raw.strip()


def _text_of(graph: Graph, subject: Node, predicate: URIRef) -> str | None:
    """1 つの述語の値をリテラル文字列として取り出す。複数あれば最初の 1 つ。"""
    for value in graph.objects(subject, predicate):
        if isinstance(value, Literal):
            return str(value)
    return None


def _collect_subject_prefixes(graph: Graph, triples_map: Node) -> list[str]:
    """1 つの TriplesMap が発行する主語 IRI の接頭辞を集める。

    Raises:
        R2rmlError: 主語が空白ノードになる、または主語の指定が無いとき。
    """
    prefixes: list[str] = []
    has_subject_definition = False

    # `rr:subject`(短縮形)は定数の IRI である。
    for value in graph.objects(triples_map, RR_SUBJECT):
        has_subject_definition = True
        if isinstance(value, URIRef):
            prefixes.append(str(value))

    for subject_map in graph.objects(triples_map, RR_SUBJECT_MAP):
        has_subject_definition = True
        # **空白ノードの主語を受け付けない**(ADR-0046 決定7)。
        # 同じ行を 2 回引いても同じ空白ノードになる保証が無く、
        # エージェントが参照を持ち回せない。
        if (subject_map, RR_TERM_TYPE, RR_BLANK_NODE) in graph:
            raise R2rmlError(
                "主語に空白ノード(rr:BlankNode)を使うマッピングは受け付けません。"
                "行を引き直したときに同じ主語になる保証が無く、参照を持ち回せません"
            )
        template = _text_of(graph, subject_map, RR_TEMPLATE)
        if template is not None:
            prefixes.append(_iri_prefix_of_template(template))
            continue
        for constant in graph.objects(subject_map, RR_CONSTANT):
            if isinstance(constant, URIRef):
                prefixes.append(str(constant))

    if not has_subject_definition:
        raise R2rmlError(
            "主語の定義(rr:subjectMap または rr:subject)が無い rr:TriplesMap があります"
        )
    if not prefixes:
        raise R2rmlError(
            "主語の IRI を決められない rr:TriplesMap があります。"
            "rr:template か rr:constant で IRI を指定してください"
        )
    return prefixes


def _collect_tables(graph: Graph, triples_map: Node) -> list[str]:
    """1 つの TriplesMap が読む関係名を集める。

    Raises:
        R2rmlError: `rr:sqlQuery` を使っている、または表名が無いとき。
    """
    tables: list[str] = []
    for logical_table in graph.objects(triples_map, RR_LOGICAL_TABLE):
        # **`rr:sqlQuery` を受け付けない**(ADR-0046 決定4)。
        # 任意の SQL を安全と判定する手段をこちらは持っていない。
        if _text_of(graph, logical_table, RR_SQL_QUERY) is not None:
            raise R2rmlError(
                "rr:sqlQuery は受け付けません。任意の SQL が安全かを判定する手段が"
                "無いため、rr:tableName で表を指定してください"
            )
        raw = _text_of(graph, logical_table, RR_TABLE_NAME)
        if raw is None:
            raise R2rmlError("rr:logicalTable に rr:tableName がありません")
        normalized = _normalize_table_name(raw)
        if not normalized:
            raise R2rmlError("rr:tableName が空です")
        tables.append(normalized)

    if not tables:
        raise R2rmlError("rr:logicalTable が無い rr:TriplesMap があります")
    return tables


def _collect_vocabulary(graph: Graph, triples_map: Node) -> tuple[list[str], list[str]]:
    """1 つの TriplesMap が使うクラスと述語の IRI を集める。

    Raises:
        R2rmlError: `rr:predicateMap` の述語が動的(テンプレート)なとき。
    """
    classes: list[str] = []
    for subject_map in graph.objects(triples_map, RR_SUBJECT_MAP):
        classes.extend(
            str(value)
            for value in graph.objects(subject_map, RR_CLASS)
            if isinstance(value, URIRef)
        )

    predicates: list[str] = []
    for pom in graph.objects(triples_map, RR_PREDICATE_OBJECT_MAP):
        found = False
        for value in graph.objects(pom, RR_PREDICATE):
            if isinstance(value, URIRef):
                predicates.append(str(value))
                found = True
        for predicate_map in graph.objects(pom, RR_PREDICATE_MAP):
            for constant in graph.objects(predicate_map, RR_CONSTANT):
                if isinstance(constant, URIRef):
                    predicates.append(str(constant))
                    found = True
            # **述語をデータから作らせない**(ADR-0046 決定6)。
            # 列の値が述語になると、**どの述語が現れるかが実データ次第**に
            # なり、マッピングを読んでも語彙が分からない。承認済み版に
            # 実在するかの検査も、原理的にできなくなる。
            if _text_of(graph, predicate_map, RR_TEMPLATE) is not None:
                raise R2rmlError(
                    "述語を rr:template で組み立てるマッピングは受け付けません。"
                    "どの述語が現れるかが実データ次第になり、語彙を検査できません"
                )
        if not found:
            raise R2rmlError(
                "述語の定義(rr:predicate または rr:predicateMap の rr:constant)が"
                "無い rr:predicateObjectMap があります"
            )
    return classes, predicates


def parse_mapping(turtle: str) -> R2rmlMapping:
    """R2RML マッピングを解析して要約を返す。

    **名前空間との突き合わせはここでしない。** `base_iri` や承認済み版の
    用語を知っているのはサービス層なので、ここは**マッピング単体として
    成立しているか**だけを見る(`ontology_core.shacl` と
    `ProjectionService` の分担と同じ形)。

    Raises:
        R2rmlError: 構文が壊れている、`rr:TriplesMap` が無い、または
            受け付けない構成(`rr:sqlQuery`、空白ノードの主語、
            動的な述語、`rr:graph`)を含むとき。
    """
    if not turtle.strip():
        raise R2rmlError("マッピングが空です")
    if len(turtle) > MAX_MAPPING_LENGTH:
        raise R2rmlError(
            f"マッピングが大きすぎます({len(turtle)} 文字 > {MAX_MAPPING_LENGTH} 文字)。"
            "1 つのソースの規則としては大きすぎるので、ソースを分けてください"
        )

    graph = Graph()
    try:
        graph.parse(data=turtle, format="turtle")
    except Exception as exc:  # rdflib は解析の失敗を複数の型で投げる
        raise R2rmlError(f"Turtle として解析できません: {exc}") from exc

    # **`rr:graph` / `rr:graphMap` を受け付けない**(ADR-0046 決定8)。
    # 仮想グラフは既定グラフに置く。名前付きグラフの IRI を書き手が
    # 決められると、版のグラフ IRI(`urn:ontology:graph/<ns>/<version>`)を
    # 名乗るマッピングを作れる。
    for predicate in (RR_GRAPH, RR_GRAPH_MAP):
        if any(graph.triples((None, predicate, None))):
            raise R2rmlError(
                "rr:graph / rr:graphMap は受け付けません。仮想グラフは既定グラフに置きます"
            )

    # **`rr:parentTriplesMap` を受け付けない**(ADR-0046 決定9)。
    # 参照結合はもう 1 つの TriplesMap の主語を借りる形なので、
    # 借りた先の検査結果に依存する。段階を増やさず、`rr:template` で
    # 相手の IRI を組み立てる形に寄せる(実測でその形が動いている)。
    if any(graph.triples((None, RR_PARENT_TRIPLES_MAP, None))):
        raise R2rmlError(
            "rr:parentTriplesMap は受け付けません。参照先の IRI は rr:template で組み立ててください"
        )

    # `rr:TriplesMap` は型宣言が省略できる形式なので、**型で探さない**。
    # `rr:logicalTable` を持つ主語を TriplesMap とみなす(R2RML 仕様が
    # 要求する必須プロパティ)。型宣言だけあって中身が無いものは
    # `_collect_tables` が捕まえる。
    declared = {
        subject
        for subject in graph.subjects(_RDF_TYPE, RR_TRIPLES_MAP)
        if isinstance(subject, URIRef | BNode)
    }
    with_table = {
        subject
        for subject in graph.subjects(RR_LOGICAL_TABLE, None)
        if isinstance(subject, URIRef | BNode)
    }
    triples_maps = sorted(declared | with_table, key=str)
    if not triples_maps:
        raise R2rmlError("rr:TriplesMap が 1 つもありません")

    tables: list[str] = []
    subject_prefixes: list[str] = []
    classes: list[str] = []
    predicates: list[str] = []
    for triples_map in triples_maps:
        tables.extend(_collect_tables(graph, triples_map))
        subject_prefixes.extend(_collect_subject_prefixes(graph, triples_map))
        found_classes, found_predicates = _collect_vocabulary(graph, triples_map)
        classes.extend(found_classes)
        predicates.extend(found_predicates)

    return R2rmlMapping(
        triples_maps=len(triples_maps),
        tables=tuple(sorted(set(tables))),
        subject_prefixes=tuple(sorted(set(subject_prefixes))),
        classes=tuple(sorted(set(classes))),
        predicates=tuple(sorted(set(predicates))),
    )
