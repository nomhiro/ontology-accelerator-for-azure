"""結果件数の上限(ADR-0025、`P2A-08`)。

**設定が「効いている」ように見えて効いていない状態を終わらせた。**
`SPARQL_MAX_RESULTS` は長らく `Settings` が保持し Bicep が注入するだけで、
どこも強制していなかった。

ここで固定するのは 2 つである。

1. **上限を超えたら切り詰める**(決定2)
2. **切り詰めたことを必ず返す**(決定3)。黙って切ると、エージェントは
   「該当は N 件で全部見た」と信じて回答を作る
"""

from __future__ import annotations

from typing import Any

import pytest

from ontology_core.sparql.limits import cap_bindings


def _select(rows: int) -> dict[str, Any]:
    bindings = [{"s": {"type": "uri", "value": f"urn:x{i}"}} for i in range(rows)]
    return {"head": {"vars": ["s"]}, "results": {"bindings": bindings}}


def _rows(payload: dict[str, Any]) -> int:
    return len(payload["results"]["bindings"])


# ---------------------------------------------------------------- 切り詰め


def test_上限以下なら切り詰めない() -> None:
    capped = cap_bindings(_select(5), 10)
    assert not capped.truncated
    assert capped.total_rows == 5
    assert _rows(capped.payload) == 5


def test_上限ちょうどは切り詰めない() -> None:
    """**境界で切ると 1 件足りない結果を「全部」として返す。**"""
    capped = cap_bindings(_select(10), 10)
    assert not capped.truncated
    assert capped.total_rows == 10
    assert _rows(capped.payload) == 10


def test_上限を超えたら切り詰めて知らせる() -> None:
    capped = cap_bindings(_select(11), 10)
    assert capped.truncated
    # **「本当は何行あったか」を返す。** これが無いと、受け取った側は
    # 「ちょうど上限だった」のか「もっとあった」のか分からない。
    assert capped.total_rows == 11
    assert _rows(capped.payload) == 10


def test_切り詰めても先頭から順に残す() -> None:
    """順序を入れ替えないこと。`ORDER BY` の意味が壊れる。"""
    capped = cap_bindings(_select(5), 2)
    values = [b["s"]["value"] for b in capped.payload["results"]["bindings"]]
    assert values == ["urn:x0", "urn:x1"]


def test_元の辞書を書き換えない() -> None:
    """**呼び出し側は切り詰める前の行数をアクセスログに記録する**(決定6)。

    破壊的に書き換えると「記録より先に切り詰めてはいけない」という順序の
    制約が生まれ、後から順序を変えた人が静かに壊す。
    """
    original = _select(11)
    capped = cap_bindings(original, 10)
    assert _rows(original) == 11
    assert _rows(capped.payload) == 10
    assert capped.payload is not original


def test_上限_1_でも動く() -> None:
    capped = cap_bindings(_select(3), 1)
    assert capped.truncated
    assert _rows(capped.payload) == 1


# ------------------------------------------------------------ 対象外の応答


def test_ASK_は対象外() -> None:
    """行の概念が無い。**`total_rows` は `None`**(0 ではない)。"""
    payload = {"head": {}, "boolean": True}
    capped = cap_bindings(payload, 10)
    assert not capped.truncated
    assert capped.total_rows is None
    assert capped.payload is payload


@pytest.mark.parametrize(
    "payload",
    [
        {"head": {"vars": ["s"]}},
        {"head": {}, "results": {}},
        {"head": {}, "results": {"bindings": "行ではない"}},
        {},
    ],
)
def test_判定できない応答を_0_行として扱わない(payload: dict[str, Any]) -> None:
    """**切り詰めの判断ができないものを「切り詰めていない」と報告しない。**

    `total_rows=0` を返すと「0 行だった」と読めてしまう。`None` は
    「数えていない」である(ADR-0020 / ADR-0021 と同じ扱い)。
    """
    capped = cap_bindings(payload, 10)
    assert capped.total_rows is None
    assert not capped.truncated


def test_空の結果は_0_行として数える() -> None:
    """**こちらは本当に 0 行である。** `None` と区別する。"""
    capped = cap_bindings(_select(0), 10)
    assert capped.total_rows == 0
    assert not capped.truncated


def test_boolean_と_results_の両方があれば_ASK_として扱う() -> None:
    """**優先順位を固定する。**

    壊れた応答(あるいは将来の別のストア)が両方を持ってきたとき、行として
    数えて切り詰めると **`ASK` の答えが「1 行に切り詰めました」として返る**。
    `boolean` があれば真偽の応答である。

    これが `cap_bindings` の先頭の早期 return を意味のあるものにしている
    (これが無いと、早期 return を外しても後続の判定が同じ結果を返すので
    変異テストで検出できない — 実際に生き残った)。
    """
    payload: dict[str, Any] = {
        "head": {},
        "boolean": True,
        "results": {"bindings": [{"s": {"value": "urn:x"}}] * 5},
    }
    capped = cap_bindings(payload, 1)
    assert not capped.truncated
    assert capped.total_rows is None
    assert capped.payload is payload
