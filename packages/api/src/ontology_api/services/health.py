"""健全性指標の材料を集める(ADR-0020、`P2B-06`)。

計算そのものは `ontology_core.health` の純粋関数が行う。ここは**正本から
材料を集める**責任だけを持つ。

## 用語の一覧は正本から作る。ストアから作らない

現在の承認済み版の TTL を Blob から読んで解析する。**ストアから数えては
いけない**(ADR-0020 決定1) — トリプルストアは再構築可能な射影であって正本
ではないので(不変条件1)、ストアが空のときにストアを数えると
**「用語数 0、未参照 0 件、責任者未設定 0 件」= 完全に健全**という報告になる。

**健全性指標として最悪の壊れ方である。** 障害が起きているときに「健全」と
報告する。

## 測れなかった項目は `None` にし、理由を並べる

**「全部か無か」にしない**(決定3)。Blob へ到達できなくても、PostgreSQL だけで
測れる項目(承認の古さ、未射影の版)はそのまま返す。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.access import AccessRepository
from ontology_api.repositories.mappings import MappingRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.questions import QuestionSetRepository
from ontology_api.repositories.term_owners import TermOwnerRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.health import HealthInputs, HealthReport, compute_health
from ontology_core.models import Namespace, OntologyVersion, OntologyVersionStatus
from ontology_core.shacl import ShaclValidationError, validate_turtle_with_shacl
from ontology_core.turtle import TurtleSyntaxError, term_iris_with_prefix

__all__ = ["HealthService", "UnknownNamespaceError"]

logger = logging.getLogger(__name__)


class UnknownNamespaceError(Exception):
    """存在しない名前空間を指定したことを表す。"""


class HealthService:
    """名前空間の健全性指標を集計する。"""

    def __init__(self, *, session: AsyncSession, blob: OntologyBlobStore) -> None:
        self._session = session
        self._blob = blob

    async def measure(self, namespace: str) -> HealthReport:
        """ADR-0009 決定5 の 6 項目に、受け入れ基準の件数(ADR-0022)と
        争われているマッピングの数(ADR-0023)を加えて集計する。

        Raises:
            UnknownNamespaceError: 名前空間が無いとき。
        """
        ns = await NamespaceRepository(self._session).get(namespace)
        if ns is None:
            raise UnknownNamespaceError(f"名前空間 '{namespace}' が見つかりません")

        versions = await VersionRepository(self._session).list_for(namespace)
        current = next((v for v in versions if v.status is OntologyVersionStatus.APPROVED), None)
        # `draft` は射影しないことが正常なので数えない(`unprojected()` と同じ扱い)。
        unprojected = sum(
            1
            for v in versions
            if v.projected_at is None and v.status is not OntologyVersionStatus.DRAFT
        )

        unavailable: list[str] = []
        terms, shacl_violations = await self._measure_from_blob(ns, current, unavailable)

        last_access = {
            row.term_iri: row.last_accessed_at
            for row in await AccessRepository(self._session).list_term_access(namespace)
        }
        owned = frozenset(
            row.term_iri for row in await TermOwnerRepository(self._session).list_for(namespace)
        )
        # **`0` は「受け入れ基準を定めていない」である**(ADR-0022 決定7)。
        # 定めていない名前空間の承認はブロックしないので、**ここに出ることが
        # 唯一それが見える経路である。**
        question_count = await QuestionSetRepository(self._session).count_questions(namespace)
        # **相手側と食い違っているマッピングの数**(ADR-0023 決定4)。自動で
        # 片方に寄せない代わりに、ここで可視化する。
        disputed = await MappingRepository(self._session).disputed_count(namespace)

        return compute_health(
            HealthInputs(
                terms=terms,
                last_access=last_access,
                owned_terms=owned,
                current_version=None if current is None else current.version,
                current_approved_at=None if current is None else current.approved_at,
                shacl_violation_count=shacl_violations,
                unprojected_version_count=unprojected,
                competency_question_count=question_count,
                disputed_mapping_count=disputed,
                unavailable=tuple(unavailable),
                now=datetime.now(UTC),
            ),
            namespace=namespace,
        )

    async def _measure_from_blob(
        self,
        ns: Namespace,
        current: OntologyVersion | None,
        unavailable: list[str],
    ) -> tuple[frozenset[str] | None, int | None]:
        """正本の TTL から用語の一覧と SHACL 違反の件数を求める。

        **承認済み版が無ければ用語 0 で正しい**(ADR-0020 決定1)。その名前空間は
        まだエージェントに何も渡していない。障害ではなく事実なので、
        `unavailable` には入れない。

        取得・解析・検証に失敗したら `None` を返して理由を積む。**`0` にしない。**
        """
        if current is None:
            return frozenset(), 0

        try:
            turtle = await self._blob.get_version(current.blob_path)
        except BlobStoreError as exc:
            logger.warning("名前空間 '%s' の正本の TTL を取得できません: %s", ns.name, exc)
            unavailable.append(
                f"正本の TTL を取得できないため、用語数・未参照・責任者未設定・"
                f"SHACL 違反を測れません ({exc})"
            )
            return None, None

        # 解析と SHACL 検証はどちらも CPU バウンドなので別スレッドへ出す
        # (`validate_shacl` と同じ扱い。イベントループを塞がない)。
        terms: frozenset[str] | None
        try:
            terms = frozenset(await asyncio.to_thread(term_iris_with_prefix, turtle, ns.base_iri))
        except TurtleSyntaxError as exc:
            # **正本に壊れた TTL が入っている状態**(`P1-C2` の検証より前に
            # 投入されたもの)。用語を 0 にすると「用語が無い」と誤解させる。
            logger.warning("名前空間 '%s' の TTL を解析できません: %s", ns.name, exc)
            unavailable.append(
                f"正本の TTL を解析できないため、用語に関する項目を測れません ({exc})"
            )
            terms = None

        shacl_violations: int | None
        try:
            report = await asyncio.to_thread(validate_turtle_with_shacl, turtle)
            # **違反の「件数」は報告のメッセージ数で数える。** pyshacl は
            # 件数を直接返さないため。適合していれば 0、していなければ
            # 最低 1(メッセージが空でも「違反はある」)。
            shacl_violations = 0 if report.conforms else max(1, len(report.messages()))
        except ShaclValidationError as exc:
            # **「検証できなかった」を「違反ゼロ」にしない**(`P2A-05` と同じ判断)。
            logger.warning("名前空間 '%s' の SHACL 検証を実行できません: %s", ns.name, exc)
            unavailable.append(f"SHACL 検証を実行できないため、違反件数を測れません ({exc})")
            shacl_violations = None

        return terms, shacl_violations
