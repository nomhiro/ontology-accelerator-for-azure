"""健全性指標(ADR-0020、`P2B-06`)。

[ADR-0009](../../../../docs/adr/0009-ontology-operations.md) 決定5 の 6 項目を
集計する。同 ADR の言葉で言えば「**測っていないものは、致命的になるまで
見えない**」。

## 「測れなかった」を「問題ゼロ」と報告しない

各項目は `int` か `None` で返す。`None` は「測れなかった」であり **`0` とは
違う**。健全性指標が障害時に「健全」と言うのは、目的に正面から反する。

これは `P2A-05` で「検証できなかった」を「違反ゼロ」と混同しないと決めたのと、
`ontology_core.diff` で「計算できなかった」を「差分が無い」と混同しないと
決めたのと、同じ形の判断である。

**「全部か無か」にもしない。** 用語の一覧が作れなくても、PostgreSQL だけで
測れる項目(承認の古さ、未射影の版)はそのまま返す。Blob の一時的な不調で
未射影の版の数まで見えなくなってはいけない。

## 用語の一覧は正本から取る

このモジュールは `terms` を受け取るだけだが、**呼び出し元はストアではなく
正本(Blob の TTL)から作らなければならない**(ADR-0020 決定1)。ストアは
再構築可能な射影であって正本ではないので、ストアが空のときにストアを数えると
**「用語数 0、未参照 0 件、責任者未設定 0 件」= 完全に健全**という報告になる。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

__all__ = [
    "SUMMARY_MAX_TERMS",
    "UNREFERENCED_WINDOW_DAYS",
    "HealthInputs",
    "HealthReport",
    "compute_health",
]

#: 「参照されていない」と数えるまでの日数(ADR-0009 決定5 の既定 90 日)。
UNREFERENCED_WINDOW_DAYS = 90

#: 要約に載せる用語 IRI の既定の上限(`ontology_core.diff` と揃える)。
SUMMARY_MAX_TERMS = 50


@dataclass(frozen=True)
class HealthInputs:
    """指標の計算に必要な材料。

    Attributes:
        terms: その名前空間が発行した用語の IRI。**`None` は「測れなかった」**
            (正本の TTL を取得・解析できなかった)。空集合とは違う。
        last_access: 用語 IRI から最後に参照された時刻への対応(`term_access`)。
        owned_terms: 責任者が設定されている用語の IRI(`term_owners`)。
        current_version: 現在の承認済み版。無ければ `None`。
        current_approved_at: その版が承認された時刻。
        shacl_violation_count: SHACL 違反の件数。**`None` は「測れなかった」**。
        unprojected_version_count: `projected_at IS NULL` の版の数。
            PostgreSQL だけで測れるので `None` にならない。
        competency_question_count: 有効な質問集合の質問の件数
            (ADR-0022 決定7)。**`0` は「基準を定めていない」**であって
            「基準を満たしていない」ではない。`None` は測れなかったとき。
        unavailable: 測れなかった項目の理由。
        now: 「今」。テストのために外から渡す。
    """

    terms: frozenset[str] | None
    last_access: dict[str, datetime]
    owned_terms: frozenset[str]
    current_version: str | None
    current_approved_at: datetime | None
    shacl_violation_count: int | None
    unprojected_version_count: int
    competency_question_count: int | None
    unavailable: tuple[str, ...]
    now: datetime


@dataclass(frozen=True)
class HealthReport:
    """健全性指標の報告(ADR-0009 決定5 の 6 項目 + ADR-0022 の 1 項目)。

    **`None` は「測れなかった」であり `0` ではない。**

    `competency_question_count` だけは `0` にも意味がある — 「受け入れ基準を
    定めていない」である(ADR-0022 決定7)。定めていない名前空間の承認は
    ブロックしないので、**ここに出ることが唯一それが見える経路である。**
    """

    namespace: str
    measured_at: datetime
    current_version: str | None
    term_count: int | None
    unreferenced_terms: tuple[str, ...] | None
    without_owner_terms: tuple[str, ...] | None
    approval_age_days: int | None
    shacl_violation_count: int | None
    unprojected_version_count: int
    competency_question_count: int | None
    unavailable: tuple[str, ...]
    unreferenced_window_days: int = UNREFERENCED_WINDOW_DAYS

    @property
    def unreferenced_count(self) -> int | None:
        return None if self.unreferenced_terms is None else len(self.unreferenced_terms)

    @property
    def without_owner_count(self) -> int | None:
        return None if self.without_owner_terms is None else len(self.without_owner_terms)

    @property
    def unreferenced_ratio(self) -> float | None:
        """未参照の用語の割合。

        **用語が 0 のときは 0.0 を返す**(ゼロ除算にしない)。用語が無ければ
        「未参照の割合」は 0 である。
        """
        if self.term_count is None or self.unreferenced_count is None:
            return None
        if self.term_count == 0:
            return 0.0
        return self.unreferenced_count / self.term_count

    def summary(
        self, *, include_terms: bool = False, max_terms: int = SUMMARY_MAX_TERMS
    ) -> dict[str, Any]:
        """JSON にできる要約を返す。

        `include_terms` が真のとき用語 IRI の一覧も載せる。**既定で載せないのは
        応答を小さく保つため**で、権限の話ではない(件数は個人を特定しない)。

        一覧は `max_terms` 件で切り、**切ったことを `truncated` で明示する**
        (`ontology_core.diff` と同じ判断。黙って切らない)。
        """
        result: dict[str, Any] = {
            "namespace": self.namespace,
            "measured_at": self.measured_at.isoformat(),
            "current_version": self.current_version,
            "term_count": self.term_count,
            "unreferenced_window_days": self.unreferenced_window_days,
            "unreferenced_count": self.unreferenced_count,
            "unreferenced_ratio": self.unreferenced_ratio,
            "without_owner_count": self.without_owner_count,
            "approval_age_days": self.approval_age_days,
            "shacl_violation_count": self.shacl_violation_count,
            "unprojected_version_count": self.unprojected_version_count,
            "competency_question_count": self.competency_question_count,
            "unavailable": list(self.unavailable),
        }
        lists = (self.unreferenced_terms, self.without_owner_terms)
        result["truncated"] = any(v is not None and len(v) > max_terms for v in lists)
        if include_terms:
            # **測れなかったときに空のリストを返さない。** 「未参照の用語は
            # 無い」と誤解させる。
            result["unreferenced_terms"] = (
                None
                if self.unreferenced_terms is None
                else list(self.unreferenced_terms[:max_terms])
            )
            result["without_owner_terms"] = (
                None
                if self.without_owner_terms is None
                else list(self.without_owner_terms[:max_terms])
            )
        return result


def compute_health(inputs: HealthInputs, *, namespace: str = "") -> HealthReport:
    """材料から健全性指標を計算する。"""
    approval_age: int | None = None
    if inputs.current_approved_at is not None:
        approval_age = (inputs.now - inputs.current_approved_at).days

    unreferenced: tuple[str, ...] | None = None
    without_owner: tuple[str, ...] | None = None
    term_count: int | None = None
    if inputs.terms is not None:
        term_count = len(inputs.terms)
        cutoff = inputs.now - timedelta(days=UNREFERENCED_WINDOW_DAYS)
        # **オントロジーに無い用語の参照・責任者は無視する。** `term_access` と
        # `term_owners` は過去の付与や外部 IRI を残すので(ADR-0015 決定3)、
        # 現在の版の用語一覧を分母にしないと割合が狂う。
        unreferenced = tuple(
            sorted(
                iri
                for iri in inputs.terms
                # `>=` にするのは、ちょうど窓の境界を「窓の内側」とするため。
                if not ((last := inputs.last_access.get(iri)) is not None and last >= cutoff)
            )
        )
        without_owner = tuple(sorted(inputs.terms - inputs.owned_terms))

    return HealthReport(
        namespace=namespace,
        measured_at=inputs.now,
        current_version=inputs.current_version,
        term_count=term_count,
        unreferenced_terms=unreferenced,
        without_owner_terms=without_owner,
        approval_age_days=approval_age,
        shacl_violation_count=inputs.shacl_violation_count,
        unprojected_version_count=inputs.unprojected_version_count,
        competency_question_count=inputs.competency_question_count,
        unavailable=inputs.unavailable,
    )
