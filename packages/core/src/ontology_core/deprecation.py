"""廃止のライフサイクル(ADR-0017、`P2B-03`)。

## 廃止は TTL に書く

`owl:deprecated true` と後継への参照(`dcterms:isReplacedBy`)は**オントロジーの
内容そのもの**である。「この用語はもう使うな、代わりにこれを使え」は定義に
関する主張であり、W3C の語彙がそれを表現するために存在する。

責任者(`ontology_core` に対応する `term_owners`。ADR-0015)を TTL に書か
なかったのと**逆の判断であり、理由も逆である**。責任者は現在の状態で、変わる
たびに新しい版を公開することになるので TTL には置けなかった。廃止は定義の
一部であり、廃止した事実が版に固定されるのは正しい(不変条件7)。

## 決定可能であることは、ブロックすべきであることを意味しない

ブロックが妥当なのは、規則が一義的で**かつ従う正当な手段が常にある**とき
だけである(ADR-0017 決定2)。

| 検査 | 扱い |
|---|---|
| **IRI の削除** | **ブロック**(従う手段: 削除せず廃止する) |
| **後継も理由も無い廃止** | **ブロック**(従う手段: どちらかを書く) |
| 生きている用語が廃止された用語を参照 | **報告のみ** |

最後をブロックしないのは、**SHACL の形状やマッピングが廃止された用語を
正当に参照する**からである。旧データを検証する形状、旧→新のマッピングは
どちらも書けなければならない。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, OWL, RDFS, SKOS

__all__ = [
    "DeprecationCheckError",
    "DeprecationProblem",
    "ProblemKind",
    "TargetStatus",
    "TermLifecycle",
    "check_deprecation",
    "deprecated_iris_in_results",
    "deprecated_terms",
    "has_blocking",
    "term_lifecycle",
]

_DEPRECATED_TRUE = Literal(True)

#: 後継を示す述語。
_SUCCESSOR_PREDICATES = (DCTERMS.isReplacedBy,)

#: 廃止の理由を示す述語。**後継の代わりになる。**
#: 後継が存在しない廃止は正当にある(間違って作った用語、統合されずに消える
#: 概念)ので、後継を必須にすると存在しない後継を捏造させることになる。
_REASON_PREDICATES = (RDFS.comment, SKOS.historyNote)

#: **歴史的参照**として問題に数えない述語(ADR-0017 決定2)。
#: 「昔これがあった」を記録するための述語であり、参照しているのが目的である。
_HISTORICAL_PREDICATES = frozenset({DCTERMS.replaces, RDFS.seeAlso})


class DeprecationCheckError(Exception):
    """検査を実行できなかったことを表す。

    **「問題なし」と返さない。** 「検証できなかった」を「適合」と混同すると、
    壊れた定義を承認してしまう(`P2A-05` と同じ判断)。
    """


class ProblemKind(StrEnum):
    """検出した問題の種類。"""

    REMOVED = "removed"
    NO_SUCCESSOR_OR_REASON = "no-successor-or-reason"
    REFERENCES_DEPRECATED = "references-deprecated"
    #: 廃止する用語へ**他の名前空間がマッピングを張っている**
    #: (ADR-0030 決定5)。**ブロックしない**(決定6)— ブロックすると、
    #: マッピングを張るだけで相手の廃止を止められる(人質になる)。
    MAPPED_BY_OTHERS = "mapped-by-others"


@dataclass(frozen=True)
class DeprecationProblem:
    """検出した問題 1 件。

    Attributes:
        blocking: 承認を止めるか。**決定可能性ではなく「従う手段が常にあるか」で
            決まる**(ADR-0017 決定2)。
        referenced: `REFERENCES_DEPRECATED` のときだけ、参照先の廃止済み IRI。
    """

    kind: ProblemKind
    term: str
    blocking: bool
    message: str
    referenced: str | None = None


def has_blocking(problems: list[DeprecationProblem]) -> bool:
    """承認を止めるべき問題が含まれるか。"""
    return any(p.blocking for p in problems)


def _parse(turtle: str, *, label: str) -> Graph:
    graph = Graph()
    if not turtle.strip():
        return graph
    try:
        graph.parse(data=turtle, format="turtle")
    except Exception as exc:
        raise DeprecationCheckError(f"{label} の Turtle を解析できません: {exc}") from exc
    return graph


def _deprecated(graph: Graph) -> frozenset[str]:
    return frozenset(
        str(s) for s in graph.subjects(OWL.deprecated, _DEPRECATED_TRUE) if isinstance(s, URIRef)
    )


def deprecated_terms(turtle: str) -> frozenset[str]:
    """`owl:deprecated true` を持つ IRI を返す。

    Raises:
        DeprecationCheckError: Turtle として解析できないとき。
    """
    return _deprecated(_parse(turtle, label="オントロジー"))


class TargetStatus(StrEnum):
    """マッピングの先の用語の生死(ADR-0030 決定2)。

    **4 つを混ぜない。** 特に `unknown` は「調べていない / 調べられなかった」
    であって「問題なし」ではない(ADR-0021 決定1 / ADR-0025 決定3 /
    ADR-0026 決定5 / ADR-0027 決定5 と同じ原則)。
    """

    DEPRECATED = "deprecated"
    ACTIVE = "active"
    #: 相手の現行版に**その IRI が無かった**。マッピングが何も指していない。
    #: **`active` に丸めない** — 直すべきものである。
    ABSENT = "absent"
    #: 調べていない / 調べられなかった。`note` に理由が入る。
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TermLifecycle:
    """ある IRI の生死(ADR-0030 決定2)。

    Attributes:
        status: 4 状態。
        successor: `dcterms:isReplacedBy` の値(`deprecated` のときだけ)。
            **後継を返さないと警告が使えない** — 呼び出し側はマッピングを
            張り替える先を知る必要がある。
        note: `unknown` の理由。**空にしない** — 理由が無い `unknown` は
            「問題なし」と読まれる。
    """

    status: TargetStatus
    successor: str | None = None
    note: str = ""


def term_lifecycle(turtle: str, iris: Collection[str]) -> dict[str, TermLifecycle]:
    """TTL の中での各 IRI の生死を返す(ADR-0030 決定2)。

    **`turtle` を 1 回だけ解析する。** IRI ごとに解析すると、対象が増えたときに
    使えなくなる。

    Args:
        turtle: 相手の名前空間の**現行の承認済み版**の TTL。
        iris: 調べる IRI。

    Returns:
        IRI から `TermLifecycle` への辞書。**`iris` の全件が入る。**

    Raises:
        DeprecationCheckError: Turtle として解析できないとき。**「生きている」と
            返さない** — 解析できなかったことを呼び出し側が `unknown` として
            扱えるようにする。
    """
    graph = _parse(turtle, label="マッピング先のオントロジー")
    deprecated = _deprecated(graph)
    present = {str(s) for s in graph.subjects() if isinstance(s, URIRef)}
    result: dict[str, TermLifecycle] = {}
    for iri in iris:
        if iri in deprecated:
            successors = [
                str(o)
                for o in graph.objects(URIRef(iri), DCTERMS.isReplacedBy)
                if isinstance(o, URIRef)
            ]
            result[iri] = TermLifecycle(
                status=TargetStatus.DEPRECATED,
                successor=sorted(successors)[0] if successors else None,
            )
        elif iri in present:
            result[iri] = TermLifecycle(status=TargetStatus.ACTIVE)
        else:
            # **`unknown` にしない。** 相手の現行版を読んだ結果として
            # 「無い」と分かったことと、調べていないことは違う。
            result[iri] = TermLifecycle(status=TargetStatus.ABSENT)
    return result


def _has_any(graph: Graph, subject: URIRef, predicates: tuple[URIRef, ...]) -> bool:
    """いずれかの述語で**中身のある**値を持つか。

    **空文字列や空白だけの値を「ある」と数えない。** 空の `rdfs:comment` で
    検査をすり抜けられてはいけない。
    """
    for predicate in predicates:
        for value in graph.objects(subject, predicate):
            if isinstance(value, Literal) and not str(value).strip():
                continue
            return True
    return False


def check_deprecation(turtle: str, *, base_turtle: str | None = None) -> list[DeprecationProblem]:
    """廃止に関する問題を列挙する(ADR-0017 決定2)。

    Args:
        turtle: 検査対象の版。
        base_turtle: 基準の版(その承認によって `superseded` になる版)。
            **`None` なら削除の検査を行わない** — 最初の承認では基準が無い。

    Raises:
        DeprecationCheckError: どちらかが Turtle として解析できないとき。
    """
    graph = _parse(turtle, label="オントロジー")
    deprecated = _deprecated(graph)
    problems: list[DeprecationProblem] = []

    # ---- 1) IRI の削除(ブロック) ----
    if base_turtle is not None:
        base = _parse(base_turtle, label="基準の版")
        base_terms = {str(s) for s in base.subjects() if isinstance(s, URIRef)}
        new_terms = {str(s) for s in graph.subjects() if isinstance(s, URIRef)}
        for iri in sorted(base_terms - new_terms):
            problems.append(
                DeprecationProblem(
                    kind=ProblemKind.REMOVED,
                    term=iri,
                    blocking=True,
                    message=(
                        f"'{iri}' が削除されています。**IRI を削除も再利用も"
                        "してはいけません**(ADR-0009 決定3)。削除ではなく "
                        "`owl:deprecated true` を立てて残してください"
                    ),
                )
            )

    # ---- 2) 後継も理由も無い廃止(ブロック) ----
    for iri in sorted(deprecated):
        subject = URIRef(iri)
        if _has_any(graph, subject, _SUCCESSOR_PREDICATES):
            continue
        if _has_any(graph, subject, _REASON_PREDICATES):
            continue
        problems.append(
            DeprecationProblem(
                kind=ProblemKind.NO_SUCCESSOR_OR_REASON,
                term=iri,
                blocking=True,
                message=(
                    f"'{iri}' は廃止されていますが、後継"
                    "(`dcterms:isReplacedBy`)も理由(`rdfs:comment` / "
                    "`skos:historyNote`)もありません。「使うな」だけでは、"
                    "利用者は代わりに何を使えばよいか分かりません"
                ),
            )
        )

    # ---- 3) 生きている用語からの参照(報告のみ) ----
    #
    # **ブロックしない。** SHACL の形状やマッピングが廃止された用語を正当に
    # 参照する(旧データを検証する形状、旧→新のマッピング)。ブロックすると
    # 移行のための記述が書けなくなる。
    references: set[tuple[str, str]] = set()
    for iri in deprecated:
        for referrer, predicate in graph.subject_predicates(URIRef(iri)):
            if predicate in _HISTORICAL_PREDICATES:
                continue
            if not isinstance(referrer, URIRef):
                continue
            source = str(referrer)
            # 廃止された用語自身についての記述、および廃止された用語からの
            # 参照は「生きている用語からの参照」ではない。
            if source == iri or source in deprecated:
                continue
            references.add((source, iri))
    for source, target in sorted(references):
        problems.append(
            DeprecationProblem(
                kind=ProblemKind.REFERENCES_DEPRECATED,
                term=source,
                blocking=False,
                message=(
                    f"'{source}' が廃止済みの '{target}' を参照しています。"
                    "移行のための記述(SHACL の形状・旧→新のマッピング)なら"
                    "意図どおりです。そうでなければ後継へ張り替えてください"
                ),
                referenced=target,
            )
        )

    return problems


def deprecated_iris_in_results(results: Any, deprecated: frozenset[str]) -> tuple[str, ...]:
    """SPARQL Results JSON に現れた廃止済み IRI を列挙する(ADR-0017 決定3)。

    **結果の形に過度な前提を置かない。** ストアを差し替えられる設計
    (ADR-0001)なので、想定外の形でも例外にせず空を返す。**警告のために
    本来の応答を壊してはいけない。**

    リテラルは IRI として扱わない(`type` が `uri` のものだけを見る)。
    文字列が偶然一致しても警告を出さない。
    """
    if not deprecated:
        return ()
    if not isinstance(results, dict):
        return ()
    section = results.get("results")
    if not isinstance(section, dict):
        return ()
    bindings = section.get("bindings")
    if not isinstance(bindings, list):
        return ()

    found: set[str] = set()
    for row in bindings:
        if not isinstance(row, dict):
            continue
        for cell in row.values():
            if not isinstance(cell, dict):
                continue
            if cell.get("type") != "uri":
                continue
            value = cell.get("value")
            if isinstance(value, str) and value in deprecated:
                found.add(value)
    return tuple(sorted(found))
