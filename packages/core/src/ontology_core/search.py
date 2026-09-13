"""用語の検索と、2 つの経路の融合(ADR-0050、`P3-02`)。

## 2 つの経路を持つ理由

| 経路 | 何に強いか | 実装 |
|---|---|---|
| **ベクトル** | 言い換え・概念の近さ(「お客さん」→ `Customer`) | pgvector の `<=>`(cosine) |
| **3-gram** | 表記の一致・部分一致(「顧客ID」→ `customerId`) | `pg_trgm` の `similarity()` |

**日本語の形態素解析は使えない。** `pg_bigm` / `pgroonga` は Azure Database
for PostgreSQL の対応拡張一覧に入っていない(PG 16 の一覧で確認)。
つまり **BM25 相当のキーワード検索は作れない**。3-gram で代用する。

**これは Azure AI Search との唯一の実質的な差である**(あちらには
`ja.microsoft` / `ja.lucene` のアナライザがある)。用語のラベルは短い
文字列なので 3-gram で実用上足りると見ているが、**長文の文書検索には
弱い**ことを記録しておく。

## どちらで当たったかを必ず返す

融合しただけの順位を返すと、**なぜその用語が出てきたのかが分からない**。
この製品は「説明可能」を掲げているので、**経路と順位を両方返す**。

## 「埋め込みが無い」を「該当なし」にしない

埋め込みを作っていない名前空間で検索すると、ベクトルの経路は 0 件になる。
それを「該当する用語が無い」として返すと、**運用者は埋め込みの作成漏れに
気づけない**。`SearchOutcome.vector_available` で区別する。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "RRF_K",
    "RankedRow",
    "SearchHit",
    "SearchOutcome",
    "SearchRoute",
    "clamp_limit",
    "fuse",
    "route_summary",
]

#: 1 回の検索で返す件数の既定と上限。
#:
#: **上限を置く。** エージェントに渡す文脈の量を境界で決める
#: (ADR-0025 と同じ向き)。
DEFAULT_LIMIT = 10
MAX_LIMIT = 50

#: RRF(Reciprocal Rank Fusion)の定数。
#:
#: **60 は原論文の値である**(Cormack ら 2009)。**調整していない** —
#: 調整するなら「何に対して良くなったか」を測る必要があり、
#: 測っていないものを調整したと書けない。
RRF_K = 60


class SearchRoute(StrEnum):
    """その用語がどちらの経路で当たったか。

    **`both` を別の値にする。** 「両方で当たった」は「片方で当たった」より
    強い根拠であり、潰すとその情報が消える。
    """

    VECTOR = "vector"
    TRIGRAM = "trigram"
    BOTH = "both"


@dataclass(frozen=True)
class SearchHit:
    """検索で当たった 1 件。

    Attributes:
        term_iri: 用語の IRI。
        route: どちらの経路で当たったか。
        score: 融合後のスコア(RRF)。**大きいほど上位。**
        vector_rank: ベクトルの経路での順位(1 始まり)。当たらなければ `None`。
        trigram_rank: 3-gram の経路での順位。当たらなければ `None`。
        vector_similarity: cosine の類似度(0〜1)。**`None` は「その経路で
            当たらなかった」**であって「似ていない」ではない。
        trigram_similarity: 3-gram の類似度(0〜1)。同上。
        source_text: 埋め込みに使ったテキスト。**なぜ当たったかを読むために
            返す。**
        deprecated: 廃止済みか。**除外しない** — 廃止された用語を検索で
            見つけられないと「なぜ使えないのか」に答えられない
            (ADR-0017 決定3 と同じ向きで、警告として見せる)。
    """

    term_iri: str
    route: SearchRoute
    score: float
    vector_rank: int | None = None
    trigram_rank: int | None = None
    vector_similarity: float | None = None
    trigram_similarity: float | None = None
    source_text: str = ""
    deprecated: bool = False


@dataclass(frozen=True)
class SearchOutcome:
    """1 回の検索の結果。

    Attributes:
        hits: 当たった用語(スコアの降順)。
        vector_available: **ベクトルの経路を使えたか。** 偽なら
            「埋め込みを作っていない」か「モデルが未設定」である。
            **偽のときに「該当なし」と読んではいけない。**
        vector_note: `vector_available` が偽の理由。**空にしない。**
        embedded_term_count: その名前空間で埋め込みを持つ用語の数。
            **`0` は「作っていない」である。**
        deprecated_hits: 結果に含まれる廃止済みの用語の IRI。
    """

    hits: tuple[SearchHit, ...] = ()
    vector_available: bool = True
    vector_note: str = ""
    embedded_term_count: int = 0

    @property
    def deprecated_hits(self) -> tuple[str, ...]:
        """結果に現れた廃止済みの用語。**エージェントに警告として渡す。**"""
        return tuple(hit.term_iri for hit in self.hits if hit.deprecated)

    @property
    def conclusive(self) -> bool:
        """**両方の経路を使えたか。**

        偽のときに「これが全部です」と言ってはいけない。
        """
        return self.vector_available


@dataclass(frozen=True)
class RankedRow:
    """融合の入力。**経路ごとに類似度の降順で渡す**(順位は位置で決まる)。"""

    term_iri: str
    similarity: float
    source_text: str
    deprecated: bool


def fuse(
    *,
    vector: Sequence[RankedRow] = (),
    trigram: Sequence[RankedRow] = (),
    limit: int = DEFAULT_LIMIT,
) -> tuple[SearchHit, ...]:
    """2 つの経路を RRF で融合する。

    RRF は **順位だけを使う**。スコアの尺度が違う 2 つの経路(cosine 距離と
    3-gram の類似度)を足し合わせるより素直である — 尺度を揃える正規化は
    「どちらをどれだけ重視するか」という測っていない判断を含む。

    ```
    score(d) = Σ 1 / (RRF_K + rank_i(d))
    ```

    **同点の順序を決定的にする。** スコアが同じなら IRI の昇順にする —
    実行ごとに順序が変わると、エージェントの応答が再現しなくなる。

    Args:
        vector: ベクトルの経路の結果(類似度の降順)。
        trigram: 3-gram の経路の結果(類似度の降順)。
        limit: 返す件数。

    Returns:
        融合後の並び(スコアの降順、同点は IRI 昇順)。
    """
    ranks: dict[str, dict[str, int]] = {}
    similarity: dict[str, dict[str, float]] = {}
    meta: dict[str, tuple[str, bool]] = {}

    for route, rows in (("vector", vector), ("trigram", trigram)):
        for position, row in enumerate(rows, start=1):
            iri = row.term_iri
            ranks.setdefault(iri, {})[route] = position
            similarity.setdefault(iri, {})[route] = row.similarity
            if iri not in meta:
                meta[iri] = (row.source_text, row.deprecated)

    hits: list[SearchHit] = []
    for iri, positions in ranks.items():
        score = sum(1.0 / (RRF_K + position) for position in positions.values())
        source_text, deprecated = meta[iri]
        vector_rank = positions.get("vector")
        trigram_rank = positions.get("trigram")
        if vector_rank is not None and trigram_rank is not None:
            route = SearchRoute.BOTH
        elif vector_rank is not None:
            route = SearchRoute.VECTOR
        else:
            route = SearchRoute.TRIGRAM
        hits.append(
            SearchHit(
                term_iri=iri,
                route=route,
                score=score,
                vector_rank=vector_rank,
                trigram_rank=trigram_rank,
                vector_similarity=similarity[iri].get("vector"),
                trigram_similarity=similarity[iri].get("trigram"),
                source_text=source_text,
                deprecated=deprecated,
            )
        )

    # **同点は IRI の昇順。** 実行ごとに順序が変わらないようにする。
    hits.sort(key=lambda hit: (-hit.score, hit.term_iri))
    return tuple(hits[:limit])


def clamp_limit(limit: int) -> int:
    """件数を範囲に収める。

    **範囲外を黙って通さない**が、**例外にもしない** — 検索の件数は
    呼び出し側の都合で、そこで失敗させる理由が無い。上限に丸める。
    """
    if limit < 1:
        return 1
    return min(limit, MAX_LIMIT)


def route_summary(hits: Iterable[SearchHit]) -> dict[str, int]:
    """経路ごとの件数。**なぜその結果になったかを読むために返す。**"""
    summary = {route.value: 0 for route in SearchRoute}
    for hit in hits:
        summary[hit.route.value] += 1
    return summary
