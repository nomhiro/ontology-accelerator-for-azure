"""オントロジーのバージョンと監査イベントの永続化。"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.db import AuditEventRow, OntologyVersionRow
from ontology_core.models import AuditEvent, AuditPage, OntologyVersion, OntologyVersionStatus

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
        edited_from=row.edited_from,
        edited_from_recorded=row.edited_from_recorded,
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
        edited_from: str | None = None,
        edited_from_recorded: bool = False,
    ) -> OntologyVersion:
        """版を 1 行書く。

        Args:
            edited_from: 編集の基準にした版(ADR-0027、`P2A-15`)。
            edited_from_recorded: 系譜が記録されているか。**既定は `False`
                =「分からない」である。** `True` かつ `edited_from` が `None`
                なら「この名前空間に先行する版が無かった」。
                **`None` の 2 つの意味を分けるためにこの旗がある** —
                1 本の列にすると「宣言されなかった」が「派生していない」と
                して読める。
        """
        row = OntologyVersionRow(
            namespace=namespace,
            version=version,
            content_hash=content_hash,
            graph_iri=graph_iri,
            blob_path=blob_path,
            created_by=created_by,
            status=status.value,
            edited_from=edited_from,
            edited_from_recorded=edited_from_recorded,
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

    async def get_many(self, namespace: str, versions: Collection[str]) -> list[OntologyVersion]:
        """指定した版だけを引く(ADR-0027 決定6、`P2A-15`)。

        PROV-O の書き出しが系譜(`edited_from`)を引くために使う。
        **名前空間の全版を引かない** — 書き出しに含まれる監査イベントが
        参照している版だけでよい。

        **空の集合では問い合わせない。** 版を参照する監査イベントが 1 件も
        無い名前空間(マッピングの宣言だけ、など)で往復を 1 回省く。
        """
        if not versions:
            return []
        stmt = select(OntologyVersionRow).where(
            OntologyVersionRow.namespace == namespace,
            OntologyVersionRow.version.in_(list(versions)),
        )
        return [_to_model(r) for r in (await self._session.execute(stmt)).scalars()]

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


def _to_audit(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        id=row.id,
        namespace=row.namespace,
        action=row.action,
        actor=row.actor,
        occurred_at=row.occurred_at,
        subject=row.subject,
        reason=row.reason,
        diff=row.diff,
    )


class AuditRepository:
    """`audit_events` へのアクセス。"""

    #: 1 ページの既定件数と上限。
    #:
    #: **上限を設ける理由は `audit_events` が追記専用で無限に伸びること**である
    #: (ADR-0011 決定2 で `DELETE` を剥奪している)。上限が無いと、運用が長い
    #: 名前空間への 1 リクエストで全件を読み出せてしまう。
    DEFAULT_LIMIT = 50
    MAX_LIMIT = 500

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        namespace: str,
        action: str,
        actor: str,
        subject: str,
        reason: str = "",
        diff: str | None = None,
    ) -> None:
        """決定記録を 1 件書く。

        `diff` は意味的差分の**要約**の JSON(`P2B-09`、ADR-0016 決定7)。
        全トリプルは載せない — 版は Blob に不変で残るので厳密な差分は
        いつでも再計算できるし、載せると監査行が非有界に育つ。
        """
        self._session.add(
            AuditEventRow(
                namespace=namespace,
                action=action,
                actor=actor,
                subject=subject,
                reason=reason,
                diff=diff,
            )
        )
        await self._session.flush()

    async def list_for_subject(self, namespace: str, subject: str) -> list[AuditEvent]:
        """ある対象(版など)についての決定記録を、起きた順に返す(P2B-08)。

        ADR-0009 決定7 の「なぜ」を参照時に返すための読み出し。
        **`occurred_at` だけでなく `id` も並び順に使う。** `occurred_at` の
        既定値は `now()` で、同一トランザクション内の複数イベントは
        **同じ値になりうる**(PostgreSQL の `now()` はトランザクション開始時刻)。
        `id` を第二キーにしないと、同じ時刻のイベントの順序が不定になり
        「published のあとに submitted」という履歴が読めなくなる。

        汎用の監査照会(名前空間全体・期間・実行者での絞り込み)は `P2B-11`。
        ここでは 1 つの対象に限る。
        """
        stmt = (
            select(AuditEventRow)
            .where(
                AuditEventRow.namespace == namespace,
                AuditEventRow.subject == subject,
            )
            .order_by(AuditEventRow.occurred_at, AuditEventRow.id)
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [_to_audit(row) for row in rows]

    async def query(
        self,
        *,
        namespace: str,
        action: str | None = None,
        actor: str | None = None,
        subject: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: int | None = None,
    ) -> AuditPage:
        """名前空間の監査証跡を新しい順に 1 ページ返す(`P2B-11`)。

        ## 並び順とページングの鍵は `id` である。`occurred_at` ではない

        `occurred_at` の既定値は `now()` で、**PostgreSQL の `now()` は
        トランザクション開始時刻**を返す。同一トランザクション内で記録した
        複数のイベントは**同じ値になる**。時刻を鍵にページングすると、
        境界にある同時刻の複数件を取りこぼすか二重に返す。

        `id` は追記専用のテーブル(ADR-0011 決定2 で `DELETE` を剥奪している)の
        単調増加する主キーなので、**全順序で安定**している。

        ## offset ではなく keyset(cursor)にする

        照会している最中に新しいイベントが追記されるのは普通に起こる。降順の
        offset ページングだと、新しい行が先頭に入るたびに窓が 1 件ずれ、
        **既読のページの末尾を取りこぼす**。`id < cursor` で辿れば、追記は
        必ず大きい `id` を持つので既読のページは動かない。

        ## 期間は半開区間 `[since, until)`

        境界を両側とも含めると、期間を並べて集計したときに二重に数える。

        Args:
            since: この時刻以降(**含む**)。タイムゾーン付きでなければならない。
            until: この時刻より前(**含まない**)。同上。
            cursor: 前のページの `next_cursor`。この `id` より小さいものを返す。

        Raises:
            ValueError: `limit` が範囲外、日時にタイムゾーンが無い、
                または `until` が `since` より前のとき。
        """
        if not 1 <= limit <= self.MAX_LIMIT:
            raise ValueError(
                f"limit は 1〜{self.MAX_LIMIT} の範囲で指定してください(受領: {limit})"
            )
        for name, value in (("since", since), ("until", until)):
            if value is not None and value.tzinfo is None:
                # **素朴に UTC と解釈しない。** 監査の照会で 9 時間ずれた結果を
                # 返すのは「何も返らない」よりたちが悪い(誤った結論の根拠になる)。
                raise ValueError(
                    f"{name} にはタイムゾーンを付けてください"
                    "(`2026-09-10T00:00:00Z` や `2026-09-10T09:00:00+09:00`)"
                )
        if since is not None and until is not None and until < since:
            # 空の結果を返すより、指定の誤りとして伝える。空だと「本当に何も
            # 無い」と誤解させる。
            raise ValueError("until が since より前です")

        stmt = select(AuditEventRow).where(AuditEventRow.namespace == namespace)
        if action is not None:
            stmt = stmt.where(AuditEventRow.action == action)
        if actor is not None:
            stmt = stmt.where(AuditEventRow.actor == actor)
        if subject is not None:
            stmt = stmt.where(AuditEventRow.subject == subject)
        if since is not None:
            stmt = stmt.where(AuditEventRow.occurred_at >= since)
        if until is not None:
            stmt = stmt.where(AuditEventRow.occurred_at < until)
        if cursor is not None:
            stmt = stmt.where(AuditEventRow.id < cursor)

        # **`limit + 1` 件取って続きの有無を判断する。** 「返った件数が limit
        # より少ないから最後」という判定を呼び出し側に押し付けない(件数が
        # ちょうど limit のときに余分な 1 往復が要るだけでなく、判定を間違える)。
        rows = list(
            (
                await self._session.execute(stmt.order_by(AuditEventRow.id.desc()).limit(limit + 1))
            ).scalars()
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        return AuditPage(
            events=tuple(_to_audit(row) for row in page),
            next_cursor=page[-1].id if has_more and page else None,
        )
