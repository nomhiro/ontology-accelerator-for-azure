"""射影ループ。

**書き込み順序は「正本 → 射影」で固定する。**
1. TTL を Blob に置く(正本、書いた時点で耐久化)
2. バージョンと監査イベントを PostgreSQL に記録して commit する(正本、
   ここで耐久化を確定させる。`SessionDep` のリクエスト終了時 commit に
   任せない。詳細は `ProjectionService.publish` のコメントを参照)
3. Fuseki の名前付きグラフへ射影する

3 が失敗しても 1・2 は巻き戻さない(1・2 は既に commit 済みで巻き戻せない)。
`projected_at` が NULL のまま残り、`reconcile()` が後から埋める。

**トリプルストアは再構築可能な射影であり正本ではない**(設計原則1)。正本
(Blob + PostgreSQL)が耐久的に書けた時点で publish は成功しており、3 の失敗は
握り潰して呼び出し元には成功として返す(`projected_at` が NULL であることで
射影待ちだと分かる)。呼び出し元に失敗を返すと、耐久的に成功した書き込みを
失敗として伝えることになり、射影を正本と同格に扱ってしまう。逆順(射影 → 正本)
にすると「ストアには居るが正本に無い」データが生まれ、レプリカ再作成で消える
ため許容できない(docs/adr/0002-triple-store-as-rebuildable-projection.md)。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import AuditRepository, VersionRepository
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.graphs import dataset_name, version_graph_iri
from ontology_core.models import OntologyVersion, OntologyVersionStatus
from ontology_core.sparql.client import SparqlStore, SparqlStoreError

logger = logging.getLogger(__name__)

__all__ = [
    "AutoVersionError",
    "ProjectionService",
    "ReconcileReport",
    "UnknownNamespaceError",
]


class UnknownNamespaceError(Exception):
    """存在しない名前空間を指定したことを表す。"""


class AutoVersionError(Exception):
    """自動採番できないバージョン形式が名前空間の最新版だったことを表す。

    `validate_version`(`ontology_core.graphs`)は英字・記号を含む版を広く許可するが、
    `_next_version` の自動採番はマイナー部を整数として扱う。両者の前提が食い違うため、
    明示バージョン(例: "1.beta.0")で publish した名前空間は、以後 version 省略の
    publish がここで検出されるまで(修正前は未捕捉の `ValueError` で)恒久的に
    500 になっていた。呼び出し側は 422 に変換し、明示バージョンの指定を促す。
    """


@dataclass
class ReconcileReport:
    """reconcile の結果。"""

    datasets_created: list[str] = field(default_factory=list)
    versions_projected: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    # 対応する名前空間が正本に無いデータセット。
    #
    # 名前空間の作成は「DB に flush → データセット作成 → コミット」の順に進むため、
    # 最後のコミットが失敗すると宙に浮いたデータセットが残る。**報告するが自動削除
    # はしない。** データセットの削除は破壊的で、DB 側の行が別の理由で失われていた
    # 場合にデータを消してしまうため、運用者の判断に委ねる。
    orphan_datasets: list[str] = field(default_factory=list)
    # PostgreSQL に対応する行が無い Blob(TTL)の一覧。
    #
    # 一意制約レースの回復パス(I-2)で負けた側が書いた Blob や、名前空間削除後の
    # TOCTOU ウィンドウ(O-1、Blob 一覧取得と PG DELETE の間に並行 publish が
    # Blob を書く)で残る Blob がこれに当たる。orphan_datasets と同じ理由で
    # **報告するが削除しない**(オントロジーは不変リビジョン、ADR-0006)。
    # これが Phase 1 における唯一の検出手段であり、削除は運用者の手動判断に委ねる。
    orphan_blobs: list[str] = field(default_factory=list)


def _next_version(previous: OntologyVersion | None) -> str:
    """次のバージョンを決める。

    Phase 1 では単純に minor を上げる。意味のあるバージョン付け
    (破壊的変更の検出による major 上げ)は Phase 2 の差分検出とあわせて行う。

    Raises:
        AutoVersionError: 最新版の major・minor が数字でなく自動採番できないとき
            (例: "1.beta.0")。`str.split(".")` で日付形式("2026-08-30")のように
            ドットを含まない版を渡すと major 側にそのまま丸ごと入り、これも
            数字判定で弾かれる("2026-08-30.1.0" のような無意味な結果を防ぐ)。
    """
    if previous is None:
        return "1.0.0"
    major, minor, _patch = [*previous.version.split("."), "0", "0"][:3]
    if not major.isdigit() or not minor.isdigit():
        raise AutoVersionError(
            f"名前空間の最新バージョン '{previous.version}' は自動採番できない形式です。"
            "version を明示的に指定して publish してください。"
        )
    return f"{major}.{int(minor) + 1}.0"


class ProjectionService:
    """正本への書き込みとストアへの射影を担う。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        blob: OntologyBlobStore,
        store: SparqlStore,
        graph_iri_base: str,
    ) -> None:
        self._session = session
        self._blob = blob
        self._store = store
        self._base = graph_iri_base

    async def publish(
        self, *, namespace: str, turtle: str, actor: str, version: str | None = None
    ) -> OntologyVersion:
        """オントロジーを新しいバージョンとして公開する。

        同一内容(content_hash が一致)の再投入は既存のバージョンを返す(冪等)。
        """
        namespaces = NamespaceRepository(self._session)
        if await namespaces.get(namespace) is None:
            raise UnknownNamespaceError(f"名前空間 '{namespace}' が見つかりません")

        versions = VersionRepository(self._session)
        content_hash = hashlib.sha256(turtle.encode("utf-8")).hexdigest()
        if (existing := await versions.find_by_hash(namespace, content_hash)) is not None:
            return existing

        resolved = version or _next_version(await versions.latest_for(namespace))
        graph_iri = version_graph_iri(self._base, namespace, resolved)

        # ---- 1. 正本(Blob) ----
        blob_path = await self._blob.put_version(namespace, resolved, turtle)

        # ---- 2. 正本(PostgreSQL) ----
        # 上の find_by_hash は check-then-insert であり、同一内容の同時投入では
        # 両方がここまで到達し得る(競合)。record() が (namespace, content_hash) の
        # 一意制約に当たった場合は SAVEPOINT (begin_nested) の範囲だけを巻き戻し、
        # このセッションの他の変更(この後書く監査イベントや、呼び出し元がまだ
        # コミットしていない別の変更)を巻き添えにしない。session.rollback() を
        # 使うと、この後さらに書き込みがあるにもかかわらずセッション全体が
        # 巻き戻ってしまう(Task 4 の NamespaceRepository.create は create が
        # 1 リクエスト内の唯一の DB 操作だったため rollback() で足りたが、
        # ここは事情が違う)。
        recorded: OntologyVersion
        try:
            async with self._session.begin_nested():
                recorded = await versions.record(
                    namespace=namespace,
                    version=resolved,
                    content_hash=content_hash,
                    graph_iri=graph_iri,
                    blob_path=blob_path,
                    created_by=actor,
                    status=OntologyVersionStatus.APPROVED,
                )
        except IntegrityError:
            # 競合に負けた側。勝った側が書いたはずの行を取り直す。
            conflict = await versions.find_by_hash(namespace, content_hash)
            if conflict is None:
                # content_hash ではなく (namespace, version) の一意制約に当たった
                # 場合(明示的に指定した version が既に別内容で使われている)は
                # 回復できないので、そのまま呼び出し元に伝える。
                raise
            recorded = conflict
            # `resolved` と `graph_iri` も勝った側の値に揃える(final-fix-brief.md
            # 修正2(a) / I-2)。揃えないと、この後の put_graph が負けた側の
            # ローカル変数のまま(=自分が使おうとした版の graph_iri)呼ばれてしまい、
            # 「ストアに居るが正本に無い」孤児グラフが生まれる。さらに
            # mark_projected(namespace, resolved) もこの版の行が存在しないため
            # NoResultFound(SparqlStoreError ではないので握り潰されず 500)になる。
            # 再計算(version_graph_iri を再度呼ぶ等)ではなく `recorded` が
            # 実際に持っている値をそのまま使う。計算式のずれが入る余地を無くすため。
            resolved = recorded.version
            graph_iri = recorded.graph_iri
        else:
            await AuditRepository(self._session).record(
                namespace=namespace,
                action="published",
                actor=actor,
                subject=f"{namespace}@{resolved}",
            )

        # ---- 正本(Blob・PostgreSQL)の耐久化をここで確定させる ----
        # `SessionDep`(`db/engine.py` の `session_scope`)はリクエスト終了後に
        # しか commit しないため、ここで明示的に commit しないと実行順は
        # 「Blob(耐久化) → PG(未コミット) → Fuseki(耐久化) → PG commit」になり、
        # 宣言している不変条件「Blob → PostgreSQL → Fuseki」が耐久化の観点で
        # 守られない(ブランチ全体レビュー O-1)。ここで commit することで、
        # 以後 put_graph が失敗しても「コミット済み・射影前」(projected_at IS
        # NULL)という reconcile が拾える正規の状態が、この時点で既に耐久化
        # されている(リクエスト終了時の commit を待たない)。
        await self._session.commit()

        # ---- 3. 射影 ----
        # put_graph が失敗しても正本(Blob・PostgreSQL、上で commit 済み)は
        # 巻き戻さず、呼び出し元には成功として返す。トリプルストアは正本では
        # なく再構築可能な射影であり(設計原則1)、正本への書き込みが耐久的に
        # 成功した時点で publish は成功している。ここで呼び出し元に失敗を返すと、
        # 耐久的に成功した書き込みを失敗として伝えることになり、射影の失敗が
        # 正本への書き込みを失敗させる(= 射影を正本と同格に扱う)ことになって
        # 設計の根幹に反する。`projected_at` が NULL のまま残ることで射影待ちだと
        # 分かり、`reconcile()` が後から回収する。
        try:
            await self._store.put_graph(graph_iri, turtle, dataset=dataset_name(namespace))
            await versions.mark_projected(namespace, resolved)
            await self._session.commit()
        except SparqlStoreError:
            logger.exception(
                "名前空間 '%s' バージョン '%s' の射影に失敗しました。"
                "正本は保存済みです。reconcile で回復します",
                namespace,
                resolved,
            )
            return recorded

        return await versions.find_by_hash(namespace, content_hash) or recorded

    async def reconcile(self) -> ReconcileReport:
        """正本を基準にストアの状態を揃える。

        レプリカ再作成後や射影失敗後の回復に使う。
        """
        report = ReconcileReport()
        namespaces = await NamespaceRepository(self._session).list_all()
        existing = set(await self._store.list_datasets())

        for ns in namespaces:
            dataset = dataset_name(ns.name)
            if dataset not in existing:
                try:
                    await self._store.create_dataset(dataset)
                    report.datasets_created.append(ns.name)
                except SparqlStoreError as exc:
                    report.failures.append(f"{ns.name}: データセット作成に失敗 ({exc})")
                    continue

        # 正本に無いデータセットを報告する(削除はしない。上記の理由による)。
        known = {dataset_name(ns.name) for ns in namespaces}
        report.orphan_datasets = sorted(existing - known - {"ds"})

        versions = VersionRepository(self._session)

        # 正本(PG)に対応する行が無い Blob を報告する(削除はしない。
        # orphan_datasets と同じ理由。final-fix-brief.md 修正2(b) / I-2)。
        all_blobs = set(await self._blob.list_versions())
        report.orphan_blobs = sorted(all_blobs - await versions.all_blob_paths())

        for version in await versions.unprojected():
            try:
                turtle = await self._blob.get_version(version.blob_path)
                await self._store.put_graph(
                    version.graph_iri, turtle, dataset=dataset_name(version.namespace)
                )
                await versions.mark_projected(version.namespace, version.version)
                report.versions_projected.append(f"{version.namespace}@{version.version}")
            except (SparqlStoreError, BlobStoreError) as exc:
                # Blob(get_version)・ストア(put_graph)いずれの失敗も専用の例外で
                # 来る契約になっているため、OSError 等を広く構える必要はない。
                report.failures.append(f"{version.namespace}@{version.version}: {exc}")

        return report
