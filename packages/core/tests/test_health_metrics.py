"""健全性指標のテスト(P2B-06、ADR-0020)。

**最重要は「測れなかった」を「問題ゼロ」と報告しないこと**(決定3)。
健全性指標が障害時に「健全」と言うのは、目的に正面から反する。

`P2A-05` で「検証できなかった」を「違反ゼロ」と混同しないと決めたのと、
ADR-0016 決定5 で「計算できなかった」を「差分が無い」と混同しないと
決めたのと、同じ形の判断である。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ontology_core.health import (
    UNREFERENCED_WINDOW_DAYS,
    HealthInputs,
    HealthReport,
    compute_health,
)

_NOW = datetime(2026, 9, 10, tzinfo=UTC)
_BASE = "https://e.example/#"


def _inputs(
    *,
    terms: frozenset[str] | None = frozenset({_BASE + "A", _BASE + "B", _BASE + "C"}),
    last_access: dict[str, datetime] | None = None,
    owned: frozenset[str] = frozenset(),
    current_version: str | None = "2.0.0",
    approved_at: datetime | None = _NOW - timedelta(days=5),
    shacl_violations: int | None = 0,
    unprojected: int = 0,
    competency_questions: int | None = 0,
    disputed_mappings: int | None = 0,
    unavailable: tuple[str, ...] = (),
) -> HealthInputs:
    return HealthInputs(
        terms=terms,
        last_access=last_access or {},
        owned_terms=owned,
        current_version=current_version,
        current_approved_at=approved_at,
        shacl_violation_count=shacl_violations,
        unprojected_version_count=unprojected,
        competency_question_count=competency_questions,
        disputed_mapping_count=disputed_mappings,
        unavailable=unavailable,
        now=_NOW,
    )


# ---------------------------------------------------------------- 用語数


def test_用語数を数える() -> None:
    report = compute_health(_inputs())
    assert report.term_count == 3


def test_承認済み版が無ければ用語_0_で正しい() -> None:
    """**これは障害ではなく事実である**(ADR-0020 決定1)。

    その名前空間はまだエージェントに何も渡していない。`current_version` が
    `None` になるので、呼び出し側は「測れなかった」と区別できる。
    """
    report = compute_health(_inputs(terms=frozenset(), current_version=None, approved_at=None))
    assert report.term_count == 0
    assert report.current_version is None
    assert report.unavailable == ()


# ---------------------------------------------------------------- 未参照


def test_一度も参照されていない用語を未参照として数える() -> None:
    report = compute_health(_inputs(last_access={}))
    assert report.unreferenced_count == 3
    assert report.unreferenced_ratio == 1.0


def test_最近参照された用語は未参照に数えない() -> None:
    report = compute_health(_inputs(last_access={_BASE + "A": _NOW - timedelta(days=1)}))
    assert report.unreferenced_count == 2


def test_窓の外の参照は未参照として数える() -> None:
    """既定の窓は 90 日(ADR-0009 決定5)。"""
    old = _NOW - timedelta(days=UNREFERENCED_WINDOW_DAYS + 1)
    report = compute_health(_inputs(last_access={_BASE + "A": old}))
    assert report.unreferenced_count == 3


def test_窓の境界ちょうどは参照済みとして数える() -> None:
    """境界を off-by-one で外さない。**ちょうど 90 日前は「90 日以内」**である。"""
    boundary = _NOW - timedelta(days=UNREFERENCED_WINDOW_DAYS)
    report = compute_health(_inputs(last_access={_BASE + "A": boundary}))
    assert report.unreferenced_count == 2


def test_割合は用語数に対する比である() -> None:
    report = compute_health(_inputs(last_access={_BASE + "A": _NOW, _BASE + "B": _NOW}))
    assert report.unreferenced_count == 1
    assert report.unreferenced_ratio == 1 / 3


def test_用語が_0_なら割合は_0():  # type: ignore[no-untyped-def]
    """**ゼロ除算にしない。** 用語が無ければ「未参照の割合」は 0 である。"""
    report = compute_health(_inputs(terms=frozenset(), current_version=None))
    assert report.unreferenced_count == 0
    assert report.unreferenced_ratio == 0.0


def test_オントロジーに無い用語の参照は無視する() -> None:
    """**削除された用語や外部 IRI の参照が割合を狂わせないこと。**

    `term_access` は過去の参照を残すので、現在の版に無い用語の行が残りうる。
    """
    report = compute_health(
        _inputs(last_access={"https://e.example/#Gone": _NOW, _BASE + "A": _NOW})
    )
    assert report.unreferenced_count == 2, "Gone は用語一覧に無いので分母にも分子にも入らない"


# ---------------------------------------------------------------- 責任者


def test_責任者が未設定の用語を数える() -> None:
    report = compute_health(_inputs(owned=frozenset({_BASE + "A"})))
    assert report.without_owner_count == 2


def test_オントロジーに無い用語の責任者は無視する() -> None:
    """外部語彙に責任者を置ける(ADR-0015 決定3)ので、用語一覧に無い付与がある。"""
    report = compute_health(_inputs(owned=frozenset({"https://schema.org/Thing", _BASE + "A"})))
    assert report.without_owner_count == 2


def test_全部に責任者があれば_0() -> None:
    report = compute_health(_inputs(owned=frozenset({_BASE + "A", _BASE + "B", _BASE + "C"})))
    assert report.without_owner_count == 0


# ---------------------------------------------------------------- 承認の古さ


def test_承認からの経過日数を返す() -> None:
    report = compute_health(_inputs(approved_at=_NOW - timedelta(days=42)))
    assert report.approval_age_days == 42


def test_承認済み版が無ければ経過日数は_None() -> None:
    report = compute_health(_inputs(current_version=None, approved_at=None))
    assert report.approval_age_days is None


def test_approved_at_が無ければ経過日数は_None() -> None:
    """**0 にしない。** 「今日承認された」と「いつ承認されたか分からない」は違う。"""
    report = compute_health(_inputs(approved_at=None))
    assert report.approval_age_days is None


# ---------------------------------------------------------------- そのまま通す項目


def test_SHACL_違反の件数をそのまま通す() -> None:
    report = compute_health(_inputs(shacl_violations=3))
    assert report.shacl_violation_count == 3


def test_未射影の版の数をそのまま通す() -> None:
    report = compute_health(_inputs(unprojected=2))
    assert report.unprojected_version_count == 2


# ---------------------------------------------------------------- 測れなかった


def test_用語一覧が測れなければ関連項目は_None() -> None:
    """**ここが本題**(ADR-0020 決定3)。

    Blob へ到達できないと用語一覧が作れない。そのとき `0` を返すと
    「完全に健全」と報告することになる。
    """
    report = compute_health(_inputs(terms=None, unavailable=("正本の TTL を取得できませんでした",)))
    assert report.term_count is None
    assert report.unreferenced_count is None
    assert report.unreferenced_ratio is None
    assert report.without_owner_count is None
    assert report.unavailable == ("正本の TTL を取得できませんでした",)


def test_用語一覧が測れなくても_DB_だけで測れる項目は返る() -> None:
    """**「全部か無か」にしない**(決定3)。

    Blob の一時的な不調で未射影の版の数まで見えなくなってはいけない。
    """
    report = compute_health(
        _inputs(terms=None, approved_at=_NOW - timedelta(days=7), unprojected=2, unavailable=("x",))
    )
    assert report.term_count is None
    assert report.approval_age_days == 7
    assert report.unprojected_version_count == 2


def test_SHACL_が測れなければ_None_のまま() -> None:
    report = compute_health(
        _inputs(shacl_violations=None, unavailable=("SHACL 検証を実行できません",))
    )
    assert report.shacl_violation_count is None


def test_測れなかった理由を並べる() -> None:
    report = compute_health(_inputs(terms=None, shacl_violations=None, unavailable=("A", "B")))
    assert report.unavailable == ("A", "B")


def test_すべて測れたら_unavailable_は空() -> None:
    assert compute_health(_inputs()).unavailable == ()


# ---------------------------------------------------------------- 要約


def test_用語の一覧はオプトインで返す() -> None:
    """既定で返さないのは応答を小さく保つため(権限の話ではない)。"""
    report = compute_health(_inputs(last_access={_BASE + "A": _NOW}))
    without = report.summary(include_terms=False)
    assert "unreferenced_terms" not in without
    with_terms = report.summary(include_terms=True)
    assert with_terms["unreferenced_terms"] == [_BASE + "B", _BASE + "C"]
    assert with_terms["without_owner_terms"] == [_BASE + "A", _BASE + "B", _BASE + "C"]


def test_一覧は上限つきで切り詰め_切ったことを明示する() -> None:
    """**黙って切らない**(ADR-0016 決定7 と同じ判断)。"""
    many = frozenset(f"{_BASE}T{i:04d}" for i in range(120))
    report = compute_health(_inputs(terms=many, current_version="1.0.0"))
    summary = report.summary(include_terms=True, max_terms=50)
    assert len(summary["unreferenced_terms"]) == 50
    assert summary["unreferenced_count"] == 120
    assert summary["truncated"] is True


def test_切り捨てが無ければ_truncated_は_False() -> None:
    summary = compute_health(_inputs()).summary(include_terms=True, max_terms=50)
    assert summary["truncated"] is False


def test_一覧は安定して並ぶ() -> None:
    report = compute_health(_inputs())
    summary = report.summary(include_terms=True)
    assert summary["unreferenced_terms"] == sorted(summary["unreferenced_terms"])


def test_要約は_JSON_にできる() -> None:
    import json

    text = json.dumps(compute_health(_inputs()).summary(), ensure_ascii=False)
    restored = json.loads(text)
    assert restored["term_count"] == 3
    assert restored["unavailable"] == []


def test_測れなかった項目は要約でも_None() -> None:
    summary = compute_health(_inputs(terms=None, unavailable=("x",))).summary()
    assert summary["term_count"] is None
    assert summary["unreferenced_count"] is None
    assert summary["unavailable"] == ["x"]


def test_測れなかったときは一覧を返さない() -> None:
    """**空のリストを返さない。** 「未参照の用語は無い」と誤解させる。"""
    summary = compute_health(_inputs(terms=None, unavailable=("x",))).summary(include_terms=True)
    assert summary["unreferenced_terms"] is None
    assert summary["without_owner_terms"] is None


def test_型は_HealthReport() -> None:
    assert isinstance(compute_health(_inputs()), HealthReport)
