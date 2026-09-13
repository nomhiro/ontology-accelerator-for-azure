"""定義と実データの乖離検出(ADR-0047、`P3-05`)。

## 何を見つけるものなのか

承認したオントロジーは「こういう形のデータがある」という主張である。
その主張が**実データと食い違っていても、誰も気づかない**。

- クラスを定義したが、実データに 1 件も無い(定義が現実より広い)
- 必須(`sh:minCount ≥ 1`)にしたプロパティが、実データでは欠けている
  (定義が現実より厳しい)

どちらも SHACL 検証(`P2A-05`)では見つからない。あちらが検証するのは
**投入された TTL 自身**であり、スキーマだけの TTL には対象ノードが無いので
「適合」を返す(`shacl.py` の冒頭がそう書いている)。

## pyshacl を実データに当てない

**当てるにはデータをメモリに載せる必要がある。** それは
[ADR-0002](../../../../docs/adr/0002-triple-store-as-rebuildable-projection.md)
と [ADR-0046](../../../../docs/adr/0046-virtual-knowledge-graph.md) が
守っているもの(実データを実体化しない)を、検査のために壊すことになる。

**代わりに、形から SPARQL の探り(probe)を組み立てて仮想グラフに問う。**
返るのは「1 件以上あるか」と「欠けている件数」だけで、実データは
こちらに来ない。

**これは SHACL 検証ではない。** 見るのは 2 つだけである。

- **クラスに実データがあるか**: `ASK { ?s a C }`
- **必須プロパティが欠けている件数**:
  `SELECT (COUNT(DISTINCT ?s)) WHERE { ?s a C OPTIONAL { ?s p ?v } FILTER(!BOUND(?v)) }`

**`FILTER NOT EXISTS` では書けない。** Ontop 5.3.0 がサポートしていない
(実測。`OntopUnsupportedKGQueryException` で 500 になる)。

`sh:pattern` / `sh:datatype` / `sh:maxCount` / `sh:class` は**見ない**。
見ないことを名前と報告に書いておく — 「乖離が無い」を「SHACL に適合した」と
読まれてはいけない。

## 0 件には 3 つの意味がある

これがこのモジュールの設計の中心である。

| 何が起きたか | 意味 |
|---|---|
| マッピングがそのクラスを作っていない | **乖離ではない。** そもそも入口が無い |
| マッピングはあるのに 0 件 | **乖離である。** 定義に対応する実データが無い |
| 照会できなかった | **何も言えない。** 「乖離なし」ではない |

3 つを 1 つの `0` に潰すと、**マッピングの書き忘れも Ontop の停止も
「実データが無い」として報告される**。呼び出し側が対処を選べなくなる
([ADR-0030](../../../../docs/adr/0030-mapping-target-lifecycle.md) 決定2 と
同じ形の 4 状態にしてある)。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from rdflib import Graph, URIRef
from rdflib.term import Literal

__all__ = [
    "MAX_PROBES",
    "ClassDivergence",
    "DivergenceReport",
    "DivergenceStatus",
    "ProbeOutcome",
    "PropertyDivergence",
    "ShapeTarget",
    "boolean_of",
    "build_class_divergence",
    "class_probe",
    "count_of",
    "count_planned_probes",
    "missing_property_probe",
    "plan_probes",
    "shape_targets",
    "total_probes",
    "unmapped_report",
]

_SH = "http://www.w3.org/ns/shacl#"
SH_NODE_SHAPE = URIRef(f"{_SH}NodeShape")
SH_TARGET_CLASS = URIRef(f"{_SH}targetClass")
SH_PROPERTY = URIRef(f"{_SH}property")
SH_PATH = URIRef(f"{_SH}path")
SH_MIN_COUNT = URIRef(f"{_SH}minCount")

_RDF_TYPE = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")

#: 1 回の報告で投げる探りの上限(ADR-0047 決定5)。
#:
#: **上界が無いものに上限を置く。** 形の数と必須プロパティの数の積だけ探りが
#: 増え、その 1 本ずつが顧客 DB へのクエリになる。超えた分は
#: `unknown` + 理由「上限」で返す — **黙って「乖離なし」にしない**
#: (ADR-0016 決定5 と同じ形)。
MAX_PROBES = 100


class DivergenceStatus(StrEnum):
    """定義 1 件についての判定(ADR-0047 決定2)。

    **4 つを混ぜない。** 特に `unknown` は「調べていない / 調べられなかった」
    であって「乖離なし」ではない。
    """

    #: 実データが 1 件以上あった。
    MATCHED = "matched"
    #: **マッピングはあるのに 0 件だった。** これが乖離である。
    EMPTY = "empty"
    #: マッピングがこのクラス(またはプロパティ)を作っていない。
    #: **乖離ではない** — そもそも実データへの入口が無い。
    UNMAPPED = "unmapped"
    #: 調べていない / 調べられなかった。`note` に理由が入る。
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ShapeTarget:
    """1 つの `sh:NodeShape` から取り出した、探りに必要な情報。

    Attributes:
        shape: 形の IRI(空白ノードの形は取らない。決定4)。
        target_class: `sh:targetClass` の値。
        required_paths: `sh:minCount >= 1` を持つ property shape の `sh:path`。
            **`sh:minCount` が無い、または 0 のものは入らない** —
            任意のプロパティが欠けていることは乖離ではない。
    """

    shape: str
    target_class: str
    required_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class PropertyDivergence:
    """必須プロパティ 1 件についての判定。

    Attributes:
        path: プロパティの IRI。
        status: 4 状態。
        missing_count: そのプロパティが欠けているインスタンスの件数。
            **`None` は「数えていない」**(`0` は「測った 0」である)。
        note: `unknown` の理由。**空にしない** — 理由が無い `unknown` は
            「問題なし」と読まれる。
    """

    path: str
    status: DivergenceStatus
    missing_count: int | None = None
    note: str = ""


@dataclass(frozen=True)
class ClassDivergence:
    """クラス 1 件についての判定。"""

    shape: str
    target_class: str
    status: DivergenceStatus
    #: 必須プロパティごとの判定。**クラスが `matched` でなければ空である** —
    #: インスタンスが無いクラスで「プロパティが欠けている」と言っても意味がない。
    properties: tuple[PropertyDivergence, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class DivergenceReport:
    """1 ソースに対する乖離の報告。

    **`unknown` を「乖離なし」に丸めない。** 呼び出し側が
    「調べられなかったものがある」ことを見せられるように、数を分けて持つ。
    """

    classes: tuple[ClassDivergence, ...] = ()
    #: 上限に達して投げなかった探りの数。
    skipped_probes: int = 0
    #: 実際に投げた探りの数。
    issued_probes: int = 0
    #: 照合に使った承認済み版。
    version: str | None = None
    #: 照合に使ったマッピングの改訂。
    mapping_revision: int | None = None
    #: 形が 1 つも無かったか。**「乖離が無い」とは違う。**
    no_shapes: bool = False

    @property
    def diverged(self) -> tuple[ClassDivergence, ...]:
        """乖離しているクラス(`empty`、または欠けている必須プロパティがある)。"""
        found: list[ClassDivergence] = []
        for item in self.classes:
            if item.status is DivergenceStatus.EMPTY:
                found.append(item)
                continue
            if any(
                p.status is DivergenceStatus.EMPTY
                or (p.missing_count is not None and p.missing_count > 0)
                for p in item.properties
            ):
                found.append(item)
        return tuple(found)

    @property
    def unknown(self) -> tuple[ClassDivergence, ...]:
        """調べられなかったクラス。**`diverged` と足して全体にはならない。**"""
        return tuple(item for item in self.classes if item.status is DivergenceStatus.UNKNOWN)

    @property
    def conclusive(self) -> bool:
        """**すべて調べきれたか。**

        真でないときに「乖離なし」と言ってはいけない。
        """
        if self.skipped_probes:
            return False
        for item in self.classes:
            if item.status is DivergenceStatus.UNKNOWN:
                return False
            if any(p.status is DivergenceStatus.UNKNOWN for p in item.properties):
                return False
        return True


def shape_targets(turtle: str) -> tuple[ShapeTarget, ...]:
    """承認済み版の TTL から `sh:NodeShape` の対象を取り出す。

    **`sh:targetClass` を持つ形だけを見る**(決定4)。`sh:targetNode` や
    `sh:targetSubjectsOf` は、探りに使える「クラス」を与えないので扱わない。

    **空白ノードの形は取らない**(決定4)。報告に出す識別子が無く、
    運用者が「どの形の話か」を辿れない。

    Raises:
        ValueError: Turtle として解析できないとき。**空のタプルを返さない** —
            「形が無かった」と「読めなかった」を混ぜない。
    """
    graph = Graph()
    try:
        graph.parse(data=turtle, format="turtle")
    except Exception as exc:  # rdflib は解析の失敗を複数の型で投げる
        raise ValueError(f"Turtle として解析できません: {exc}") from exc

    targets: list[ShapeTarget] = []
    for shape in graph.subjects(_RDF_TYPE, SH_NODE_SHAPE):
        if not isinstance(shape, URIRef):
            # 空白ノードの形。**黙って飛ばさず、呼び出し側には見えない** —
            # 数えたいなら別の指標にする(`P3-03`)。ここでは探りを作れない。
            continue
        for target_class in graph.objects(shape, SH_TARGET_CLASS):
            if not isinstance(target_class, URIRef):
                # `sh:targetClass "C"`(リテラル)はどのノードにも当たらない。
                # **SHACL-SHACL が段階 1 で捕まえる**(`shacl.py`)ので、
                # ここでは探りを作らないだけにする。
                continue
            targets.append(
                ShapeTarget(
                    shape=str(shape),
                    target_class=str(target_class),
                    required_paths=_required_paths(graph, shape),
                )
            )
    return tuple(sorted(targets, key=lambda t: (t.target_class, t.shape)))


def _required_paths(graph: Graph, shape: URIRef) -> tuple[str, ...]:
    """`sh:minCount >= 1` を持つ property shape の `sh:path` を返す。"""
    paths: list[str] = []
    for property_shape in graph.objects(shape, SH_PROPERTY):
        minimum = None
        for value in graph.objects(property_shape, SH_MIN_COUNT):
            if isinstance(value, Literal):
                try:
                    minimum = int(value)
                except (TypeError, ValueError):
                    # `sh:minCount "いち"` のような型違いは SHACL-SHACL が
                    # 捕まえる(`shacl.py` の段階 1)。ここでは必須と見なさない。
                    minimum = None
        if minimum is None or minimum < 1:
            continue
        for path in graph.objects(property_shape, SH_PATH):
            if isinstance(path, URIRef):
                paths.append(str(path))
    return tuple(sorted(set(paths)))


def class_probe(target_class: str) -> str:
    """そのクラスに実データが 1 件以上あるかを問う `ASK`。

    **`ASK` を使う。** 件数は要らない — 要るのは「1 件以上あるか」だけで、
    全行を materialize すると桁でコストが変わる(ADR-0022 決定4 と同じ判断)。

    IRI は `<...>` で囲んで埋め込む。`validate_term_iri` が空白・制御文字・
    `<>` を弾いているので、ここで構文が壊れることはない。
    """
    return f"ASK {{ ?s a <{target_class}> }}"


def missing_property_probe(target_class: str, path: str) -> str:
    """必須プロパティが欠けているインスタンスの件数を問う `SELECT`。

    **件数を数える。** ここは「あるか」では足りない — 運用者が知りたいのは
    「何件が欠けているか」で、1 件の例外と全件の欠落は対処が違う。

    **`FILTER NOT EXISTS` を使ってはいけない。** Ontop 5.3.0 は
    これを**サポートしていない**(実測。
    `OntopUnsupportedKGQueryException: The expression Exists ... is not
    supported yet!` で HTTP 500 になる)。`OPTIONAL` + `!BOUND` で書く —
    **`MINUS` も動いたが**(同じ答えを返した)、`OPTIONAL` + `!BOUND` は
    SPARQL 1.0 の時代からある形で、持ち込みストア(ADR-0001 設計原則3)でも
    通る可能性がいちばん高い。

    **`LIMIT` を付けない。** 集約なので行は 1 行しか返らない
    (ADR-0025 決定1 が `LIMIT` の後付けを却下した理由と同じく、集約に
    `LIMIT` を足すと意味が変わる)。
    """
    return (
        f"SELECT (COUNT(DISTINCT ?s) AS ?missing) WHERE {{ "
        f"?s a <{target_class}> . "
        f"OPTIONAL {{ ?s <{path}> ?v }} "
        f"FILTER(!BOUND(?v)) }}"
    )


def boolean_of(payload: Mapping[str, object]) -> bool | None:
    """`ASK` の応答から真偽を取り出す。読めなければ `None`。

    **`Mapping` で受ける。`dict` ではない。** `dict[str, object]` は
    不変なので、`dict[str, dict[...]]` を渡せない(mypy strict が拒否する)。
    読むだけなので共変な `Mapping` が正しい。

    **`None` を `False` にしない。** 「答えが偽だった」と「応答を読めなかった」
    は違う。
    """
    value = payload.get("boolean")
    return value if isinstance(value, bool) else None


def count_of(payload: Mapping[str, object], variable: str = "missing") -> int | None:
    """集約の `SELECT` の応答から件数を取り出す。読めなければ `None`。

    **`None` を `0` にしない。** 「0 件だった」と「数えられなかった」は違う
    (ADR-0041 決定2 と同じ扱い)。
    """
    results = payload.get("results")
    if not isinstance(results, dict):
        return None
    bindings = results.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        return None
    row = bindings[0]
    if not isinstance(row, dict):
        return None
    cell = row.get(variable)
    if not isinstance(cell, dict):
        return None
    raw = cell.get("value")
    if not isinstance(raw, str):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def plan_probes(
    targets: Iterable[ShapeTarget],
    *,
    mapped_classes: Collection[str],
    mapped_predicates: Collection[str],
    limit: int = MAX_PROBES,
) -> tuple[tuple[ShapeTarget, tuple[str, ...]], ...]:
    """探りを投げる対象を決める(ADR-0047 決定3・5)。

    **マッピングが作っていないクラス・述語には探りを投げない。** 投げても
    0 件が返るだけで、それは「乖離」ではなく「入口が無い」である。
    判定は呼び出し側が `unmapped` として組み立てる。

    Returns:
        `(形, 探りを投げる必須プロパティ)` の並び。**上限を超えた分は
        含まれない** — 呼び出し側が `skipped` として数える。
    """
    plan: list[tuple[ShapeTarget, tuple[str, ...]]] = []
    budget = limit
    for target in targets:
        if target.target_class not in mapped_classes:
            continue
        if budget <= 0:
            break
        budget -= 1  # クラスの探り 1 本
        paths: list[str] = []
        for path in target.required_paths:
            if path not in mapped_predicates:
                continue
            if budget <= 0:
                break
            budget -= 1
            paths.append(path)
        plan.append((target, tuple(paths)))
    return tuple(plan)


def count_planned_probes(plan: Iterable[tuple[ShapeTarget, tuple[str, ...]]]) -> int:
    """計画した探りの本数(クラス 1 本 + プロパティの数)。"""
    return sum(1 + len(paths) for _, paths in plan)


def total_probes(
    targets: Iterable[ShapeTarget],
    *,
    mapped_classes: Collection[str],
    mapped_predicates: Collection[str],
) -> int:
    """上限を無視したときに必要な探りの本数。

    **`skipped` を数えるために必要である。** 「上限に達した」と言うだけでは、
    どれだけ見ていないのかが分からない。
    """
    total = 0
    for target in targets:
        if target.target_class not in mapped_classes:
            continue
        total += 1
        total += sum(1 for path in target.required_paths if path in mapped_predicates)
    return total


def unmapped_report(
    target: ShapeTarget, *, mapped_predicates: Collection[str], note: str
) -> ClassDivergence:
    """マッピングがそのクラスを作っていない場合の判定を組み立てる。

    **プロパティは見ない。** クラスの入口が無いのに「プロパティが欠けている」
    と言っても意味がない(`matched` でなければ `properties` は空、という
    `ClassDivergence` の約束と揃える)。
    """
    return ClassDivergence(
        shape=target.shape,
        target_class=target.target_class,
        status=DivergenceStatus.UNMAPPED,
        note=note,
    )


@dataclass(frozen=True)
class ProbeOutcome:
    """1 本の探りの結果。

    **`value` が `None` なら「読めなかった」である。** 呼び出し側は
    `unknown` に落とす。
    """

    value: bool | int | None
    note: str = ""
    failed: bool = False


def build_class_divergence(
    target: ShapeTarget,
    *,
    class_outcome: ProbeOutcome,
    property_outcomes: dict[str, ProbeOutcome] | None = None,
    unmapped_paths: tuple[str, ...] = (),
    unmapped_note: str = "",
) -> ClassDivergence:
    """探りの結果から 1 クラスの判定を組み立てる(ADR-0047 決定2)。

    **クラスが `matched` でなければプロパティを見ない。** インスタンスが
    無いクラスで「プロパティが欠けている」と言っても意味がないうえ、
    実装によって 0 が返ったり例外になったりして解釈が揺れる。
    """
    if class_outcome.failed or class_outcome.value is None:
        return ClassDivergence(
            shape=target.shape,
            target_class=target.target_class,
            status=DivergenceStatus.UNKNOWN,
            note=class_outcome.note or "クラスの探りの結果を読めませんでした",
        )
    if class_outcome.value is False:
        return ClassDivergence(
            shape=target.shape,
            target_class=target.target_class,
            status=DivergenceStatus.EMPTY,
            note="マッピングはありますが、実データが 1 件もありません",
        )

    outcomes = property_outcomes or {}
    properties: list[PropertyDivergence] = []
    for path in unmapped_paths:
        properties.append(
            PropertyDivergence(
                path=path,
                status=DivergenceStatus.UNMAPPED,
                note=unmapped_note or "マッピングがこの述語を作っていません",
            )
        )
    for path, outcome in sorted(outcomes.items()):
        if outcome.failed or not isinstance(outcome.value, int):
            properties.append(
                PropertyDivergence(
                    path=path,
                    status=DivergenceStatus.UNKNOWN,
                    note=outcome.note or "欠けている件数を数えられませんでした",
                )
            )
            continue
        properties.append(
            PropertyDivergence(
                path=path,
                status=DivergenceStatus.MATCHED if outcome.value == 0 else DivergenceStatus.EMPTY,
                missing_count=outcome.value,
            )
        )
    return ClassDivergence(
        shape=target.shape,
        target_class=target.target_class,
        status=DivergenceStatus.MATCHED,
        properties=tuple(sorted(properties, key=lambda p: p.path)),
    )
