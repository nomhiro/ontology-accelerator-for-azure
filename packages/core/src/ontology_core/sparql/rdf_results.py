"""`CONSTRUCT` / `DESCRIBE` の結果の検査と正規化(ADR-0034、`P2A-14`)。

## 切り詰めずに断る

[ADR-0025](../../../../../docs/adr/0025-result-limit-enforcement.md) 決定5 は
**行数**について「エラーにしない」と決めた。**ここは意図的に違う判断をする。**
理由は 1 つで十分に強い。

> **RDF には封筒が無い。**

`SELECT` の結果は JSON の封筒(`head` / `results`)で、そこに「切り詰めた」
という情報を載せる場所がある(ADR-0025 決定4 はヘッダに、MCP は本文に載せた)。
**Turtle にはその場所が無い。** トリプルを足せば**利用者のグラフにこちらが
作った主張を混ぜる**ことになる。

ヘッダに載せる手はあるが、[ADR-0017](../../../../../docs/adr/0017-deprecation-lifecycle.md)
決定3 が「**エージェントはヘッダを見ない**」と結論している。つまり
**切り詰めた RDF は、切り詰めたと言えないまま完全な RDF として届く。**

RDF は単調なので、トリプルを落としても偽になる主張は生まれない
(部分集合はより少ないことを言うだけ)。**それでも切り詰めないのは、
「少なく言っている」ことを伝えられないからである。**

## 解析する費用を認める

トリプル数を数えるには解析が必要で、安く済む方法は無い(バイト数はトリプル数の
代理にならない — 接頭辞の使い方と IRI の長さで 10 倍変わる)。

ADR-0025 が既に受け入れている未解決事項と同じである(「ストアの応答全体は
一度メモリに載る。時間の上限が事実上の防波堤」)。**新しい未解決を作ったの
ではなく、既にある未解決が 1 つの経路に増えた。**
"""

from __future__ import annotations

from dataclasses import dataclass

from rdflib import Graph, URIRef

__all__ = [
    "RdfParseError",
    "RdfResult",
    "TripleLimitExceededError",
    "count_triples",
    "deprecated_iris_in_graph",
    "load_construct_result",
    "terms_with_prefix",
]


class TripleLimitExceededError(Exception):
    """トリプル数の上限を超えた(ADR-0034 決定4)。

    呼び出し元は **413 Payload Too Large** にする。**切り詰めない。**

    Attributes:
        triples: 実際のトリプル数。**返す** — 「多すぎます」だけでは
            エージェントがどれだけ絞ればよいか分からない。
        limit: 上限。
    """

    def __init__(self, *, triples: int, limit: int) -> None:
        super().__init__(
            f"結果が {triples} トリプルで上限 {limit} を超えています。"
            "**切り詰めて返しません** — RDF には「切り詰めた」と書く場所が"
            "無いため、不完全なグラフが完全なものとして届いてしまいます。"
            "条件を足して問い直してください"
        )
        self.triples = triples
        self.limit = limit


class RdfParseError(Exception):
    """ストアの応答を RDF として解析できなかった。

    呼び出し元は **502** にする。**空のグラフを返さない** — 「解析できな
    かった」を「該当なし」と混同すると、エージェントが誤った結論を出す。
    """


@dataclass(frozen=True)
class RdfResult:
    """`CONSTRUCT` / `DESCRIBE` の結果(ADR-0034 決定6)。

    Attributes:
        turtle: **解析し直した** Turtle。ストアが返した本文をそのまま
            転送しない — 上限の検査のためにどうせ解析するので、
            **形を 1 つに決める**ほうがよい(持ち込みストアが書き方を変えても
            この API の応答は変わらない)。空白ノードのラベルは保たれないが、
            `CONSTRUCT` の空白ノードのラベルはそもそも安定していない。
        triple_count: トリプル数。アクセスログに記録する(決定7)。
        iris: グラフに現れた IRI(主語・述語・目的語のすべて)。
            **ここで持つのは解析を 1 回に留めるためである**
            ([ADR-0038](../../../../../docs/adr/0038-rdf-deprecation-warning.md)
            決定3)。この経路は上限の検査とアクセスログの用語で既に 2 回
            解析していた。廃止の検査を足すと 3 回になる — rdflib の解析は
            CPU バウンドで、そのために別スレッドへ逃がしている。
    """

    turtle: str
    triple_count: int
    iris: frozenset[str]


