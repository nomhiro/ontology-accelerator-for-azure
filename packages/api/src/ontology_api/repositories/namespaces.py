"""名前空間の永続化。

名前空間名は Fuseki のデータセット名になるため、**DB に触る前に**検証する。
"""

from __future__ import annotations

from sqlalchemy import select
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
    ) -> Namespace:
        """名前空間を作る。

        Raises:
            NamespaceNameError: 名前が不正なとき。
            NamespaceExistsError: 既に存在するとき。
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
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _to_model(row)

    async def get(self, name: str) -> Namespace | None:
        row = await self._session.get(NamespaceRow, name)
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
