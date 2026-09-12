"""Web が依存している応答の形を固定する(ADR-0044 決定2、`P2A-03`)。

## なぜ Python 側にこのテストがあるのか

Web の型は **API から生成される**(ADR-0004)。生成されるので、API を変えれば
CI の web ジョブで `tsc` が落ちる — **ただし OpenAPI に形が出ている場合だけ**
である。

`GET /namespaces/{ns}/versions/{v}/diff` は **`dict[str, Any]` を返す**ので、
OpenAPI には `additionalProperties: true` としか出ない。つまり
**生成された型が存在せず、Web は手書きの型を持つしかない**
(`apps/web/src/review/diff.ts` の `DiffSummary`)。

ADR-0004 は手書きの型を却下していた。**ここは例外になっている** — だから
**代わりにこちら側で鍵の名前を固定する**。API を変えたらここが落ち、
TS 側を直す必要があることが分かる。

**このテストは「Web が読む鍵」を列挙しているだけで、意味は検査しない。**
意味の検査は `packages/core/tests/test_diff.py` にある。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ontology_core.diff import TripleDiffStatus, diff_ontologies

#: `apps/web/src/review/diff.ts` の `DiffSummary` が読む鍵。
#:
#: **増やすときは TS 側も直す。** 減らすとき(API から消すとき)は、
#: TS 側が `undefined` を読むようになるので必ず落ちる。
WEB_DIFF_KEYS = frozenset(
    {
        "empty",
        "triple_status",
        "blank_node_count",
        "has_removed_terms",
        "added_triple_count",
        "removed_triple_count",
        "truncated",
        "added_terms",
        "added_term_count",
        "removed_terms",
        "removed_term_count",
        "deprecated_terms",
        "deprecated_term_count",
        "modified_terms",
        "modified_term_count",
    }
)

#: TS 側が「計算していない」と判定する値。
#:
#: **文字列が一致していなければならない**(`diff.ts` の
#: `NOT_COMPUTED_STATUS`)。
WEB_NOT_COMPUTED = "skipped-too-many-blank-nodes"

_BASE = "https://e.example/web#"
_ONE = f"@prefix ex: <{_BASE}> .\nex:A a <http://www.w3.org/2002/07/owl#Class> .\n"
_TWO = _ONE + "ex:B a <http://www.w3.org/2002/07/owl#Class> .\n"


def test_Web_が読む鍵がすべて揃っている() -> None:
    """**鍵が欠けると TS 側は `undefined` を読む。**

    `undefined` は `0` でも `null` でもないので、画面の 3 状態の判定が
    静かに壊れる(`triple_status` が `undefined` なら「計算した」扱いに
    なり、**計算していない差分を「変更なし」として見せる**)。
    """
    summary = diff_ontologies(_ONE, _TWO).summary()
    missing = WEB_DIFF_KEYS - set(summary)
    assert not missing, (
        f"Web が読む鍵が応答に無い: {sorted(missing)}。"
        "apps/web/src/review/diff.ts の DiffSummary も直すこと"
    )


def test_計算していないことを示す値が一致している() -> None:
    """**TS 側は文字列で判定している**(`NOT_COMPUTED_STATUS`)。

    ここを変えると、画面は「計算した」と読んで**件数 0 を「変更なし」として
    見せる**。ADR-0016 決定5 が区別したものが画面で消える。
    """
    assert TripleDiffStatus.SKIPPED_TOO_MANY_BLANK_NODES.value == WEB_NOT_COMPUTED


def test_計算できたときの状態の値も一致している() -> None:
    """`"exact"` 以外が来たら TS 側は「計算していない」とは言わないが、
    **`NOT_COMPUTED` でもないので「計算できた」扱いになる**。

    状態が増えたらここが落ちる — そのとき TS 側の判定を見直す。
    """
    assert {status.value for status in TripleDiffStatus} == {"exact", WEB_NOT_COMPUTED}


def test_計算していないときに件数が_None_になる() -> None:
    """**0 ではなく `None` である**(ADR-0016 決定5)。

    TS 側の `DiffSummary` は `number | null` と宣言している。ここが 0 に
    変わると、**型は通るのに意味が反転する**(「測っていない」が
    「変更が無い」になる)。
    """
    many_blank_nodes = _blank_node_turtle(1200)
    result = diff_ontologies(_ONE, many_blank_nodes)

    assert result.triple_status is TripleDiffStatus.SKIPPED_TOO_MANY_BLANK_NODES
    summary = result.summary()
    assert summary["added_triple_count"] is None
    assert summary["removed_triple_count"] is None
    # **`empty` も `False` になる**(`is_empty` が「`None` なら `False`」)。
    # TS 側はこれに頼らず `triple_status` を直接見るが、両方を固定しておく。
    assert summary["empty"] is False


def test_変更された用語は_None_になりうる() -> None:
    """**空配列(0 件)ではない**。

    TS 側は `string[] | null` と宣言し、`null` のときは
    「測っていません」と表示する。
    """
    summary = diff_ontologies(_ONE, _blank_node_turtle(1200)).summary()
    assert summary["modified_terms"] is None
    assert summary["modified_term_count"] is None


def _blank_node_turtle(count: int) -> str:
    """空白ノードを `count` 個持つ Turtle を作る。

    **SHACL の property shape を使わない。** 使うと `approve` の SHACL 検証が
    「shapes 自体が不正」で落ちる(過去に踏んだ)。独自の述語で空白ノードだけを
    作る。
    """
    lines = [f"@prefix ex: <{_BASE}> ."]
    for index in range(count):
        lines.append(f"ex:Holder{index} ex:detail [ ex:index {index} ] .")
    return "\n".join(lines) + "\n"


def test_TS_側の宣言とこのテストの鍵が一致している() -> None:
    """**TS のファイルを実際に読んで比べる。**

    列挙を 2 か所に置くと片方だけ直る。`diff.ts` の `DiffSummary` の
    宣言から鍵を抜き出して、この列挙と突き合わせる。
    """
    source = Path(__file__).resolve().parents[3] / "apps/web/src/review/diff.ts"
    if not source.exists():  # pragma: no cover - リポジトリ内では必ずある
        pytest.skip("apps/web が無い")
    body = source.read_text(encoding="utf-8")
    start = body.index("export interface DiffSummary {")
    end = body.index("}", start)
    declared = {
        line.split(":")[0].strip()
        for line in body[start:end].splitlines()[1:]
        if ":" in line and not line.strip().startswith(("/**", "*", "//"))
    }
    assert declared == set(WEB_DIFF_KEYS), (
        f"TS 側の宣言とこのテストの列挙が食い違っている。"
        f"TS のみ: {sorted(declared - WEB_DIFF_KEYS)} / "
        f"こちらのみ: {sorted(WEB_DIFF_KEYS - declared)}"
    )
