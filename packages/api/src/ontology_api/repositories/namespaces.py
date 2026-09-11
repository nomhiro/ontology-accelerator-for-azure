"""名前空間の永続化。

名前空間名は Fuseki のデータセット名になるため、**DB に触る前に**検証する。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.db import NamespaceRow
from ontology_core.graphs import validate_namespace_name
from ontology_core.models import Namespace

__all__ = ["NamespaceExistsError", "NamespaceRepository"]


class NamespaceExistsError(Exception):
    """同名の名前空間が既に存在することを表す。"""


def _to_model(row: NamespaceRow) -> Namespace:
    return Namespace(
        name=row.name,
        display_name=row.display_name,
        description=row.description,
        base_iri=row.base_iri,
        created_at=row.created_at,
        created_by=row.created_by,
        require_two_person_approval=row.require_two_person_approval,
    )


class NamespaceRepository:
    """名前空間テーブルへのアクセス。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        name: str,
        display_name: str,
        description: str,
        base_iri: str,
        created_by: str,
        require_two_person_approval: bool = True,
    ) -> Namespace:
        """名前空間を作る。

        事前に `get()` で存在確認するが、これは check-then-insert であり同時に
        同名で作成要求が来た場合は両方が確認を通過し得る(競合)。その場合は
        後から `flush()` する側が一意制約違反の `IntegrityError` になるため、
        それを最後の砦として捕捉し `NamespaceExistsError` に変換する。

        Raises:
            NamespaceNameError: 名前が不正なとき。
            NamespaceExistsError: 既に存在するとき(事前チェック、または
                競合による一意制約違反)。
        """
        validate_namespace_name(name)
        if await self.get(name) is not None:
            raise NamespaceExistsError(f"名前空間 '{name}' は既に存在します")

        row = NamespaceRow(
            name=name,
            display_name=display_name,
            description=description,
            base_iri=base_iri,
            created_by=created_by,
            require_two_person_approval=require_two_person_approval,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise NamespaceExistsError(f"名前空間 '{name}' は既に存在します") from exc
        await self._session.refresh(row)
        return _to_model(row)

    async def get(self, name: str) -> Namespace | None:
        row = await self._session.get(NamespaceRow, name)
        return _to_model(row) if row is not None else None

    async def get_locked(self, name: str) -> Namespace | None:
        """行ロック(`SELECT ... FOR UPDATE`)を取ってから返す(ADR-0024 決定1)。

        **Blob に `.ttl` を書く経路と、名前空間を削除する経路が、これを使う。**
        ロックはトランザクションの終わり(`commit` / `rollback`)まで保持される。

        これが無いと、削除の「Blob は空か」の確認と PostgreSQL の行の削除の間に
        `publish` が入り込み、**Blob に TTL があって PostgreSQL には何も無い**
        状態ができる。ローダは PostgreSQL を見ず Blob だけを見て再構築するので、
        **削除したはずの名前空間が次のレプリカ再作成で復活する**(ADR-0024)。

        **「後から Blob を再検査する」では閉じられない。** publish が Blob を
        書く前に削除が commit してしまう順序が残る(決定2)。

        `get` と別のメソッドにしているのは、**呼び出し側でロックを取ったことが
        読めるようにするため**である。既定でロックする実装にすると、
        一覧や参照の経路まで直列化してしまう。

        **SQLAlchemy は SQLite に `FOR UPDATE` を出さない。** テストも本番も
        PostgreSQL なので実害は無いが、SQLite に戻すならこの排他は成立しない。
        """
        stmt = (
            select(NamespaceRow)
            .where(NamespaceRow.name == name)
            .with_for_update()
            # 同一セッションで既に読んでいた行を返さず、ロック付きで読み直す。
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_model(row) if row is not None else None

    async def list_all(self) -> list[Namespace]:
        result = await self._session.execute(select(NamespaceRow).order_by(NamespaceRow.name))
        return [_to_model(row) for row in result.scalars()]

    async def delete(self, name: str) -> bool:
        """削除する。存在しなければ False を返す。"""
        row = await self._session.get(NamespaceRow, name)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
