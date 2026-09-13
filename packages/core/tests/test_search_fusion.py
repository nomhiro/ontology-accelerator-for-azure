"""2 つの検索経路の融合(ADR-0050、`P3-02`)。

ここで固定するのは 5 つである。

1. **RRF は順位だけを使う。** 尺度の違う 2 つの類似度を足し合わせない
2. **`both` を別の値にする。** 「両方で当たった」は「片方で当たった」より
   強い根拠であり、潰すとその情報が消える
3. **同点の順序が決定的である。** 実行ごとに順序が変わると、エージェントの
   応答が再現しない
4. **`vector_available` が偽のとき `conclusive` も偽になる。**
   「測れなかった」を「問題なし」にしない
5. **廃止済みの用語を結果から除外しない。** 除外すると「なぜ使えないのか」に
   答えられない(ADR-0017 決定3)
"""

from __future__ import annotations

from ontology_core.search import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    RRF_K,
    RankedRow,
    SearchHit,
    SearchOutcome,
    SearchRoute,
    clamp_limit,
    fuse,
    route_summary,
)


def _row(iri: str, similarity: float, *, deprecated: bool = False) -> RankedRow:
    return RankedRow(
        term_iri=iri,
        similarity=similarity,
        source_text=f"{iri} の材料",
        deprecated=deprecated,
    )


def test_両方の経路で当たったら_both_になる() -> None:
    """**`both` を潰さない。** 片方より強い根拠である。"""
    hits = fuse(vector=[_row("urn:a", 0.9)], trigram=[_row("urn:a", 0.8)])
    assert len(hits) == 1
    assert hits[0].route is SearchRoute.BOTH
    assert hits[0].vector_rank == 1
    assert hits[0].trigram_rank == 1


def test_片方だけで当たったら経路がそのまま出る() -> None:
    hits = fuse(vector=[_row("urn:v", 0.9)], trigram=[_row("urn:t", 0.8)])
    routes = {hit.term_iri: hit.route for hit in hits}
    assert routes == {"urn:v": SearchRoute.VECTOR, "urn:t": SearchRoute.TRIGRAM}


def test_当たらなかった経路の類似度は_None_である() -> None:
    """**`None` は「その経路で当たらなかった」であって「似ていない」ではない。**

    `0.0` を入れると「類似度を測ったら 0 だった」と読めてしまい、
    ベクトルの経路が引かれなかったことが消える。
    """
    hits = fuse(trigram=[_row("urn:t", 0.8)])
    assert hits[0].vector_similarity is None
    assert hits[0].trigram_similarity == 0.8


def test_両方で当たった方が片方より上位になる() -> None:
    """RRF の要点。**2 つの経路の合意が順位に効く。**"""
    hits = fuse(
        vector=[_row("urn:only-v", 0.99), _row("urn:both", 0.10)],
        trigram=[_row("urn:both", 0.10), _row("urn:only-t", 0.99)],
    )
    assert hits[0].term_iri == "urn:both"


def test_スコアは順位だけで決まる() -> None:
    """**類似度の値を足さない。** cosine と 3-gram は尺度が違う。

    類似度を 0.99 にしても 0.01 にしても、順位が同じならスコアは同じである。
    """
    high = fuse(vector=[_row("urn:a", 0.99)])
    low = fuse(vector=[_row("urn:a", 0.01)])
    assert high[0].score == low[0].score == 1.0 / (RRF_K + 1)


def test_同点は_IRI_の昇順で決定的になる() -> None:
    """**実行ごとに順序が変わってはいけない。**

    どちらも「ベクトルの経路で 1 位」ではないが、順位が同じ組み合わせに
    なる形を作る。
    """
    first = fuse(vector=[_row("urn:b", 0.5)], trigram=[_row("urn:a", 0.5)])
    second = fuse(vector=[_row("urn:b", 0.5)], trigram=[_row("urn:a", 0.5)])
    assert [hit.term_iri for hit in first] == ["urn:a", "urn:b"]
    assert [hit.term_iri for hit in first] == [hit.term_iri for hit in second]


def test_件数の上限が効く() -> None:
    rows = [_row(f"urn:{index:02d}", 1.0 - index / 100) for index in range(20)]
    assert len(fuse(vector=rows, limit=5)) == 5


def test_材料は最初に見た経路のものを使う() -> None:
    """**両方の経路が同じ行を返すので、材料は同じである。**

    ここで固定したいのは「空にならない」こと。検索の結果に
    「なぜ当たったか」を返せなくなると、説明可能性が消える。
    """
    hits = fuse(vector=[_row("urn:a", 0.9)], trigram=[_row("urn:a", 0.8)])
    assert hits[0].source_text == "urn:a の材料"


def test_廃止済みの用語は除外されず印が付く() -> None:
    """**ADR-0017 決定3 と同じ向き。** 見つけられないと理由を答えられない。"""
    outcome = SearchOutcome(hits=fuse(trigram=[_row("urn:old", 0.9, deprecated=True)]))
    assert outcome.hits[0].deprecated is True
    assert outcome.deprecated_hits == ("urn:old",)


def test_空の入力は空の結果になる() -> None:
    assert fuse() == ()


def test_件数は範囲に丸められる() -> None:
    """**範囲外を黙って通さないが、例外にもしない。**"""
    assert clamp_limit(0) == 1
    assert clamp_limit(-5) == 1
    assert clamp_limit(MAX_LIMIT + 100) == MAX_LIMIT
    assert clamp_limit(DEFAULT_LIMIT) == DEFAULT_LIMIT


def test_ベクトルが使えないとき_conclusive_が偽になる() -> None:
    """**「測れなかった」を「問題なし」にしない。**

    この製品で繰り返し出てくる形である(ADR-0016 決定5 ほか)。
    """
    outcome = SearchOutcome(vector_available=False, vector_note="理由")
    assert outcome.conclusive is False


def test_ベクトルが使えるとき_conclusive_が真になる() -> None:
    assert SearchOutcome(vector_available=True).conclusive is True


def test_経路ごとの件数を数える() -> None:
    """**なぜその結果になったかを読むために返す。**"""
    hits = (
        SearchHit(term_iri="urn:a", route=SearchRoute.BOTH, score=0.1),
        SearchHit(term_iri="urn:b", route=SearchRoute.VECTOR, score=0.1),
        SearchHit(term_iri="urn:c", route=SearchRoute.VECTOR, score=0.1),
    )
    assert route_summary(hits) == {"vector": 2, "trigram": 0, "both": 1}


def test_経路ごとの件数はすべての経路を鍵に持つ() -> None:
    """**当たらなかった経路を省かない。** 省くと「引かなかった」と
    「引いたが 0 件だった」が区別できない。
    """
    assert set(route_summary(()).keys()) == {route.value for route in SearchRoute}
    assert route_summary(()) == {"vector": 0, "trigram": 0, "both": 0}


def test_RRF_の定数は原論文の値である() -> None:
    """**調整していない。** 調整するなら「何に対して良くなったか」を
    測る必要があり、測っていないものを調整したと書けない。
    """
    assert RRF_K == 60
