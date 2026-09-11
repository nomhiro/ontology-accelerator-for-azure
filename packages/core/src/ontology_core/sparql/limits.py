"""結果件数の上限を境界で強制する(ADR-0025、`P2A-08`)。

## なぜクエリを書き換えないのか

`LIMIT` を後付けする実装は採らない(ADR-0025 決定1)。

- 既存の `LIMIT` / `OFFSET`、副問い合わせ、`GROUP BY` / `ORDER BY` との
  相互作用で**意味が変わる**(外側に `LIMIT` を付けると集約の結果が変わる)
- **`CONSTRUCT` の `LIMIT` は解の数であってトリプル数ではない**
- クエリを書き換える実装は、ADR-0001 が名前空間の隔離で避けた
  「任意 SPARQL の書き換え」と同じ形である

## なぜストア側で止めないのか

**止められない**(実測)。Fuseki 6.2.0 / Jena ARQ 6.2.0 には行数の上限が無い。

| 調べたもの | 結果 |
|---|---|
| ARQ のコンテキスト記号 | `queryTimeout` / `updateTimeout` / `httpQueryTimeout` のみ |
| `fuseki:queryLimit`(語彙にある) | **効かない**(12 行に `queryLimit 5` で 12 行返った) |
| その読み手 | 語彙の定義クラスだけ。**実装に読み手がいない** |

`arq:queryTimeout` は効いているので**時間は止められるが行数は止められない**。

## 黙って切らない

切り詰めたことは必ず呼び出し側へ返す。**切り詰めた結果を「全部です」として
返すのがいちばん避けたい形**である — エージェントは「該当は 10,000 件で全部
見た」と信じて回答を作る。

`ontology_core.diff`(空白ノードの上限)・`ontology_core.health`(測れなかった
指標)・`ontology_core.competency`(評価しきれなかった質問)と同じ扱いである。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["CappedResults", "cap_bindings"]


@dataclass(frozen=True)
class CappedResults:
    """上限を当てた後の結果(ADR-0025 決定2・3)。

    Attributes:
        payload: 呼び出し元へ返す SPARQL Results JSON。**元の辞書は壊さない。**
        truncated: 切り詰めたか。**偽でも `total_rows` は意味を持つ。**
        total_rows: **ストアが返した行数**(切り詰める前)。`ASK` のように行の
            概念が無い応答では `None`。

            アクセスログにはこちらを記録する(ADR-0025 決定6)。
            「エージェントに何行渡したか」ではなく「**何行返ろうとしたか**」で
            なければ、上限に張り付いているクエリを見つけられない。
    """

    payload: dict[str, Any]
    truncated: bool
    total_rows: int | None


def cap_bindings(payload: dict[str, Any], limit: int) -> CappedResults:
    """`SELECT` の結果を `limit` 件に切る。

    Args:
        payload: SPARQL 1.1 Query Results JSON。
        limit: 返す行数の上限。**1 以上**(`Settings` が `ge=1` で検証する)。

    Returns:
        `CappedResults`。

    **`ASK` は対象外である。** 行の概念が無いので `boolean` を持つ応答は
    そのまま返し、`total_rows` は `None` になる。**「対象外」をコードで明示
    するために早期に返す** — `results` が無いことを暗黙に扱うと、後から
    `CONSTRUCT` の応答を通したときに黙って 0 行として扱ってしまう。

    **元の辞書を書き換えない。** 呼び出し側(ルータ)が切り詰める前の行数を
    アクセスログに記録する必要があり、破壊的に書き換えると順序の制約が
    生まれる。
    """
    if "boolean" in payload:
        # ASK の応答。行の概念が無い。
        return CappedResults(payload=payload, truncated=False, total_rows=None)

    results = payload.get("results")
    if not isinstance(results, dict) or not isinstance(results.get("bindings"), list):
        # SELECT でも ASK でもない応答。**0 行として扱わない** —
        # 切り詰めの判断ができないものを「切り詰めていない」と報告するのは、
        # このモジュールが避けたい形そのものである。
        return CappedResults(payload=payload, truncated=False, total_rows=None)

    bindings: list[Any] = results["bindings"]
    total = len(bindings)
    if total <= limit:
        return CappedResults(payload=payload, truncated=False, total_rows=total)

    # **浅いコピーで足りる。** 差し替えるのは `results` と `bindings` だけで、
    # `head` や個々の束縛は共有してよい(呼び出し側は読むだけである)。
    capped = dict(payload)
    capped["results"] = {**results, "bindings": bindings[:limit]}
    return CappedResults(payload=capped, truncated=True, total_rows=total)
