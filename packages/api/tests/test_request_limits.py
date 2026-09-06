"""投入サイズの上限と、マイグレーション状態の整合に対するテスト。

どちらも「静かに困る」型の問題を防ぐためのもので、機能そのもののテストではない。
"""

from __future__ import annotations

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.routers.versions import MAX_TURTLE_LENGTH, PublishRequest

# ---- P1-20: TTL のサイズ上限 ----
#
# rdflib の解析コストは約 1.0 秒/MB。上限は「1 リクエストが占有し得る時間」を
# 決める値なので、実オントロジーの規模に対して過大にしない。


def test_turtle_length_limit_is_not_larger_than_five_million() -> None:
    """上限を引き上げる変更に気づけるようにする。

    これを上げると 1 リクエストのスレッド占有時間がそのまま伸びる。
    大きな入力が必要になったら、上限ではなく投入経路(非同期ジョブ)を変える。
    """
    assert MAX_TURTLE_LENGTH <= 5_000_000


def test_turtle_at_the_limit_is_accepted() -> None:
    body = "#" + "x" * (MAX_TURTLE_LENGTH - 1)
    assert len(PublishRequest(turtle=body).turtle) == MAX_TURTLE_LENGTH


def test_turtle_over_the_limit_is_rejected_before_parsing() -> None:
    """上限超過はスキーマの検証で落ちる(rdflib に渡る前)。

    ここを通してしまうと、解析に入ってからスレッドを占有する。
    """
    body = "#" + "x" * MAX_TURTLE_LENGTH
    try:
        PublishRequest(turtle=body)
    except ValidationError as exc:
        assert "too_long" in str(exc) or "at most" in str(exc)
    else:
        raise AssertionError("上限を超える本文が受理されてしまった")


# ---- P1-23: テスト後に just migrate が失敗しない ----


async def test_session_fixture_stamps_alembic_version(
    session: AsyncSession, alembic_head: str
) -> None:
    """フィクスチャが `alembic_version` を head で stamp すること。

    **これが無いと、テストを実行した後の `just migrate` が
    DuplicateTableError で失敗する。** テーブルは `create_all` で出来ている
    のに alembic は「まだ何も適用されていない」と判断し `CREATE TABLE` から
    やり直そうとするため。「テストを回してから just migrate」という自然な
    順序で貢献者が踏む。
    """
    result = await session.execute(sa.text("SELECT version_num FROM alembic_version"))
    stamped = [row[0] for row in result]
    assert stamped == [alembic_head]
