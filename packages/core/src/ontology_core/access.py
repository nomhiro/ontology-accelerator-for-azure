"""コンテキストのアクセスログの材料(ADR-0018、`P2B-05`)。

ここは**純粋関数だけ**である。DB への書き込みは
`ontology_api.repositories.access` が行う。

## 「返した用語」はその名前空間が発行した IRI に限る

健全性指標が答えたいのは「**自分のオントロジーのどの用語が使われていないか**」
である(ADR-0009 決定5)。`rdf:type` や `owl:Class` の参照回数は指標にならないし、
外部語彙の IRI は自分が廃止を判断できる対象ではない。

**責任者(ADR-0015 決定3)とは逆の判断であり、理由も逆である。** 責任者は外部
IRI にも置けるようにした(外部語彙へのマッピングの妥当性について説明責任を
負うのは張った側だから)。アクセスの集約は自分の用語に限る(**使われていない
外部 IRI を「縮める」ことはできない**)。

副作用として、集約テーブルの行数に**その名前空間の用語数で上限が付く**
(エージェントの稼働に比例して増えない)。

## `GRAPH` 句の有無を記録する

`GRAPH` 句なしのクエリは承認済みの現行版だけを見る(ADR-0010 決定5)ので、
その版を記録すれば「どの版を返したか」の答えになる。明示したクエリは他の版を
読みうるが、どの版かを厳密に知るにはクエリの解析器が必要である。
**解析器を持ち込むより「不完全であることを記録する」ほうが正直である**
(ADR-0018 決定7)。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MAX_QUERY_TEXT",
    "AccessRecord",
    "QueryFingerprint",
    "build_access_record",
    "build_rdf_access_record",
    "query_fingerprint",
    "returned_terms",
    "uses_graph_clause",
]

#: `access_events.query_text` に保存するクエリの最大長。
#:
#: 全文は保存しない(上限 100,000 文字のクエリを受け付けるため)。
#: **代わりに全文のハッシュを持つ** — 切り詰めた文字列が一致しても元の
#: クエリが同じとは限らないので、同じクエリをまとめるにはハッシュが要る。
MAX_QUERY_TEXT = 2000

# `GRAPH` を**単語として**探す。`?graphName` や `<urn:paragraph>` を
# 誤検出すると、`GRAPH` 句なしのクエリの版の記録まで「不完全」と報告されて
# しまい、正しい記録が信用できなくなる。
_GRAPH_KEYWORD = re.compile(r"(?<![\w?$:])graph(?![\w])", re.IGNORECASE)


@dataclass(frozen=True)
class QueryFingerprint:
    """保存用に整えたクエリ。

    Attributes:
        text: 切り詰めたクエリ本文。
        hash: **全文**の SHA-256(hexdigest、64 文字)。
        truncated: 切り詰めたか。**黙って切らない**ための印。
    """

    text: str
    hash: str
    truncated: bool


@dataclass(frozen=True)
class AccessRecord:
    """1 回のクエリについて記録する内容(ADR-0018 決定1)。"""

    namespace: str
    actor: str
    query: QueryFingerprint
    default_graph_version: str | None
    used_graph_clause: bool
    # **行という概念が無いときは `None`**(ADR-0034 決定7 / ADR-0039 決定1)。
    # 「0 行返した」と「行という概念が無い」は違う。`CONSTRUCT` / `DESCRIBE` と
    # **`ASK`** がこれに当たる。読めない形のときも `None` である。
    returned_row_count: int | None = None
    # `CONSTRUCT` / `DESCRIBE` が返したトリプル数。`SELECT` / `ASK` では `None`。
    returned_triple_count: int | None = None
    terms: tuple[str, ...] = ()

    @property
    def returned_term_count(self) -> int:
        """返した用語(その名前空間の IRI)の件数。"""
        return len(self.terms)


def query_fingerprint(query: str) -> QueryFingerprint:
    """クエリを保存用に整える。"""
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
    truncated = len(query) > MAX_QUERY_TEXT
    return QueryFingerprint(
        text=query[:MAX_QUERY_TEXT] if truncated else query,
        hash=digest,
        truncated=truncated,
    )


def uses_graph_clause(query: str) -> bool:
    """クエリが `GRAPH` 句を含むか(ADR-0018 決定7)。

    真のとき、記録した版は「既定グラフの版」でしかない。
    """
    return _GRAPH_KEYWORD.search(query) is not None


def returned_terms(results: Any, *, base_iri: str) -> tuple[str, ...]:
    """結果に現れた**その名前空間の**用語 IRI を返す(ADR-0018 決定6)。

    **重複は 1 件にまとめる。** 参照回数はクエリ 1 回で 1 回である
    (行数を数えると `LIMIT` の違いで指標が動く)。

    **結果の形に前提を置かない。** ストアを差し替えられる設計(ADR-0001)なので、
    想定外の形でも例外にせず空を返す。**記録のために本来の応答を壊しては
    いけない。**
    """
    if not base_iri:
        # `base_iri` が無い名前空間で全 IRI を数えてしまわないようにする。
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
            if not isinstance(cell, dict) or cell.get("type") != "uri":
                continue
            value = cell.get("value")
            if isinstance(value, str) and value.startswith(base_iri):
                found.add(value)
    return tuple(sorted(found))


def _row_count(results: Any) -> int | None:
    """束縛の行数を返す。数えられなければ `None`(ADR-0039 決定1)。

    | 結果の形 | 返す値 |
    |---|---|
    | `results.bindings` が配列 | **その長さ**(空配列なら `0`。測った `0` である) |
    | `boolean` がある(`ASK`) | **`None`** |
    | 読めない形 | **`None`** |

    **`0` を作らない。** 「0 行返した」と「行という概念が無い」は違う
    (ADR-0034 決定7 が `CONSTRUCT` について決めたのと同じ規則)。

    **`ASK` を名前で分岐しない**(決定2)。上の表に `ASK` という語は要らない —
    「束縛が無い」で足りる。形で分岐すると、持ち込みストアが別の形を返した
    ときに再び `0` が生まれる([ADR-0001](../../../../docs/adr/0001-rdf-store-selection.md)
    が「SPARQL 1.1 Protocol をハード境界にする」と決めている)。

    **空の配列の `0` は残す。** 「`SELECT` が 0 行返した」は測った事実である。
    """
    if not isinstance(results, dict):
        return None
    section = results.get("results")
    if not isinstance(section, dict):
        return None
    bindings = section.get("bindings")
    return len(bindings) if isinstance(bindings, list) else None


def build_access_record(
    *,
    namespace: str,
    actor: str,
    query: str,
    results: Any,
    base_iri: str,
    default_graph_version: str | None,
) -> AccessRecord:
    """1 回のクエリからアクセスログの記録を組み立てる(`SELECT` / `ASK`)。

    `CONSTRUCT` / `DESCRIBE` は `build_rdf_access_record` を使う
    (行とトリプルを混ぜない。ADR-0034 決定7)。

    **`ASK` では `returned_row_count` が `None` になる**
    ([ADR-0039](../../../../docs/adr/0039-ask-row-count.md) 決定1)。
    `ASK` の結果に束縛は無いので、行数は「測った `0`」ではなく
    「行という概念が無い」である。**真偽値は保存しない**(決定3) —
    オントロジーは不変リビジョンなので、記録したクエリと版で再実行できる
    (ADR-0018 決定1 が用語の一覧を保存しなかったのと同じ理由)。
    """
    return AccessRecord(
        namespace=namespace,
        actor=actor,
        query=query_fingerprint(query),
        default_graph_version=default_graph_version,
        used_graph_clause=uses_graph_clause(query),
        returned_row_count=_row_count(results),
        terms=returned_terms(results, base_iri=base_iri),
    )


def build_rdf_access_record(
    *,
    namespace: str,
    actor: str,
    query: str,
    triple_count: int,
    terms: tuple[str, ...],
    default_graph_version: str | None,
) -> AccessRecord:
    """`CONSTRUCT` / `DESCRIBE` の記録を組み立てる(ADR-0034 決定7)。

    **`returned_row_count` は `None` のままにする。** 「0 行返した」と
    「行という概念が無い」は違う — `0` を書くと、アクセスログを読んだ人が
    「何も返さなかったクエリ」として数える。

    `terms` は呼び出し側が `ontology_core.sparql.rdf_results.terms_in_graph`
    で数えたものを渡す。グラフから数えるほうが `SELECT` の束縛より**むしろ
    正確**である(束縛に現れない IRI も拾える)。
    """
    return AccessRecord(
        namespace=namespace,
        actor=actor,
        query=query_fingerprint(query),
        default_graph_version=default_graph_version,
        used_graph_clause=uses_graph_clause(query),
        returned_row_count=None,
        returned_triple_count=triple_count,
        terms=terms,
    )
