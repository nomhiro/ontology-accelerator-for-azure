"""想定質問(Competency Questions)を SPARQL テストとして実行する。

ADR-0009 決定6。「このオントロジーは『ロットのリコールで影響を受ける顧客』に
答えられなければならない」を **SPARQL として書き、CI で回す**。オントロジーが
目的を果たしているかを主観なしに判定できる。

品質スコア型の評価は採らない(ADR-0009 の代替案)。点数が下がった理由が行動に
結びつかないため。想定質問なら、落ちたときに何を直すべきかが自明である。

## 3 つの判定モード(`expect`)がある理由

**スキーマだけのオントロジーには、行を返す質問は書けない。** 同梱サンプル
(`samples/retail-core.ttl`)はクラス・プロパティ・SKOS・SHACL だけで、
インスタンスデータを含まない。実データは Ontop 経由の連邦クエリ(Phase 3)で
初めて現れる。それまでの間、想定質問が検査できるのは「**その問いに答えるための
語彙と関係が存在するか**」である。

そのため判定モードを分ける。

| `expect` | 意味 | 主な用途 |
|---|---|---|
| `ask_true` | ASK が true を返す | **語彙の表現力**。「注文から顧客へ辿る関係が定義されているか」 |
| `non_empty` | SELECT が 1 行以上返す | **データ検索**。実データがある場合(Phase 3 以降) |
| `empty` | SELECT が 0 行返す | **規約の遵守**。「ラベルの無いクラスが無いこと」 |

`empty` は ADR-0009 決定1 の「合意済みの規約(命名規則、必須項目)はテストとして
機械が実行する」をそのまま満たす。想定質問と規約チェックを同じ仕組みで書ける。

## 想定質問は読み取り専用でなければならない

質問ファイルは人が書くものだが、**テストがストアを書き換えてよい理由は無い**。
`ensure_agent_safe_query` を通して SPARQL Update と `SERVICE` 句を弾く。

## 質問ファイルの置き場所

現時点ではリポジトリ内のファイル(`samples/<namespace>.questions.yaml`)を
CI で回すだけである。**デプロイ済みの名前空間に質問を紐づける仕組みは未決**で、
Blob に置くのか PostgreSQL に持つのか、`approve` をブロックするのかを含めて
バックログ `P2B-14` で扱う。ここで storage を勝手に発明しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from ontology_core.sparql.client import SparqlStore, SparqlStoreError
from ontology_core.sparql.guards import QueryRejectedError, ensure_agent_safe_query

__all__ = [
    "CompetencyQuestion",
    "Expectation",
    "QuestionFileError",
    "QuestionResult",
    "load_questions",
    "run_questions",
]


class Expectation(StrEnum):
    """想定質問の判定モード。"""

    ASK_TRUE = "ask_true"
    NON_EMPTY = "non_empty"
    EMPTY = "empty"


class QuestionFileError(ValueError):
    """質問ファイルが読めない、または内容が不正であることを表す。

    **黙って読み飛ばさない。** 質問が 1 件でも壊れていれば全体を失敗させる。
    静かにスキップされたテストは、通っているように見えて何も検証しない
    (`packages/api/tests/conftest.py` と同じ方針)。
    """


@dataclass(frozen=True)
class CompetencyQuestion:
    """1 件の想定質問。"""

    id: str
    question: str
    expect: Expectation
    sparql: str


@dataclass(frozen=True)
class QuestionResult:
    """1 件の実行結果。"""

    question: CompetencyQuestion
    passed: bool
    detail: str


def _require(mapping: dict[str, Any], key: str, where: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise QuestionFileError(f"{where}: '{key}' が必要です(空でない文字列)")
    return value


def load_questions(path: str | Path) -> list[CompetencyQuestion]:
    """質問ファイル(YAML)を読み込んで検証する。

    Raises:
        QuestionFileError: ファイルが読めない、形式が不正、`id` が重複、
            `expect` が未知の値、SPARQL が読み取り専用でないとき。
    """
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise QuestionFileError(f"{p} を読めません: {exc}") from exc
    except yaml.YAMLError as exc:
        raise QuestionFileError(f"{p} の YAML が不正です: {exc}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("questions"), list):
        raise QuestionFileError(f"{p}: 最上位に 'questions' の配列が必要です")

    questions: list[CompetencyQuestion] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw["questions"], start=1):
        where = f"{p} の {index} 件目"
        if not isinstance(entry, dict):
            raise QuestionFileError(f"{where}: 各要素はマッピングである必要があります")
        qid = _require(entry, "id", where)
        if qid in seen:
            raise QuestionFileError(f"{where}: id '{qid}' が重複しています")
        seen.add(qid)
        expect_raw = _require(entry, "expect", where)
        try:
            expect = Expectation(expect_raw)
        except ValueError as exc:
            allowed = ", ".join(e.value for e in Expectation)
            raise QuestionFileError(
                f"{where}: expect '{expect_raw}' は使えません(使えるのは {allowed})"
            ) from exc
        sparql = _require(entry, "sparql", where)
        # 想定質問がストアを書き換えてよい理由は無い。
        try:
            ensure_agent_safe_query(sparql, allow_service=False)
        except QueryRejectedError as exc:
            raise QuestionFileError(f"{where} (id={qid}): {exc}") from exc
        questions.append(
            CompetencyQuestion(
                id=qid,
                question=_require(entry, "question", where),
                expect=expect,
                sparql=sparql,
            )
        )

    if not questions:
        raise QuestionFileError(f"{p}: 想定質問が 1 件もありません")
    return questions


def _judge(question: CompetencyQuestion, payload: dict[str, Any]) -> QuestionResult:
    """ストアの応答を判定する。

    応答の形が `expect` と噛み合わないときは、**通したり落としたりせず
    「形が違う」として失敗させる**。ASK を書くつもりで SELECT を書いた等の
    間違いを、偶然の合否で隠さないため。
    """
    if question.expect is Expectation.ASK_TRUE:
        if "boolean" not in payload:
            return QuestionResult(
                question,
                False,
                "expect が ask_true ですが ASK の応答ではありません(SELECT を書いていませんか)",
            )
        value = bool(payload["boolean"])
        return QuestionResult(question, value, f"ASK -> {value}")

    results = payload.get("results")
    if not isinstance(results, dict) or not isinstance(results.get("bindings"), list):
        return QuestionResult(
            question,
            False,
            f"expect が {question.expect.value} ですが SELECT の応答ではありません"
            "(ASK を書いていませんか)",
        )
    count = len(results["bindings"])
    if question.expect is Expectation.NON_EMPTY:
        return QuestionResult(question, count > 0, f"{count} 行")
    return QuestionResult(question, count == 0, f"{count} 行")


async def run_questions(
    store: SparqlStore, dataset: str, questions: list[CompetencyQuestion]
) -> list[QuestionResult]:
    """想定質問をストアに対して実行する。

    **1 件の失敗で打ち切らない。** どの質問が落ちたかを全部知りたいので、
    各件を独立に実行して結果を集める(`reconcile` と同じ方針)。
    ストアの失敗(`SparqlStoreError`)もその質問の失敗として扱う。
    """
    results: list[QuestionResult] = []
    for question in questions:
        try:
            payload = await store.query(question.sparql, dataset=dataset)
        except SparqlStoreError as exc:
            results.append(QuestionResult(question, False, f"クエリの実行に失敗: {exc}"))
            continue
        results.append(_judge(question, payload))
    return results
