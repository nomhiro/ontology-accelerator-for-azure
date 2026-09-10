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

## 質問ファイルの置き場所と 2 つの評価経路

**デプロイ済みの名前空間では PostgreSQL の `competency_question_sets` に
名前空間ごとの不変改訂として持つ**(ADR-0022 決定1・2、`P2B-14`)。
リポジトリ内のファイル(`samples/<namespace>.questions.yaml`)は、同梱
サンプルを CI で回すために残っている。

評価経路は 2 つあり、**問う相手が違う**。

| 関数 | 評価対象 | 用途 |
|---|---|---|
| `evaluate_questions_on_turtle` | **正本の TTL**(rdflib) | **`approve` の検査** |
| `run_questions` | ストア(Fuseki) | CI とローカル CLI |

前者が `approve` の検査である(ADR-0022 決定3)。後者は「射影後のストアが
実際に答えるか」を見る。

**`approve` でストアに問い合わせてはいけない。** その時点でその版はまだ
射影されていないし、承認をストアの可用性に依存させるのは不変条件3 が
守ろうとしているものの逆向きである(ADR-0022 決定3)。

**同じ質問を 2 つのエンジンで評価するので、判定が食い違う余地は構造的に
残る。** 同梱サンプルは両方で 11/11 を確認しているが、一般の保証ではない
(ADR-0022 の「受け入れるコスト」)。
"""

from __future__ import annotations

import hashlib
import itertools
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from rdflib import Dataset, URIRef

from ontology_core.sparql.client import SparqlStore, SparqlStoreError
from ontology_core.sparql.guards import QueryRejectedError, ensure_agent_safe_query

__all__ = [
    "EVALUATION_BUDGET_SECONDS",
    "MAX_QUESTIONS",
    "MAX_QUESTION_SET_BYTES",
    "CompetencyQuestion",
    "CompetencyReport",
    "Expectation",
    "QuestionAnswer",
    "QuestionFileError",
    "QuestionResult",
    "content_hash",
    "evaluate_questions_on_turtle",
    "load_questions",
    "parse_questions",
    "run_questions",
]

# 1 つの質問集合に入れられる質問の数。**承認経路で全件を評価する**ため、
# 上限が無いと承認の応答時間が無制限になる。実測で 200 件が 1.24 秒
# (トリプル 60 件のサンプルに対して。ADR-0022)。
MAX_QUESTIONS = 200

# 質問集合の本文の上限。同梱サンプルは 6.5 KB なので 40 倍の余裕がある。
MAX_QUESTION_SET_BYTES = 256 * 1024

# インメモリ評価の壁時計の予算(秒)。**質問の間で確認する。**
#
# **1 件のクエリの途中では打ち切れない。** rdflib にクエリタイムアウトは無く、
# 「全走査して 0 行」になる質問は最後まで走る(実測でトリプル 60 件の 3 重
# 直積が 7.69 秒)。呼び出し側は待つのをやめられる(リクエストはハングしない)
# が、スレッドはそのクエリが終わるまで走り続ける。**質問集合を書けるのが
# `owner` に限られていることが唯一の緩和である**(ADR-0022 決定5・6)。
EVALUATION_BUDGET_SECONDS = 30.0


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


@dataclass(frozen=True)
class QuestionAnswer:
    """質問への応答を、判定に必要な形だけに落としたもの。

    **2 つの評価経路(ストアと rdflib)の応答を 1 つの型に寄せる**ことで、
    判定のロジック(`_judge`)を 1 か所に保つ。ここが 2 つあると、経路に
    よって判定が違うという最悪の食い違いを作る。

    - `boolean`: ASK の答え。SELECT の応答なら `None`
    - `has_rows`: SELECT が 1 行以上返したか。ASK の応答なら `None`
    - `row_count`: 行数。**数えていないときは `None`**(`[]` ではなく `None`
      で「測っていない」を表すのは ADR-0020 / ADR-0021 と同じ扱い)

    `row_count` を数えないのは意図的である。判定に必要なのは存在の有無だけ
    で、全行を materialize すると桁でコストが変わる(実測で 216,000 行の
    直積が 9.68 秒 対 先頭 1 行が 0.000 秒。ADR-0022 決定4)。
    """

    boolean: bool | None = None
    has_rows: bool | None = None
    row_count: int | None = None


@dataclass(frozen=True)
class CompetencyReport:
    """質問集合 1 つの評価結果。

    **`not_evaluated` が空でないことを「合格」に丸めない。** 予算を超えて
    評価しなかった質問は、通ったのでも落ちたのでもない(ADR-0022 決定5)。
    呼び出し側はこれを 502 として扱う(落ちた質問がある 422 とは別物である
    — 運用者が取るべき対処が違う)。
    """

    results: tuple[QuestionResult, ...] = ()
    not_evaluated: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0
    graph_iri: str | None = None

    @property
    def evaluated_all(self) -> bool:
        """すべての質問を評価できたか。"""
        return not self.not_evaluated

    @property
    def failures(self) -> tuple[QuestionResult, ...]:
        return tuple(r for r in self.results if not r.passed)

    @property
    def conforms(self) -> bool:
        """**全件を評価できて、かつ全件が通った**ときだけ真。

        評価していない質問があるときに真を返してはいけない。
        """
        return self.evaluated_all and not self.failures

    def messages(self) -> list[str]:
        """落ちた質問と評価しなかった質問を、人が読める 1 行にする。"""
        lines = [f"{r.question.id}: {r.question.question} ({r.detail})" for r in self.failures]
        lines += [f"{qid}: 評価していません(予算超過)" for qid in self.not_evaluated]
        return lines


def _require(mapping: dict[str, Any], key: str, where: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise QuestionFileError(f"{where}: '{key}' が必要です(空でない文字列)")
    return value


def content_hash(text: str) -> str:
    """質問集合の本文のハッシュ。改訂の同一性の根拠にする。

    オントロジーの版が content_hash で冪等性を担保しているのと同じ形
    (`OntologyVersionRow`)。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_questions(path: str | Path) -> list[CompetencyQuestion]:
    """質問ファイル(YAML)を読み込んで検証する。

    Raises:
        QuestionFileError: ファイルが読めない、形式が不正、`id` が重複、
            `expect` が未知の値、SPARQL が読み取り専用でないとき。
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise QuestionFileError(f"{p} を読めません: {exc}") from exc
    return parse_questions(text, where=str(p))


def parse_questions(text: str, *, where: str = "質問集合") -> list[CompetencyQuestion]:
    """質問集合の本文(YAML)を解析して検証する。

    ファイルからも PostgreSQL の `competency_question_sets` からも同じ検証を
    通すため、解析をここに集める(ADR-0022 決定1)。**検証が 2 か所にあると、
    API 経由で入れた質問集合だけが検証をすり抜ける。**

    Raises:
        QuestionFileError: 形式が不正、`id` が重複、`expect` が未知の値、
            SPARQL が読み取り専用でない、件数や本文サイズが上限を超えるとき。
    """
    size = len(text.encode("utf-8"))
    if size > MAX_QUESTION_SET_BYTES:
        raise QuestionFileError(
            f"{where}: 本文が大きすぎます({size} バイト > {MAX_QUESTION_SET_BYTES})"
        )
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise QuestionFileError(f"{where} の YAML が不正です: {exc}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("questions"), list):
        raise QuestionFileError(f"{where}: 最上位に 'questions' の配列が必要です")

    if len(raw["questions"]) > MAX_QUESTIONS:
        # **承認経路で全件を評価する**ので、件数に上限が無いと承認の
        # 応答時間が無制限になる(ADR-0022 決定5)。
        raise QuestionFileError(
            f"{where}: 質問が多すぎます({len(raw['questions'])} 件 > {MAX_QUESTIONS})"
        )

    questions: list[CompetencyQuestion] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw["questions"], start=1):
        where_item = f"{where} の {index} 件目"
        if not isinstance(entry, dict):
            raise QuestionFileError(f"{where_item}: 各要素はマッピングである必要があります")
        qid = _require(entry, "id", where_item)
        if qid in seen:
            raise QuestionFileError(f"{where_item}: id '{qid}' が重複しています")
        seen.add(qid)
        expect_raw = _require(entry, "expect", where_item)
        try:
            expect = Expectation(expect_raw)
        except ValueError as exc:
            allowed = ", ".join(e.value for e in Expectation)
            raise QuestionFileError(
                f"{where_item}: expect '{expect_raw}' は使えません(使えるのは {allowed})"
            ) from exc
        sparql = _require(entry, "sparql", where_item)
        # 想定質問がストアを書き換えてよい理由は無い。
        #
        # **`SERVICE` を弾くのはインメモリ評価では一層重要である。** rdflib も
        # `SERVICE` で外部へ HTTP を投げるため、承認経路が SSRF の入口になる。
        try:
            ensure_agent_safe_query(sparql, allow_service=False)
        except QueryRejectedError as exc:
            raise QuestionFileError(f"{where_item} (id={qid}): {exc}") from exc
        questions.append(
            CompetencyQuestion(
                id=qid,
                question=_require(entry, "question", where_item),
                expect=expect,
                sparql=sparql,
            )
        )

    if not questions:
        raise QuestionFileError(f"{where}: 想定質問が 1 件もありません")
    return questions


def _answer_from_store(payload: dict[str, Any]) -> QuestionAnswer:
    """ストア(SPARQL 1.1 Query Results JSON)の応答を判定用の形に落とす。

    ストアは全件を返すので `row_count` を数えられる。インメモリ評価と違って
    ここで数えても追加のコストは発生しない。
    """
    if "boolean" in payload:
        return QuestionAnswer(boolean=bool(payload["boolean"]))
    results = payload.get("results")
    if isinstance(results, dict) and isinstance(results.get("bindings"), list):
        count = len(results["bindings"])
        return QuestionAnswer(has_rows=count > 0, row_count=count)
    # ASK でも SELECT でもない応答。どちらとも判定できないので両方 None。
    return QuestionAnswer()


def _rows_phrase(answer: QuestionAnswer) -> str:
    """行数を、**数えたかどうかを偽らずに**言葉にする。"""
    if answer.row_count is not None:
        return f"{answer.row_count} 行"
    return "1 行以上" if answer.has_rows else "0 行"


def _judge(question: CompetencyQuestion, answer: QuestionAnswer) -> QuestionResult:
    """応答を判定する。

    応答の形が `expect` と噛み合わないときは、**通したり落としたりせず
    「形が違う」として失敗させる**。ASK を書くつもりで SELECT を書いた等の
    間違いを、偶然の合否で隠さないため。
    """
    if question.expect is Expectation.ASK_TRUE:
        if answer.boolean is None:
            return QuestionResult(
                question,
                False,
                "expect が ask_true ですが ASK の応答ではありません(SELECT を書いていませんか)",
            )
        return QuestionResult(question, answer.boolean, f"ASK -> {answer.boolean}")

    if answer.has_rows is None:
        return QuestionResult(
            question,
            False,
            f"expect が {question.expect.value} ですが SELECT の応答ではありません"
            "(ASK を書いていませんか)",
        )
    phrase = _rows_phrase(answer)
    if question.expect is Expectation.NON_EMPTY:
        return QuestionResult(question, answer.has_rows, phrase)
    return QuestionResult(question, not answer.has_rows, phrase)


def evaluate_questions_on_turtle(
    turtle: str,
    questions: list[CompetencyQuestion],
    *,
    graph_iri: str | None = None,
    budget_seconds: float = EVALUATION_BUDGET_SECONDS,
) -> CompetencyReport:
    """正本の TTL に対して想定質問を評価する(rdflib、インメモリ)。

    **`approve` の検査に使う経路である**(ADR-0022 決定3)。ストアには問い
    合わせない — その時点でその版はまだ射影されていないし、承認をストアの
    可用性に依存させるのは不変条件3 が守ろうとしているものの逆である。

    `graph_iri` を渡すと、TTL を**既定グラフとその名前付きグラフの両方**に
    読み込む。ADR-0010 決定5・6 が「承認済み現行版は既定グラフと名前付き
    グラフの両方に載る」と決めているので、これが射影後の姿の忠実な写しに
    なる(`GRAPH <...>` を書いた質問がストアと同じように動く)。

    **行数は数えない。** 判定に必要なのは存在の有無だけで、全行を
    materialize すると桁でコストが変わる(ADR-0022 決定4)。

    **予算は質問の間で確認する。** 超えた分は `not_evaluated` に入る。
    1 件のクエリの途中では打ち切れない(`EVALUATION_BUDGET_SECONDS` 参照)。

    Raises:
        QuestionFileError: TTL を解析できないとき。**「評価できなかった」を
            合格に丸めない**ため、静かに空の結果を返さない。
    """
    started = time.perf_counter()
    dataset = Dataset(default_union=False)
    try:
        dataset.default_graph.parse(data=turtle, format="turtle")
        if graph_iri is not None:
            dataset.graph(URIRef(graph_iri)).parse(data=turtle, format="turtle")
    except Exception as exc:
        # rdflib の Turtle パーサは例外の型が一貫しない(`ontology_core.turtle`
        # の `validate_turtle` と同じ理由で広く捕まえる)。
        raise QuestionFileError(f"TTL を解析できないため想定質問を評価できません: {exc}") from exc

    results: list[QuestionResult] = []
    not_evaluated: list[str] = []
    for index, question in enumerate(questions):
        if index > 0 and time.perf_counter() - started > budget_seconds:
            not_evaluated.extend(q.id for q in questions[index:])
            break
        results.append(_judge(question, _evaluate_one(dataset, question)))

    return CompetencyReport(
        results=tuple(results),
        not_evaluated=tuple(not_evaluated),
        elapsed_seconds=time.perf_counter() - started,
        graph_iri=graph_iri,
    )


def _evaluate_one(dataset: Dataset, question: CompetencyQuestion) -> QuestionAnswer:
    """1 件の質問を rdflib で評価する。

    **クエリの失敗を「合格」にしない。** 例外はその質問の判定不能として
    扱い、`_judge` が「形が違う」として失敗させる(`QuestionAnswer()` は
    `boolean` も `has_rows` も `None` である)。
    """
    try:
        result = dataset.query(question.sparql)
    except Exception:
        return QuestionAnswer()

    # **応答の種類で分岐する。** `expect` を見て分岐すると、`expect: empty` に
    # ASK を書いた質問が「1 行以上」として判定されてしまう(実際にテストが
    # 捕まえた)。ストア経路が JSON の `boolean` / `results.bindings` の
    # どちらが来たかで分岐しているのと揃える。
    if result.type == "ASK":
        answer = result.askAnswer
        return QuestionAnswer() if answer is None else QuestionAnswer(boolean=bool(answer))
    if result.type != "SELECT":
        # CONSTRUCT / DESCRIBE は `expect` のどれとも噛み合わない。
        return QuestionAnswer()
    try:
        # **先頭 1 行だけを取る。** 全行を materialize しない(ADR-0022 決定4)。
        first = list(itertools.islice(iter(result), 1))
    except Exception:
        return QuestionAnswer()
    return QuestionAnswer(has_rows=bool(first))


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
        results.append(_judge(question, _answer_from_store(payload)))
    return results
