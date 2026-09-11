"""監査照会のテスト(P2B-11、ADR-0009 決定7 / ADR-0006 §3)。

**中心にあるのは並び順とページングの正しさ**である。監査は「説明できること」の
土台なので、**取りこぼしと重複が起きてはいけない**。

`occurred_at` は `now()`(トランザクション開始時刻)なので、同一トランザクション
内の複数イベントは**同じ値になる**。時刻でページングすると境界で取りこぼす。
`id` だけが全順序で安定した鍵である。ここではその性質を固定する。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.routers.audit import query_audit
from ontology_core.auth.entra import Principal
from ontology_core.models import ActorType, NamespaceRole, PlatformRole

_NS = "audit-ns"
_OTHER_NS = "audit-other"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")

_ALICE = "alice-oid"
_BOB = "bob-oid"


async def _setup(session: AsyncSession) -> None:
    repo = NamespaceRepository(session)
    for name in (_NS, _OTHER_NS):
        await repo.create(
            name=name,
            display_name=name,
            description="",
            base_iri="https://e.example/#",
            created_by=_ADMIN.object_id,
        )
    await RoleRepository(session).grant(
        namespace=_NS,
        principal_id=_ANALYST.object_id,
        role=NamespaceRole.DATA_ANALYST,
        granted_by=_ADMIN.object_id,
    )
    await session.commit()


async def _record(
    session: AsyncSession,
    *,
    namespace: str = _NS,
    action: str,
    actor: str,
    subject: str,
    reason: str = "",
) -> None:
    """1 件記録して commit する。

    **1 件ずつ commit する。** `occurred_at` はトランザクション開始時刻なので、
    まとめて commit すると全件が同じ時刻になり、時刻での絞り込みを検証できない。
    """
    await AuditRepository(session).record(
        namespace=namespace,
        action=action,
        actor=actor,
        actor_type=ActorType.UNKNOWN,
        subject=subject,
        reason=reason,
    )
    await session.commit()


# ---------------------------------------------------------------- 基本


@pytest.mark.integration
async def test_新しい順に返る(session: AsyncSession) -> None:
    """運用者が見たいのは直近の出来事である。"""
    await _setup(session)
    for i in range(3):
        await _record(session, action=f"a{i}", actor=_ALICE, subject=f"s{i}")
    page = await query_audit(namespace=_NS, principal=_ANALYST, session=session)
    assert [e.action for e in page.events] == ["a2", "a1", "a0"]
    assert page.next_cursor is None


@pytest.mark.integration
async def test_他の名前空間のイベントは混ざらない(session: AsyncSession) -> None:
    """名前空間は隔離の境界である(不変条件5)。監査もその内側にある。"""
    await _setup(session)
    await _record(session, action="mine", actor=_ALICE, subject="s")
    await _record(session, namespace=_OTHER_NS, action="theirs", actor=_ALICE, subject="s")
    page = await query_audit(namespace=_NS, principal=_ANALYST, session=session)
    assert [e.action for e in page.events] == ["mine"]


@pytest.mark.integration
async def test_イベントが無ければ空のページを返す(session: AsyncSession) -> None:
    """**404 にしない。** 「まだ何も起きていない」はエラーではない。"""
    await _setup(session)
    page = await query_audit(namespace=_NS, principal=_ANALYST, session=session)
    assert page.events == ()
    assert page.next_cursor is None


# ---------------------------------------------------------------- ページング


@pytest.mark.integration
async def test_同一トランザクションのイベントもページングで取りこぼさない(
    session: AsyncSession,
) -> None:
    """**これが本題。**

    `occurred_at` は `now()`(トランザクション開始時刻)なので、まとめて記録した
    イベントは**全件が同じ時刻**になる。時刻でページングすると、境界の複数件を
    取りこぼすか二重に返す。`id` を鍵にしていればそれが起きない。
    """
    await _setup(session)
    repo = AuditRepository(session)
    for i in range(5):
        await repo.record(
            namespace=_NS,
            action=f"same{i}",
            actor=_ALICE,
            actor_type=ActorType.UNKNOWN,
            subject="s",
        )
    await session.commit()

    # 全件が同じ occurred_at であることを確かめてから、ページングを検証する。
    all_at_once = await query_audit(namespace=_NS, principal=_ANALYST, session=session)
    times = {e.occurred_at for e in all_at_once.events}
    assert len(times) == 1, "同一トランザクションなら occurred_at は 1 種類でなければならない"

    seen: list[str] = []
    cursor: int | None = None
    for _ in range(10):  # 無限ループにしない
        page = await query_audit(
            namespace=_NS, principal=_ANALYST, session=session, limit=2, cursor=cursor
        )
        seen.extend(e.action for e in page.events)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == ["same4", "same3", "same2", "same1", "same0"], (
        "取りこぼしも重複もあってはならない"
    )


@pytest.mark.integration
async def test_最後のページの_next_cursor_は_None(session: AsyncSession) -> None:
    """**件数が limit ちょうどでも None になる。** 「返った件数が limit より
    少ないから最後」という判定に呼び出し側を頼らせない。"""
    await _setup(session)
    for i in range(4):
        await _record(session, action=f"a{i}", actor=_ALICE, subject="s")
    page = await query_audit(namespace=_NS, principal=_ANALYST, session=session, limit=4)
    assert len(page.events) == 4
    assert page.next_cursor is None


@pytest.mark.integration
async def test_続きがあれば_next_cursor_が返る(session: AsyncSession) -> None:
    await _setup(session)
    for i in range(4):
        await _record(session, action=f"a{i}", actor=_ALICE, subject="s")
    page = await query_audit(namespace=_NS, principal=_ANALYST, session=session, limit=3)
    assert len(page.events) == 3
    assert page.next_cursor is not None


@pytest.mark.integration
async def test_ページングの途中で追記されても既読分は動かない(session: AsyncSession) -> None:
    """`audit_events` は追記専用である(ADR-0011 決定2)。新しい行は必ず
    大きい `id` を持つので、`id` の降順で辿っている限り既読のページは動かない。
    **offset でページングしていると、ここで 1 件ずつずれて取りこぼす。**
    """
    await _setup(session)
    for i in range(3):
        await _record(session, action=f"old{i}", actor=_ALICE, subject="s")
    first = await query_audit(namespace=_NS, principal=_ANALYST, session=session, limit=2)
    assert [e.action for e in first.events] == ["old2", "old1"]

    await _record(session, action="brand-new", actor=_BOB, subject="s")

    second = await query_audit(
        namespace=_NS, principal=_ANALYST, session=session, limit=2, cursor=first.next_cursor
    )
    assert [e.action for e in second.events] == ["old0"], (
        "追記された行が既読のページに割り込んで old0 を押し出してはいけない"
    )


@pytest.mark.integration
async def test_上限を超える_limit_は_422(session: AsyncSession) -> None:
    """`audit_events` は追記専用で無限に伸びる。上限が無いと 1 リクエストで
    全件を読み出せてしまう。"""
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await query_audit(
            namespace=_NS,
            principal=_ANALYST,
            session=session,
            limit=AuditRepository.MAX_LIMIT + 1,
        )
    assert exc.value.status_code == 422


@pytest.mark.integration
async def test_ゼロ以下の_limit_は_422(session: AsyncSession) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await query_audit(namespace=_NS, principal=_ANALYST, session=session, limit=0)
    assert exc.value.status_code == 422


# ---------------------------------------------------------------- 絞り込み


@pytest.mark.integration
async def test_実行者で絞り込める(session: AsyncSession) -> None:
    await _setup(session)
    await _record(session, action="by-alice", actor=_ALICE, subject="s")
    await _record(session, action="by-bob", actor=_BOB, subject="s")
    page = await query_audit(namespace=_NS, principal=_ANALYST, session=session, actor=_ALICE)
    assert [e.action for e in page.events] == ["by-alice"]


@pytest.mark.integration
async def test_操作で絞り込める(session: AsyncSession) -> None:
    await _setup(session)
    await _record(session, action="approved", actor=_ALICE, subject="s1")
    await _record(session, action="rejected", actor=_ALICE, subject="s2")
    page = await query_audit(namespace=_NS, principal=_ANALYST, session=session, action="approved")
    assert [e.subject for e in page.events] == ["s1"]


@pytest.mark.integration
async def test_対象で絞り込める(session: AsyncSession) -> None:
    await _setup(session)
    await _record(session, action="a", actor=_ALICE, subject="audit-ns@1.0.0")
    await _record(session, action="b", actor=_ALICE, subject="audit-ns@2.0.0")
    page = await query_audit(
        namespace=_NS, principal=_ANALYST, session=session, subject="audit-ns@1.0.0"
    )
    assert [e.action for e in page.events] == ["a"]


@pytest.mark.integration
async def test_絞り込みを組み合わせられる(session: AsyncSession) -> None:
    await _setup(session)
    await _record(session, action="approved", actor=_ALICE, subject="s")
    await _record(session, action="approved", actor=_BOB, subject="s")
    await _record(session, action="rejected", actor=_ALICE, subject="s")
    page = await query_audit(
        namespace=_NS, principal=_ANALYST, session=session, action="approved", actor=_ALICE
    )
    assert len(page.events) == 1
    assert page.events[0].actor == _ALICE


@pytest.mark.integration
async def test_期間で絞り込める(session: AsyncSession) -> None:
    """`since` は含み、`until` は含まない(半開区間)。

    **境界を両側とも含めると、期間を並べて集計したときに二重に数える。**

    **境界は DB から読み戻した時刻で作る。** `occurred_at` は PostgreSQL の
    `now()`(サーバ側の時計)なので、**`datetime.now(UTC)`(アプリ側の時計)を
    境界に使うとずれて間欠的に落ちる**(実際に 3 回に 1 回落ちていた。
    docker の中と外で時計が一致しない)。2 件の間の中点を使う。
    """
    await _setup(session)
    await _record(session, action="old", actor=_ALICE, subject="s")
    await _record(session, action="new", actor=_ALICE, subject="s")

    recorded = await query_audit(namespace=_NS, principal=_ANALYST, session=session)
    times = {e.action: e.occurred_at for e in recorded.events}
    assert times["old"] < times["new"], (
        "2 件が同じ時刻に記録された。1 件ずつ commit しているので通常は起きない"
        "(起きたらこのテストの前提が壊れている)"
    )
    boundary = times["old"] + (times["new"] - times["old"]) / 2

    after = await query_audit(namespace=_NS, principal=_ANALYST, session=session, since=boundary)
    assert [e.action for e in after.events] == ["new"]

    before = await query_audit(namespace=_NS, principal=_ANALYST, session=session, until=boundary)
    assert [e.action for e in before.events] == ["old"]


@pytest.mark.integration
async def test_境界ちょうどのイベントは_since_に含まれ_until_に含まれない(
    session: AsyncSession,
) -> None:
    """**半開区間 `[since, until)` をここで固定する。**

    前のテストは境界を 2 つのイベントの「間」に置くので、`until` を含むか
    含まないかを区別できない(実際に、`until` を `<=` に変えても落ちなかった)。
    **記録されたイベントの `occurred_at` を読み戻して、それをそのまま境界に
    使う**ことで、境界ちょうどの振る舞いを検証する。

    境界を両側とも含めると、期間を並べて集計したときに二重に数える。
    """
    await _setup(session)
    await _record(session, action="target", actor=_ALICE, subject="s")
    recorded = await query_audit(namespace=_NS, principal=_ANALYST, session=session)
    boundary = recorded.events[0].occurred_at

    included = await query_audit(namespace=_NS, principal=_ANALYST, session=session, since=boundary)
    assert [e.action for e in included.events] == ["target"], "since はその時刻を含む"

    excluded = await query_audit(namespace=_NS, principal=_ANALYST, session=session, until=boundary)
    assert excluded.events == (), "until はその時刻を含まない"


@pytest.mark.integration
async def test_タイムゾーンの無い日時は_422(session: AsyncSession) -> None:
    """**素朴に UTC と解釈しない。** 監査の照会で 9 時間ずれた結果を返すのは、
    「何も返らない」よりたちが悪い(誤った結論の根拠になる)。
    """
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await query_audit(
            namespace=_NS,
            principal=_ANALYST,
            session=session,
            # タイムゾーン無しの日時を意図的に渡す。
            since=datetime(2026, 1, 1),
        )
    assert exc.value.status_code == 422


@pytest.mark.integration
async def test_until_が_since_より前なら_422(session: AsyncSession) -> None:
    """空の結果を返すより、指定の誤りとして伝える。空だと「本当に何も無い」と
    誤解させる。"""
    await _setup(session)
    now = datetime.now(UTC)
    with pytest.raises(HTTPException) as exc:
        await query_audit(
            namespace=_NS,
            principal=_ANALYST,
            session=session,
            since=now,
            until=now - timedelta(hours=1),
        )
    assert exc.value.status_code == 422


# ---------------------------------------------------------------- 権限


@pytest.mark.integration
async def test_data_analyst_が読める(session: AsyncSession) -> None:
    """**`owner` を要求しない。** 版単位の決定記録(`decisions`)は既に
    `data-analyst` で読めるので、名前空間全体の照会だけを絞っても、版を
    列挙して同じ情報を集められる。**足し合わせれば見えるものを、集約した
    ときだけ隠すのは見せかけの制限である。**
    """
    await _setup(session)
    await _record(session, action="a", actor=_ALICE, subject="s")
    page = await query_audit(namespace=_NS, principal=_ANALYST, session=session)
    assert len(page.events) == 1


@pytest.mark.integration
async def test_ロールを持たない主体は読めない(session: AsyncSession) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await query_audit(namespace=_NS, principal=_STRANGER, session=session)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_存在しない名前空間は_404(session: AsyncSession) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await query_audit(namespace="no-such-ns", principal=_ADMIN, session=session)
    assert exc.value.status_code == 404
