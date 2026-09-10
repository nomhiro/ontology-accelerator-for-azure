"""保持ポリシー(ADR-0019、`P2B-02`)。

## 判断はここ 1 箇所にある

「どの版をストアに載せるか」を決めるのはこのモジュールだけである。ローダは
マニフェストに載った**判断済みの結果**を解釈するだけで、状態を再判定しない
(ADR-0019 決定1)。

以前は判断がローダの `projection_targets`(シェル)にあり、`reconcile` は
食い違いを避けるために `superseded` の在否を**どちらも不問**にしていた。
その結果**保持ポリシーを誰も強制していなかった**。判断を 1 箇所に集めれば、
降りる必要が無くなる。

## `SUPERSEDED_RETAIN` は個数である

以前のローダの実装は `0` 以外なら**全部載せていた**。`SUPERSEDED_RETAIN=2` と
書いた運用者は「直近 2 版を残す」と読むので、静かに期待と違う結果になって
いた(ADR-0019 問題1)。

## 「直近」は `approved_at` の降順

**バージョン文字列で並べない。** `validate_version` は英字と記号を広く許す
(`1.0.0-rc1`、`2026-09`)ので、文字列順は「直近」を意味しない。`superseded` の
版はかつて `approved` だったので `approved_at` を持つ。

**`approved_at` が NULL の版は最も古いものとして扱う。** NULL を「新しい」と
解釈すると、**古い版が残って新しい版が落ちる**という最も分かりにくい壊れ方に
なる。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from ontology_core.models import OntologyVersion, OntologyVersionStatus

__all__ = ["ProjectionTarget", "decide_projection"]

# `approved_at` が無い版を最も古いものとして並べるための番兵。
_OLDEST = datetime.min.replace(tzinfo=UTC)


class ProjectionTarget(StrEnum):
    """版をストアのどこへ載せるか。

    **値はローダが解釈する語彙そのものである。** マニフェストにこの文字列を
    載せ、ローダは文字列全体の一致で見分ける(状態を再判定しない)。
    """

    NAMED_AND_DEFAULT = "named default"
    NAMED = "named"
    SKIP_DRAFT = "skip:draft"
    SKIP_REJECTED = "skip:rejected"
    SKIP_BEYOND_RETAIN = "skip:superseded-beyond-retain"

    @property
    def loads_named(self) -> bool:
        """名前付きグラフに載るか。"""
        return self in (ProjectionTarget.NAMED_AND_DEFAULT, ProjectionTarget.NAMED)

    @property
    def loads_default(self) -> bool:
        """既定グラフに載るか。

        **エージェントが読むのは既定グラフである**(ADR-0010 決定6)ので、
        ここに載る版は常にちょうど 1 つでなければならない(`P1-C1`)。
        """
        return self is ProjectionTarget.NAMED_AND_DEFAULT


def _sort_key(version: OntologyVersion) -> tuple[datetime, str]:
    """`approved_at` の降順で並べるための鍵。

    第二鍵にバージョン文字列を置くのは、`approved_at` が同じ版があっても
    **決定が呼ぶたびに変わらない**ようにするため。`now()` はトランザクション
    開始時刻なので、同一トランザクションで複数の版が同じ値を持ちうる。
    """
    return (version.approved_at or _OLDEST, version.version)


def decide_projection(
    versions: list[OntologyVersion], *, retain_superseded: int
) -> dict[str, ProjectionTarget]:
    """各版の射影先を決める(ADR-0019 決定1・2・3)。

    Args:
        versions: その名前空間の全バージョン。
        retain_superseded: 名前付きグラフに残す `superseded` の**個数**。
            **負の値は 0 として扱う**(設定の誤りで「全部載る」に倒れない。
            安全側は載せない側である)。

    Returns:
        バージョン文字列から射影先への対応。
    """
    retain = max(0, retain_superseded)
    decided: dict[str, ProjectionTarget] = {}

    # 現行版は `approved` のうち `approved_at` が最も新しいもの。
    # **`approved` が 2 つある状態は本来起こらない**が、起きたときに既定
    # グラフを汚さないよう、ここで 1 つに決める。
    approved = sorted(
        (v for v in versions if v.status is OntologyVersionStatus.APPROVED),
        key=_sort_key,
        reverse=True,
    )
    current = approved[0].version if approved else None

    # `superseded` を直近から数える。
    retained = {
        v.version
        for v in sorted(
            (v for v in versions if v.status is OntologyVersionStatus.SUPERSEDED),
            key=_sort_key,
            reverse=True,
        )[:retain]
    }

    for version in versions:
        match version.status:
            case OntologyVersionStatus.APPROVED:
                decided[version.version] = (
                    ProjectionTarget.NAMED_AND_DEFAULT
                    if version.version == current
                    else ProjectionTarget.NAMED
                )
            case OntologyVersionStatus.IN_REVIEW:
                decided[version.version] = ProjectionTarget.NAMED
            case OntologyVersionStatus.SUPERSEDED:
                decided[version.version] = (
                    ProjectionTarget.NAMED
                    if version.version in retained
                    else ProjectionTarget.SKIP_BEYOND_RETAIN
                )
            case OntologyVersionStatus.DRAFT:
                decided[version.version] = ProjectionTarget.SKIP_DRAFT
            case OntologyVersionStatus.REJECTED:
                decided[version.version] = ProjectionTarget.SKIP_REJECTED
    return decided
