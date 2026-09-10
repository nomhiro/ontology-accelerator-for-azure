"""オントロジーの意味的差分(ADR-0016、`P2B-09`)。

## 「意味的」とは何か

同じ内容の TTL が、接頭辞・トリプルの順序・**空白ノードのラベル**の違いだけで
バイト列として別物になる。意味的差分とは、少なくともこれらを差分として
報告しないことである。テキスト差分ではこれが守れない。

空白ノードの正規化は rdflib の `to_isomorphic` に任せる(ADR-0016 決定1)。
RDF Dataset Canonicalization の再実装は**微妙に間違えても静かに間違う**。

## 用語単位の `added` / `removed` は正規化なしで求める

**差分の最も重要な部分が最も安い**(ADR-0016 決定4)。`added` と `removed` は
「IRI が主語として現れるか」だけで決まり、空白ノードの同一性に依存しない。

これが重要なのは、正規化のコストが**空白ノードの数だけ**で決まり、しかも
急激に伸びるからである(実測。トリプル総数はほとんど効かない)。

| 空白ノード数 | 2 グラフの差分 |
|---|---|
| 100 | 0.14 秒 |
| 300 | 2.0〜4.5 秒 |
| 500 | 8.0 秒 |
| 1,000 | 42.1 秒 |

そのため上限を超えたらトリプル単位の差分は**計算しない**。「変更なし」と
「計算できなかった」を混同すると、IRI の削除という規律違反(ADR-0009 決定3)を
見落とす。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from rdflib import Graph, Literal, URIRef
from rdflib.compare import graph_diff
from rdflib.namespace import OWL
from rdflib.term import Node

from ontology_core.turtle import iri_subjects

__all__ = [
    "MAX_BLANK_NODES",
    "SUMMARY_MAX_TERMS",
    "DiffError",
    "OntologyDiff",
    "TripleDiffStatus",
    "diff_ontologies",
]

#: 1 グラフあたりの空白ノードの上限(ADR-0016 決定5)。
#:
#: **実測に基づく値である。** 2 グラフの差分の所要時間は空白ノード数で決まり、
#: トリプル総数はほとんど効かない。
#:
#: | 空白ノード | 差分の所要 |
#: |---|---|
#: | 100 | 0.14 秒 |
#: | 200 | 0.71 秒 |
#: | **300** | **2.0〜4.5 秒** |
#: | 400 | 5.9 秒 |
#: | 500 | 8.0 秒 |
#: | 800 | 25.6 秒 |
#: | 1,000 | 42.1 秒 |
#:
#: オントロジーの解析を 5 MB ≒ 5 秒で上限にしているのと同程度の予算に
#: 合わせて 300 にした。
MAX_BLANK_NODES = 300

#: 要約に載せる用語 IRI の既定の上限(ADR-0016 決定7)。
SUMMARY_MAX_TERMS = 50

_DEPRECATED_TRUE = Literal(True)


class DiffError(Exception):
    """差分を計算できなかったことを表す。

    **空の差分を返さない。** 「変更なし」と誤解させるため。
    """


class TripleDiffStatus(StrEnum):
    """トリプル単位の差分が得られたかどうか。"""

    EXACT = "exact"
    SKIPPED_TOO_MANY_BLANK_NODES = "skipped-too-many-blank-nodes"


@dataclass(frozen=True)
class OntologyDiff:
    """2 つの版の意味的差分(ADR-0016 決定3)。

    `added_terms` / `removed_terms` / `deprecated_terms` は**常に厳密**である
    (正規化に依存しない)。`modified_terms` と トリプル数は、空白ノードが
    上限を超えると `None` になる。
    """

    added_terms: tuple[str, ...]
    removed_terms: tuple[str, ...]
    deprecated_terms: tuple[str, ...]
    modified_terms: tuple[str, ...] | None
    added_triple_count: int | None
    removed_triple_count: int | None
    triple_status: TripleDiffStatus
    blank_node_count: int

    @property
    def has_removed_terms(self) -> bool:
        """IRI が削除されたか。

        **ADR-0009 決定3 の規律違反を示す唯一の信号である。** 廃止
        (`deprecated_terms`)は正しい縮め方なのでここに含めない。
        """
        return bool(self.removed_terms)

    @property
    def is_empty(self) -> bool:
        """差分が無いか。

        **判定はトリプル数で行う。** 用語の一覧はトリプル差分から導出した
        ものなので、そちらが原本である。用語の一覧だけで判定すると、
        **空白ノードの内部だけが変わった場合を「変更なし」と誤判定する**
        (実際にこの実装で一度そうなった)。

        **計算できなかった場合は「無い」とは言わない。** 上限を超えたときは
        トリプル数が `None` なので `False` になる。「変更なし」と
        「確かめられなかった」を混同しない。
        """
        if self.added_triple_count is None or self.removed_triple_count is None:
            return False
        return self.added_triple_count == 0 and self.removed_triple_count == 0

    def summary(self, *, max_terms: int = SUMMARY_MAX_TERMS) -> dict[str, Any]:
        """監査に保存する要約を返す(ADR-0016 決定7)。

        **全トリプルは載せない。** 版は Blob に不変で残るので(不変条件7)、
        厳密な差分はいつでも再計算できる。監査行に全トリプルを積むと行が
        非有界に育ち、監査そのものが読めなくなる。

        用語の一覧は `max_terms` 件で切り、**切ったことを `truncated` で
        明示する。黙って切らない。**
        """
        lists = {
            "added_terms": self.added_terms,
            "removed_terms": self.removed_terms,
            "deprecated_terms": self.deprecated_terms,
        }
        truncated = any(len(v) > max_terms for v in lists.values()) or (
            self.modified_terms is not None and len(self.modified_terms) > max_terms
        )
        result: dict[str, Any] = {
            "empty": self.is_empty,
            "triple_status": self.triple_status.value,
            "blank_node_count": self.blank_node_count,
            "has_removed_terms": self.has_removed_terms,
            "added_triple_count": self.added_triple_count,
            "removed_triple_count": self.removed_triple_count,
            "truncated": truncated,
        }
        for name, values in lists.items():
            result[name] = list(values[:max_terms])
            result[f"{name[:-1]}_count" if name.endswith("s") else f"{name}_count"] = len(values)
        if self.modified_terms is None:
            result["modified_terms"] = None
            result["modified_term_count"] = None
        else:
            result["modified_terms"] = list(self.modified_terms[:max_terms])
            result["modified_term_count"] = len(self.modified_terms)
        return result


def _parse(turtle: str, *, label: str) -> Graph:
    graph = Graph()
    if not turtle.strip():
        # 空のオントロジーは構文としては正当である。
        return graph
    try:
        graph.parse(data=turtle, format="turtle")
    except Exception as exc:
        # rdflib は多様な例外を投げるためここで一本化する。
        # **空の差分を返さない**(「変更なし」と誤解させる)。
        raise DiffError(f"{label} の Turtle を解析できません: {exc}") from exc
    return graph


def _iri_subjects(graph: Graph) -> set[str]:
    """主語として現れる IRI を返す。

    **判断は `ontology_core.turtle.iri_subjects` に置いてある。** 「用語とは
    IRI の主語である」という定義を、差分と健全性指標で二重に持たないため。
    """
    return iri_subjects(graph)


def _deprecated_subjects(graph: Graph) -> set[str]:
    """`owl:deprecated true` を持つ IRI を返す。

    **`owl:deprecated false` は廃止として数えない。**
    """
    return {
        str(s) for s in graph.subjects(OWL.deprecated, _DEPRECATED_TRUE) if isinstance(s, URIRef)
    }


def _owner_terms(graph: Graph, node: Node, *, seen: set[Node] | None = None) -> set[str]:
    """空白ノードを(推移的に)参照している IRI の主語を返す。

    **これが無いと、空白ノードの内部だけが変わった用語を検出できない。**
    `sh:property [ sh:minCount 1 ]` を `2` に変えたとき、差分に現れる
    トリプルの主語は**空白ノード**であって `ex:S` ではない。SHACL の制約は
    レビュアが最も見たい部分なので、ここを落とすと差分が役に立たない
    (実際にこの実装で一度落とした)。

    循環する空白ノードでも止まるよう `seen` で訪問済みを持つ。
    """
    if seen is None:
        seen = set()
    if node in seen:
        return set()
    seen.add(node)
    owners: set[str] = set()
    for subject in graph.subjects(None, node):
        if isinstance(subject, URIRef):
            owners.add(str(subject))
        else:
            owners |= _owner_terms(graph, subject, seen=seen)
    return owners


def _touched_terms(diff_graph: Graph, canonical: Graph) -> set[str]:
    """差分のトリプルが触っている用語の IRI を返す。

    主語が IRI ならそれ自身、空白ノードなら**それを参照している IRI**へ
    遡る(`_owner_terms`)。

    `canonical` は `graph_diff` が返した正規化後のグラフ(`in_both` と
    片側のみの和)である。**元のグラフではない** — `graph_diff` は空白ノードを
    正規化したラベル(`cb0` など)で返すので、元のグラフには存在しない
    (実測で確認)。
    """
    terms: set[str] = set()
    for subject in set(diff_graph.subjects()):
        if isinstance(subject, URIRef):
            terms.add(str(subject))
        else:
            terms |= _owner_terms(canonical, subject)
    return terms


def _union(*graphs: Graph) -> Graph:
    """トリプルを合併した新しいグラフを返す(正規化後のラベルを保つ)。"""
    merged = Graph()
    for graph in graphs:
        for triple in graph:
            merged.add(triple)
    return merged


def _blank_node_count(graph: Graph) -> int:
    """空白ノードの個数を返す。正規化コストを決める唯一の要因である(実測)。"""
    return len({n for n in graph.all_nodes() if not isinstance(n, URIRef | Literal)})


def diff_ontologies(base_turtle: str, new_turtle: str) -> OntologyDiff:
    """2 つの Turtle の意味的差分を返す。

    Args:
        base_turtle: 基準となる版(承認によって `superseded` になる版)。
        new_turtle: 新しい版。

    Raises:
        DiffError: どちらかが Turtle として解析できないとき。
    """
    base = _parse(base_turtle, label="基準の版")
    new = _parse(new_turtle, label="新しい版")

    base_terms = _iri_subjects(base)
    new_terms = _iri_subjects(new)
    added = tuple(sorted(new_terms - base_terms))
    removed = tuple(sorted(base_terms - new_terms))

    # **廃止は削除と分ける**(ADR-0016 決定3)。廃止は正しい縮め方、削除は
    # 規律違反である(ADR-0009 決定3)。新しい版で初めて `owl:deprecated true`
    # を得た IRI だけを廃止として数える。
    deprecated = tuple(sorted(_deprecated_subjects(new) - _deprecated_subjects(base)))

    blank_nodes = max(_blank_node_count(base), _blank_node_count(new))
    if blank_nodes > MAX_BLANK_NODES:
        # **トリプル単位は計算しない。** 空の一覧を返すと「変更なし」と
        # 混同される(ADR-0016 決定5)。`added` / `removed` は上で厳密に
        # 求めてあるので、そのまま返す。
        return OntologyDiff(
            added_terms=added,
            removed_terms=removed,
            deprecated_terms=deprecated,
            modified_terms=None,
            added_triple_count=None,
            removed_triple_count=None,
            triple_status=TripleDiffStatus.SKIPPED_TOO_MANY_BLANK_NODES,
            blank_node_count=blank_nodes,
        )

    # **`graph_diff` は内部で `to_canonical_graph` を呼ぶ**(rdflib 7.6.0 の実装を
    # 読んで確認。実測でも `to_isomorphic` を明示的に挟んでも差が出ない)。
    # 外から `to_isomorphic` を渡すのは冗長なので渡さない。
    in_both, only_base, only_new = graph_diff(base, new)

    # 変わった用語 = 差分のトリプルが触っている IRI のうち、追加も削除も
    # されていないもの。
    # **追加・削除と重複して数えない。** 「増えた」と「変わった」は別の事実である。
    #
    # 空白ノードの内部だけが変わった場合、差分のトリプルの主語は空白ノード
    # なので、**それを参照している IRI へ遡る**必要がある(`_touched_terms`)。
    # 遡る先は `graph_diff` が返した正規化後のグラフでなければならない。
    touched = _touched_terms(only_base, _union(in_both, only_base)) | _touched_terms(
        only_new, _union(in_both, only_new)
    )
    modified = tuple(sorted(touched & base_terms & new_terms))

    return OntologyDiff(
        added_terms=added,
        removed_terms=removed,
        deprecated_terms=deprecated,
        modified_terms=modified,
        added_triple_count=len(only_new),
        removed_triple_count=len(only_base),
        triple_status=TripleDiffStatus.EXACT,
        blank_node_count=blank_nodes,
    )