def load_construct_result(body: str, *, limit: int) -> RdfResult:
    """ストアが返した Turtle を検査して正規化する(ADR-0034 決定4・6)。

    Args:
        body: ストアの応答(Turtle)。
        limit: トリプル数の上限。**1 以上。**

    Raises:
        RdfParseError: RDF として解析できないとき。**空のグラフを返さない。**
        TripleLimitExceededError: 上限を超えたとき。**切り詰めない。**
    """
    graph = Graph()
    try:
        graph.parse(data=body, format="turtle")
    except Exception as exc:  # rdflib は多様な例外を投げる
        raise RdfParseError(f"ストアの応答を RDF として解析できませんでした: {exc}") from exc

    count = len(graph)
    if count > limit:
        raise TripleLimitExceededError(triples=count, limit=limit)

    serialized = graph.serialize(format="turtle")
    return RdfResult(
        turtle=serialized if isinstance(serialized, str) else serialized.decode("utf-8"),
        triple_count=count,
        # **主語・述語・目的語のすべてを見る**(ADR-0038 決定4)。
        # **リテラルは IRI として扱わない**(決定5) — 文字列が偶然一致しても
        # 警告しない。
        iris=frozenset(
            str(node) for triple in graph for node in triple if isinstance(node, URIRef)
        ),
    )


def count_triples(turtle: str) -> int | None:
    """Turtle のトリプル数を数える。解析できなければ `None`。

    **`0` を返さない。** 「トリプルが 0 件」と「数えられなかった」は違う
    (このリポジトリが繰り返している区別)。呼び出し側は `None` のときに
    件数を出さないこと。

    MCP が使う(ADR-0034 決定8)。Core API 側は `load_construct_result` で
    上限も一緒に検査するのでこちらは使わない。
    """
    graph = Graph()
    try:
        graph.parse(data=turtle, format="turtle")
    except Exception:
        return None
    return len(graph)


def terms_with_prefix(iris: frozenset[str], *, base_iri: str) -> tuple[str, ...]:
    """IRI の集合からその名前空間の用語だけを返す(ADR-0034 決定7)。

    **解析しない。** `RdfResult.iris` を受け取るだけの純粋な絞り込みである
    (ADR-0038 決定3) — 同じ本文を 2 回解析しないため。

    **`base_iri` が空なら何も返さない。** 空の接頭辞で全 IRI を数えて
    しまわないようにする(`ontology_core.access.returned_terms` と同じ判断)。
    **この規則がこの関数の存在理由である** — 呼び出し側で
    `startswith(base_iri)` と書くと、空文字のときに全件一致する。
    """
    if not base_iri:
        return ()
    return tuple(sorted(iri for iri in iris if iri.startswith(base_iri)))


def deprecated_iris_in_graph(iris: frozenset[str], deprecated: frozenset[str]) -> tuple[str, ...]:
    """グラフに現れた廃止済み IRI を列挙する(ADR-0038 決定1)。

    **`base_iri` で絞らない**(決定6)。`deprecated` はそのデータセットに
    対する SPARQL で求めているので、そこに入るのは呼び出し元が既に読める
    用語だけである。絞ると**取り込んだ外部語彙の廃止を警告できなくなる**。

    `SELECT` 側(`ontology_core.deprecation.deprecated_iris_in_results`)とは
    入力の形が違うので別の関数にしてある(決定の「検討した代替案」)。
    **こちらは述語も見るので網羅的である**(決定4)。
    """
    return tuple(sorted(iris & deprecated))
