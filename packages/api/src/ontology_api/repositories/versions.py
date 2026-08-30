"""オントロジーのバージョンと監査イベントの永続化。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.db import AuditEventRow, OntologyVersionRow
from ontology_core.models import OntologyVersion, OntologyVersionStatus

__all__ = ["AuditRepository", "VersionRepository"]


def _to_model(row: OntologyVersionRow) -> OntologyVersion:
    return OntologyVersion(
        namespace=row.namespace,
        version=row.version,
        content_hash=row.content_hash,
        status=OntologyVersionStatus(row.status),
        graph_iri=row.graph_iri,
        blob_path=row.blob_path,
        created_at=row.created_at,
        created_by=row.created_by,
        approved_at=row.approved_at,
        approved_by=row.approved_by,
        projected_at=row.projected_at,
    )


class VersionRepository:
    """`ontology_versions` へのアクセス。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        namespace: str,
        version: str,
        content_hash: str,
        graph_iri: str,
        blob_path: str,
        created_by: str,
        status: OntologyVersionStatus,
    ) -> OntologyVersion:
        row = OntologyVersionRow(
            namespace=namespace,
            version=version,
            content_hash=content_hash,
            graph_iri=graph_iri,
            blob_path=blob_path,
            created_by=created_by,
            status=status.value,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _to_model(row)

    async def find_by_hash(self, namespace: str, content_hash: str) -> OntologyVersion | None:
        stmt = select(OntologyVersionRow).where(
            OntologyVersionRow.namespace == namespace,
            OntologyVersionRow.content_hash == content_hash,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_model(row) if row is not None else None

    async def list_for(self, namespace: str) -> list[OntologyVersion]:
        stmt = (
            select(OntologyVersionRow)
            .where(OntologyVersionRow.namespace == namespace)
            .order_by(OntologyVersionRow.id)
        )
        return [_to_model(r) for r in (await self._session.execute(stmt)).scalars()]

    async def latest_for(self, namespace: str) -> OntologyVersion | None:
        stmt = (
            select(OntologyVersionRow)
            .where(OntologyVersionRow.namespace == namespace)
            .order_by(OntologyVersionRow.id.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_model(row) if row is not None else None

    async def mark_projected(self, namespace: str, version: str) -> None:
        stmt = select(OntologyVersionRow).where(
            OntologyVersionRow.namespace == namespace,
            OntologyVersionRow.version == version,
        )
        row = (await self._session.execute(stmt)).scalar_one()
        row.projected_at = datetime.now(UTC)
        await self._session.flush()

    async def unprojected(self) -> list[OntologyVersion]:
        """まだ射影されていないバージョン。reconcile の対象。"""
        stmt = select(OntologyVersionRow).where(OntologyVersionRow.projected_at.is_(None))
        return [_to_model(r) for r in (await self._session.execute(stmt)).scalars()]


class AuditRepository:
    """`audit_events` へのアクセス。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self, *, namespace: str, action: str, actor: str, subject: str, reason: str = ""
    ) -> None:
        self._session.add(
            AuditEventRow(
                namespace=namespace,
                action=action,
                actor=actor,
                subject=subject,
                reason=reason,
            )
        )
        await self._session.flush()
