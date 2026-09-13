"""識別子の並び順が照合順序に依存しないことを固定する(`P3-12`)。

## なぜこのファイルが要るのか

**ローカルと CI とデプロイ環境で `lc_collate` が違っていた。**

| どこ | `lc_collate` |
|---|---|
| かつてのローカル/CI(`postgres:16-alpine`、musl) | **`C`**(バイト順) |
| いまのローカル/CI(`pgvector/pgvector:pg16`、Debian) | `en_US.utf8` |
| **Azure Database for PostgreSQL** | **`en_US.utf8`**(読み取り専用) |

`P3-02` でイメージを替えたときに `test_term_owners.py` の並び順のテストが
落ちて気づいた。**イメージの差し替えが壊したのではなく、既にあった
食い違いを見えるようにした** — 落ちたテストは**本番では成り立たない
順序を主張していた**。

**`en_US.utf8` は第一水準で句読点を無視する**ので、`://` の位置が効かない。
題材はその差がいちばん分かる形にしてある(`http://` と `https://`)。

**このファイルは実物の PostgreSQL を要求する。** 照合順序は DB の機能で、
フェイクでは再現しない(そもそも再現しなかったから見落としていた)。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.term_owners import TermOwnerRepository
from ontology_core.models import NamespaceRole

#: 検査の対象になるリポジトリ群。
_REPOSITORIES = Path(__file__).resolve().parents[1] / "src/ontology_api/repositories"

#: **句読点の位置だけが違う IRI。** `en_US.utf8` と `C` で順序が逆になる。
_IRIS = (
    "http://www.w3.org/2004/02/skos/core#Concept",
    "https://e.example/#Customer",
    "https://e.example/#Product",
)

#: **ハイフンを含む名前。** 名前空間名は `[a-z0-9-]` なので該当する。
_NAMES = ("ord-aa", "ord-a-b", "ord-ab", "ord-a-c")


async def test_この_DB_の照合順序は_en_US_である(session: AsyncSession) -> None:
    """**前提の確認。** Azure と同じ照合順序でテストしていることを固定する。

    `C` に戻ると、この下のテストが**通ってしまう**(実装が壊れていても)。
    だから照合順序そのものを先に固定する。
    """
    result = await session.execute(
        text("SELECT datcollate FROM pg_database WHERE datname = current_database()")
    )
    assert str(result.scalar()) == "en_US.utf8", (
        "ローカルの PostgreSQL の照合順序が Azure(en_US.utf8)と違う。"
        "docker-compose.yml のイメージを確かめること"
    )


async def test_素の_ORDER_BY_は_Python_の並びと一致しない(session: AsyncSession) -> None:
    """**この差がこのファイルの存在理由である。**

    実装ではなく **PostgreSQL の挙動**を固定している。ここが「一致する」に
    変わったら、照合順序が変わったということである。
    """
    values = ", ".join(f"('{iri}')" for iri in _IRIS)
    result = await session.execute(
        text(f"WITH s(v) AS (VALUES {values}) SELECT v FROM s ORDER BY v")
    )
    database_order = [str(row[0]) for row in result.all()]
    assert database_order != sorted(_IRIS), (
        "en_US.utf8 でバイト順と一致するなら、by_identifier は不要である"
    )
    # **C を指定すれば一致する。**
    result = await session.execute(
        text(f'WITH s(v) AS (VALUES {values}) SELECT v FROM s ORDER BY v COLLATE "C"')
    )
    assert [str(row[0]) for row in result.all()] == sorted(_IRIS)


async def test_用語の責任者の一覧はバイト順で返る(session: AsyncSession) -> None:
    """**クライアントが自分で並べ替えた結果と一致する。**

    Python の `sorted()` と JavaScript の `Array.sort()` はどちらも
    コードポイント順である。
    """
    await _namespace(session, "ord-owners")
    repository = TermOwnerRepository(session)
    for iri in _IRIS:
        await repository.assign(
            namespace="ord-owners", term_iri=iri, principal_id="expert-oid", assigned_by="tester"
        )
    await session.commit()

    owners = await repository.list_for("ord-owners")

    assert [owner.term_iri for owner in owners] == sorted(_IRIS)


async def test_名前空間の一覧もバイト順で返る(session: AsyncSession) -> None:
    """**ハイフンを含む名前でも環境に依存しない。**"""
    for name in _NAMES:
        await _namespace(session, name)
    await session.commit()

    listed = [row.name for row in await NamespaceRepository(session).list_all()]

    assert [name for name in listed if name.startswith("ord-")] == sorted(_NAMES)


async def test_ロールの一覧もバイト順で返る(session: AsyncSession) -> None:
    """プリンシパル ID も識別子である(人間向けの辞書順に並べる理由が無い)。"""
    await _namespace(session, "ord-roles")
    repository = RoleRepository(session)
    principals = ("p-aa", "p-a-b", "p-ab")
    for principal in principals:
        await repository.grant(
            namespace="ord-roles",
            principal_id=principal,
            role=NamespaceRole.DATA_ANALYST,
            granted_by="tester",
        )
    await session.commit()

    listed = [row.principal_id for row in await repository.list_for("ord-roles")]

    assert listed == sorted(principals)


def test_人間が読むテキストには使っていない() -> None:
    """**識別子専用の道具である。**

    ラベルや説明をバイト順に並べると**日本語の並びが壊れる**。
    `by_identifier` が説明やラベルの列に付いていないことを機械的に確かめる。

    **同期の関数にしてある。** 非同期の中で `pathlib` を触ると静的解析が
    ASYNC240 を出す(イベントループを塞ぐため)。この検査に DB は要らない。
    """
    banned = ("display_name", "description", "label", "comment", "reason", "source_text")
    root = _REPOSITORIES
    assert root.exists(), f"リポジトリのディレクトリが見つかりません: {root}"
    for path in root.glob("*.py"):
        body = path.read_text(encoding="utf-8").replace(" ", "")
        for column in banned:
            assert f"by_identifier({column}" not in body, (
                f"{path.name}: 人間が読むテキスト({column})にバイト順を当てている"
            )


async def _namespace(session: AsyncSession, name: str) -> None:
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri="https://e.example/ord#",
        created_by="tester",
    )
    await session.commit()
