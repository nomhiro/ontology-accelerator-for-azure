"""保持ポリシーのテスト(P2B-02、ADR-0019)。

**要点は 3 つ。**

1. **`SUPERSEDED_RETAIN` は個数である**(決定2)。真偽値ではない。
   「2 と書いたのに全部載る」を終わらせる
2. **「直近」は `approved_at` の降順で決める**(決定3)。バージョン文字列では
   並べない(`1.0.0-rc1` や `2026-09` を許すので文字列順は「直近」を意味しない)
3. **`approved_at` が NULL の版は最も古いものとして扱う**(決定3)。NULL を
   「新しい」と解釈すると、**古い版が残って新しい版が落ちる**という最も
   分かりにくい壊れ方になる
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ontology_core.models import OntologyVersion, OntologyVersionStatus
from ontology_core.retention import ProjectionTarget, decide_projection

_NOW = datetime(2026, 9, 10, tzinfo=UTC)


def _version(
    version: str,
    status: OntologyVersionStatus,
    *,
    approved_at: datetime | None = None,
) -> OntologyVersion:
    return OntologyVersion(
        namespace="ns",
        version=version,
        content_hash="h" * 64,
        status=status,
        graph_iri=f"urn:ontology:graph/ns/{version}",
        blob_path=f"versions/ns/{version}.ttl",
        created_at=_NOW,
        created_by="t",
        approved_at=approved_at,
    )


def _targets(versions: list[OntologyVersion], *, retain: int) -> dict[str, str]:
    return {v: t.value for v, t in decide_projection(versions, retain_superseded=retain).items()}


# ---------------------------------------------------------------- 状態ごとの既定


def test_承認済みの現行版は既定グラフと名前付きグラフへ() -> None:
    versions = [_version("1.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW)]
    assert _targets(versions, retain=0) == {"1.0.0": "named default"}


def test_in_review_は名前付きグラフのみ() -> None:
    versions = [_version("2.0.0", OntologyVersionStatus.IN_REVIEW)]
    assert _targets(versions, retain=0) == {"2.0.0": "named"}


def test_draft_は載せない() -> None:
    """`draft` は Blob と PostgreSQL にのみ存在する(ADR-0010 決定5)。"""
    versions = [_version("3.0.0", OntologyVersionStatus.DRAFT)]
    assert _targets(versions, retain=0) == {"3.0.0": "skip:draft"}


def test_rejected_は載せない() -> None:
    versions = [_version("4.0.0", OntologyVersionStatus.REJECTED)]
    assert _targets(versions, retain=0) == {"4.0.0": "skip:rejected"}


def test_approved_が_2_つあっても既定グラフは_1_つだけ() -> None:
    """**本来起こらないが、起きたときに既定グラフを汚さない。**

    `approved_at` が新しいほうを現行とする。
    """
    versions = [
        _version("1.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW - timedelta(days=1)),
        _version("2.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW),
    ]
    result = _targets(versions, retain=0)
    assert result["2.0.0"] == "named default"
    assert result["1.0.0"] == "named"


# ---------------------------------------------------------------- 保持の個数


def test_retain_0_なら_superseded_を載せない() -> None:
    versions = [
        _version("2.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW),
        _version("1.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=_NOW - timedelta(days=1)),
    ]
    assert _targets(versions, retain=0) == {
        "2.0.0": "named default",
        "1.0.0": "skip:superseded-beyond-retain",
    }


def test_retain_1_なら直近_1_版だけ載せる() -> None:
    """**これが問題1 の修正である。** 以前は 0 以外なら全部載っていた。"""
    versions = [
        _version("3.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW),
        _version("2.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=_NOW - timedelta(days=1)),
        _version("1.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=_NOW - timedelta(days=2)),
    ]
    assert _targets(versions, retain=1) == {
        "3.0.0": "named default",
        "2.0.0": "named",
        "1.0.0": "skip:superseded-beyond-retain",
    }


def test_retain_2_なら直近_2_版を載せる() -> None:
    versions = [
        _version("4.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW),
        _version("3.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=_NOW - timedelta(days=1)),
        _version("2.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=_NOW - timedelta(days=2)),
        _version("1.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=_NOW - timedelta(days=3)),
    ]
    result = _targets(versions, retain=2)
    assert result["3.0.0"] == "named"
    assert result["2.0.0"] == "named"
    assert result["1.0.0"] == "skip:superseded-beyond-retain"


def test_retain_が版数以上なら全部載せる() -> None:
    versions = [
        _version("2.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW),
        _version("1.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=_NOW - timedelta(days=1)),
    ]
    assert _targets(versions, retain=99)["1.0.0"] == "named"


def test_負の_retain_は_0_として扱う() -> None:
    """設定の誤りで「全部載る」に倒れないこと。**安全側は載せない側**である。

    **版を 6 つ用意するのが要点。** `max(0, ...)` を外すと `[:-5]` という
    スライスになり、Python では**末尾から 5 件を落とす**という意味になる。
    版が 1 つだけだと結果が空になって偶然通ってしまう(変異テストで見逃した)。
    版が 6 つあれば、誤った実装は 1 件を残してしまう。
    """
    versions = [_version("9.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW)]
    versions += [
        _version(
            f"{i}.0.0",
            OntologyVersionStatus.SUPERSEDED,
            approved_at=_NOW - timedelta(days=i + 1),
        )
        for i in range(6)
    ]
    result = _targets(versions, retain=-5)
    superseded = {v: t for v, t in result.items() if v != "9.0.0"}
    assert set(superseded.values()) == {"skip:superseded-beyond-retain"}, (
        f"負の retain で 1 件でも載ってはいけない: {superseded}"
    )


# ---------------------------------------------------------------- 順序


def test_順序はバージョン文字列ではなく_approved_at_で決まる() -> None:
    """**文字列順で並べてはいけない。** `validate_version` は英字と記号を
    広く許すので、文字列順は「直近」を意味しない。

    ここでは文字列順と `approved_at` 順が**逆になる**ように仕込んである。
    """
    versions = [
        _version("9.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW),
        # 文字列順では "1.0.0" < "2.0.0" だが、承認は "1.0.0" のほうが新しい。
        _version("1.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=_NOW - timedelta(days=1)),
        _version("2.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=_NOW - timedelta(days=5)),
    ]
    result = _targets(versions, retain=1)
    assert result["1.0.0"] == "named", "承認が新しい版を残す"
    assert result["2.0.0"] == "skip:superseded-beyond-retain"


def test_approved_at_が_NULL_の版は最も古いものとして扱う() -> None:
    """**NULL を「新しい」と解釈すると、古い版が残って新しい版が落ちる**
    という最も分かりにくい壊れ方になる(決定3)。
    """
    versions = [
        _version("3.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW),
        _version("2.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=_NOW - timedelta(days=1)),
        _version("1.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=None),
    ]
    result = _targets(versions, retain=1)
    assert result["2.0.0"] == "named", "approved_at がある版を優先する"
    assert result["1.0.0"] == "skip:superseded-beyond-retain"


def test_同じ_approved_at_でも入力の順序に依存しない() -> None:
    """`now()` はトランザクション開始時刻なので、同一トランザクションで
    複数の版が同じ値を持ちうる。**そのとき DB が返す行の順序で決定が
    変わってはいけない。**

    **同じリストを繰り返し渡すだけでは足りない**(`sorted` は安定なので
    入力順がそのまま保たれ、第二鍵が無くても通る。変異テストで見逃した)。
    **入力の順序を変えて**同じ結果になることを確認する。
    """
    same = _NOW - timedelta(days=1)
    a = _version("1.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=same)
    b = _version("2.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=same)
    current = _version("3.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW)

    forward = _targets([current, a, b], retain=1)
    backward = _targets([current, b, a], retain=1)
    assert forward == backward, f"入力の順序で決定が変わってはいけない: {forward} と {backward}"


# ---------------------------------------------------------------- 全体


def test_版が無ければ空() -> None:
    assert decide_projection([], retain_superseded=0) == {}


def test_現行版が無くても_in_review_は載る() -> None:
    """最初の submit の時点では `approved` が無い。"""
    versions = [_version("1.0.0", OntologyVersionStatus.IN_REVIEW)]
    assert _targets(versions, retain=0) == {"1.0.0": "named"}


def test_戻り値の型は_ProjectionTarget() -> None:
    versions = [_version("1.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW)]
    decided = decide_projection(versions, retain_superseded=0)
    assert decided["1.0.0"] is ProjectionTarget.NAMED_AND_DEFAULT


def test_載せる版と載せない版を判定できる() -> None:
    versions = [
        _version("2.0.0", OntologyVersionStatus.APPROVED, approved_at=_NOW),
        _version("1.0.0", OntologyVersionStatus.SUPERSEDED, approved_at=_NOW - timedelta(days=1)),
    ]
    decided = decide_projection(versions, retain_superseded=0)
    assert decided["2.0.0"].loads_named is True
    assert decided["2.0.0"].loads_default is True
    assert decided["1.0.0"].loads_named is False
    assert decided["1.0.0"].loads_default is False
