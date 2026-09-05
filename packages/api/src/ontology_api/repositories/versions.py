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

    async def get(self, namespace: str, version: str) -> OntologyVersion | None:
        stmt = select(OntologyVersionRow).where(
            OntologyVersionRow.namespace == namespace,
            OntologyVersionRow.version == version,
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

    async def set_status(
        self,
        namespace: str,
        version: str,
        *,
        status: OntologyVersionStatus,
        approved_by: str | None = None,
        approved_at: datetime | None = None,
        reset_projected: bool = False,
    ) -> OntologyVersion:
        """状態遷移(submit/approve/reject/supersede)をまとめて書く。

        `reset_projected=True` は「この行に対応する射影(Fuseki への反映)を
        やり直す必要がある」という意味で `projected_at` を NULL に戻す。
        これを忘れると、遷移後の射影(名前付きグラフ・既定グラフの更新)が
        失敗しても `unprojected()` がその行を拾えず、`reconcile` が永久に
        回収できなくなる(状態遷移のたびに「射影済み」の意味が変わるため)。
        """
        stmt = select(OntologyVersionRow).where(
            OntologyVersionRow.namespace == namespace,
            OntologyVersionRow.version == version,
        )
        row = (await self._session.execute(stmt)).scalar_one()
        row.status = status.value
        if approved_by is not None:
            row.approved_by = approved_by
        if approved_at is not None:
            row.approved_at = approved_at
        if reset_projected:
            row.projected_at = None
        await self._session.flush()
        await self._session.refresh(row)
        return _to_model(row)

    async def unprojected(self) -> list[OntologyVersion]:
        """まだ射影されていないバージョン。reconcile の対象。

        `draft` は除外する。ADR-0010 決定5により `draft` は射影しないことが
        正常な状態であり、`projected_at IS NULL` は `draft` にとっての通常の
        姿になった(以前は「publish 後、射影が終わるまでの一時的な状態」
        だったが、`draft` は射影自体が存在しないため恒久的に NULL のまま
        になる)。ここで除外しないと `reconcile` が `draft` を毎回拾って
        射影しようとしてしまう。
        """
        stmt = select(OntologyVersionRow).where(
            OntologyVersionRow.projected_at.is_(None),
            OntologyVersionRow.status != OntologyVersionStatus.DRAFT.value,
        )
        return [_to_model(r) for r in (await self._session.execute(stmt)).scalars()]

    async def all_blob_paths(self) -> set[str]:
        """記録されている全バージョンの Blob パス集合を返す。

        `ProjectionService.reconcile()` が孤児 Blob(PG に対応する行が無い TTL)を
        検出するために使う。`ontology_versions` は `namespaces` への外部キーが
        `ON DELETE CASCADE` なので、名前空間の行が削除されればその配下のバージョン
        行も一緒に消える。つまり名前空間ごとにループする必要はなく、テーブル全体を
        1回読めば「正本(PG)が知っている Blob パス」の全体が取れる。
        """
        stmt = select(OntologyVersionRow.blob_path)
        return set((await self._session.execute(stmt)).scalars())


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
