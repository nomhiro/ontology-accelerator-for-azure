"""射影ループと承認の状態遷移(ADR-0010)。

**書き込み順序は「正本 → 射影」で固定する。**
0. (`publish` のみ)TTL を rdflib で構文検証する(P1-C2、`ontology_core.turtle.
   validate_turtle`)。**Blob へ書く前に行う。** 検証を通さずに 1 へ進むと、
   壊れた TTL が正本に入り、以後の射影が永久に失敗し続ける(下記参照)
1. TTL を Blob に置く(正本、書いた時点で耐久化。状態遷移(submit/approve/
   reject)では TTL は変わらないためこの手順は無い)
2. バージョンと監査イベントを PostgreSQL に記録して commit する(正本、
   ここで耐久化を確定させる。`SessionDep` のリクエスト終了時 commit に
   任せない。詳細は `ProjectionService.publish` のコメントを参照)
3. 名前空間ごとの承認状態マニフェスト(`versions/<ns>/_state.json`)を Blob へ
   書く(射影。PostgreSQL の状態から作り直せるので正本ではない。ADR-0010 決定7)
4. Fuseki へ射影する。ADR-0010 決定5により**状態によって射影先が変わる**。
   `draft` は射影しない。`in-review` は名前付きグラフのみ。`approved` は
   名前付きグラフ + 既定グラフ(既定グラフは PUT で丸ごと置き換わるため、
   前の承認済み版の内容は自動的に消える)。`reject` は名前付きグラフから外す

3・4 が失敗しても 1・2 は巻き戻さない(1・2 は既に commit 済みで巻き戻せない)。
`projected_at` が NULL のまま残り、`reconcile()` が後から埋める。マニフェストの
失敗も同様に扱う(射影側。正本ではないので失敗を握り潰し、`reconcile` が
PostgreSQL から再生成する)。

**トリプルストアは再構築可能な射影であり正本ではない**(設計原則1)。正本
(Blob + PostgreSQL)が耐久的に書けた時点で操作は成功しており、3・4 の失敗は
握り潰して呼び出し元には成功として返す(`projected_at` が NULL であることで
射影待ちだと分かる)。呼び出し元に失敗を返すと、耐久的に成功した書き込みを
失敗として伝えることになり、射影を正本と同格に扱ってしまう。逆順(射影 → 正本)
にすると「ストアには居るが正本に無い」データが生まれ、レプリカ再作成で消える
ため許容できない(docs/adr/0002-triple-store-as-rebuildable-projection.md)。

状態遷移のたびに、その版の `projected_at` を NULL に戻してから射影を試みる
(`VersionRepository.set_status` の `reset_projected`)。「射影済み」の意味が
遷移ごとに変わる(`in-review` の射影済みと `approved` の射影済みは別物)ため、
リセットを忘れると射影失敗時に `reconcile` が回収できない状態になる。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import AuditRepository, VersionRepository
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.graphs import dataset_name, version_graph_iri
from ontology_core.models import OntologyVersion, OntologyVersionStatus
from ontology_core.sparql.client import SparqlStore, SparqlStoreError
from ontology_core.turtle import validate_turtle

logger = logging.getLogger(__name__)

__all__ = [
    "AutoVersionError",
    "InvalidTransitionError",
    "ProjectionService",
    "ReconcileReport",
    "UnknownNamespaceError",
    "UnknownVersionError",
]


class UnknownNamespaceError(Exception):
    """存在しない名前空間を指定したことを表す。"""


class UnknownVersionError(Exception):
    """存在しない (名前空間, バージョン) の組を指定したことを表す。"""


class InvalidTransitionError(Exception):
    """現在の状態から許されない遷移を要求したことを表す(例: draft を approve)。"""


def _build_manifest(namespace: str, versions: list[OntologyVersion]) -> dict[str, Any]:
    """PostgreSQL 上の状態からマニフェスト(ADR-0010 決定7)を組み立てる。

    `draft` は含めない(ローダが射影しないため、渡す必要が無い)。`current` は
    `approved` の版で、存在しなければ `None`(JSON では `null`)。
    """
    included = [v for v in versions if v.status is not OntologyVersionStatus.DRAFT]
    current = next(
        (v.version for v in included if v.status is OntologyVersionStatus.APPROVED), None
    )
    return {
        "schema": 1,
        "namespace": namespace,
        "current": current,
        "versions": [{"version": v.version, "status": v.status.value} for v in included],
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


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

        # ---- 0. TTL の構文検証(P1-C2) ----
        # **最初の Blob 書き込み(直後の put_version)より前に呼ぶ。位置が本質。**
        # ここより後(put_version の後)に置いても、壊れた TTL が正本に入って
        # しまった後の検証になり意味が無い。壊れた TTL が Blob に入ると、
        # その後の put_graph が Fuseki に拒否されて失敗し続けても射影の失敗は
        # 握り潰される設計(不変条件3)なので呼び出し元には成功が返り、
        # reconcile が永久に回収できなくなる。さらに P1-C1 の 409 ガード
        # (Blob に版が残っている名前空間は削除できない)により、名前空間を
        # 削除して逃げることもできなくなる。
        #
        # rdflib の解析は同期・CPU バウンドで、`PublishRequest` が許す
        # 20MB の TTL では実測で約 18.5 秒かかる(手元の環境、単純な
        # トリプルの繰り返しで計測。実運用の TTL は語彙が複雑になり得るため
        # さらに遅くなる可能性がある)。`publish` は async であり、
        # そのままだとこの間イベントループを塞いで他のリクエストが進めなく
        # なるため、`asyncio.to_thread` で別スレッドに逃がす。
        # `submit`/`approve` では検証しない(オントロジーは不変リビジョンで
        # あり、内容は publish 時に一度検証すれば足りる)。
        await asyncio.to_thread(validate_turtle, turtle)

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
                    # **承認済みを主張しない。** 承認フローは Phase 2(ADR-0010 で設計)で、
                    # Phase 1 には承認の段階が存在しない。それにもかかわらず APPROVED を
                    # 記録すると「誰も承認していないのに承認済み」というデータになる。
                    # この製品の中核価値は「誰が承認した定義に基づく答えかを説明できること」
                    # (ADR-0006)なので、偽の主張がデータに残るのは機能の欠落より害が大きい。
                    # `approved_by` / `approved_at` も未設定のままにする(既定が None)。
                    # 承認 API を実装するまで APPROVED には到達しない。
                    status=OntologyVersionStatus.DRAFT,
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

        # ---- 3. 射影(マニフェストのみ) ----
        # ADR-0010 決定5: `draft` は Fuseki に射影しない。`graph_iri` はここでは
        # 使わない(submit で in-review になった時点で初めて名前付きグラフへ
        # 射影する)。それでもマニフェスト(`versions/<ns>/_state.json`)は
        # ここで書く。書かないと、まだ何も承認・審査されていない名前空間の
        # マニフェストが存在しないままになり、ローダが「マニフェストが無い
        # (=想定外)」と「まだ何も無い(=正常)」を区別できなくなる
        # (containers/fuseki/load-snapshot.sh 修正5)。`draft` 自体は
        # マニフェストの `versions` に含めない(_build_manifest 参照)。
        await self._refresh_manifest(namespace)

        return recorded

    async def _refresh_manifest(self, namespace: str) -> None:
        """PostgreSQL の現在の状態からマニフェストを作り直して Blob へ書く。

        マニフェストは射影であり正本ではない(ADR-0010 決定7)。失敗しても
        正本への書き込みを失敗させず、ログに残すだけにする(不変条件3と同じ
        考え方)。`reconcile()` が全名前空間について再生成するため、ここで
        失敗しても永久には失われない。
        """
        versions_list = await VersionRepository(self._session).list_for(namespace)
        manifest = _build_manifest(namespace, versions_list)
        try:
            await self._blob.put_manifest(namespace, manifest)
        except BlobStoreError:
            logger.exception(
                "名前空間 '%s' のマニフェスト更新に失敗しました。reconcile で回復します",
                namespace,
            )

    async def _project_named_graph(self, *, namespace: str, version: OntologyVersion) -> bool:
        """版の TTL を名前付きグラフへ射影する。成功したら True を返す。

        Blob からの読み取り失敗(`BlobStoreError`)も Fuseki への書き込み失敗
        (`SparqlStoreError`)と同じく射影側の失敗として握り潰す。`blob_path` は
        正本(PostgreSQL)に記録済みの値であり、Blob 側の一時的な障害で読めない
        ことがあっても、それは正本への書き込みそのものの失敗ではないため
        (不変条件3)。
        """
        try:
            turtle = await self._blob.get_version(version.blob_path)
            await self._store.put_graph(version.graph_iri, turtle, dataset=dataset_name(namespace))
        except (BlobStoreError, SparqlStoreError):
            logger.exception(
                "名前空間 '%s' バージョン '%s' の名前付きグラフへの射影に失敗しました。"
                "reconcile で回復します",
                namespace,
                version.version,
            )
            return False
        return True

    async def _project_default_graph(self, *, namespace: str, version: OntologyVersion) -> bool:
        """版の TTL を既定グラフへ射影する(承認済み現行版のみ)。成功したら True。"""
        try:
            turtle = await self._blob.get_version(version.blob_path)
            await self._store.put_default_graph(turtle, dataset=dataset_name(namespace))
        except (BlobStoreError, SparqlStoreError):
            logger.exception(
                "名前空間 '%s' バージョン '%s' の既定グラフへの射影に失敗しました。"
                "reconcile で回復します",
                namespace,
                version.version,
            )
            return False
        return True

    async def submit(self, *, namespace: str, version: str, actor: str) -> OntologyVersion:
        """`draft` を `in-review` にし、名前付きグラフへ射影する(ADR-0010 決定1)。"""
        versions = VersionRepository(self._session)
        current = await versions.get(namespace, version)
        if current is None:
            raise UnknownVersionError(f"'{namespace}@{version}' が見つかりません")
        if current.status is not OntologyVersionStatus.DRAFT:
            raise InvalidTransitionError(
                f"'{namespace}@{version}' は draft ではないため submit できません"
                f"(現在の状態: {current.status.value})"
            )

        updated = await versions.set_status(
            namespace,
            version,
            status=OntologyVersionStatus.IN_REVIEW,
            reset_projected=True,
        )
        await AuditRepository(self._session).record(
            namespace=namespace,
            action="submitted",
            actor=actor,
            subject=f"{namespace}@{version}",
        )
        await self._session.commit()

        await self._refresh_manifest(namespace)

        if await self._project_named_graph(namespace=namespace, version=updated):
            await versions.mark_projected(namespace, version)
            await self._session.commit()
            updated = await versions.get(namespace, version) or updated

        return updated

    async def approve(self, *, namespace: str, version: str, actor: str) -> OntologyVersion:
        """`in-review` を `approved` にする。前の `approved` は自動で `superseded`
        にする(ADR-0010 決定3)。既定グラフ + 名前付きグラフへ射影する(決定5・6)。

        Phase 1 では権限を強制しない(ADR-0010「承認の権限を Phase 1 では
        強制できない」)。四眼原則(提案者と承認者を別人にする)と責任者のみが
        承認できるという制約は、名前空間 RBAC(P2A-06)と責任者(P2B-04)に
        依存するため Phase 2 で対応する。認証済みの呼び出し元は誰でも
        approve でき、`approved_by` には実際に呼び出した主体が記録される
        (記録は正しいが、強制は無い)。
        """
        versions = VersionRepository(self._session)
        current = await versions.get(namespace, version)
        if current is None:
            raise UnknownVersionError(f"'{namespace}@{version}' が見つかりません")
        if current.status is not OntologyVersionStatus.IN_REVIEW:
            raise InvalidTransitionError(
                f"'{namespace}@{version}' は in-review ではないため approve できません"
                f"(現在の状態: {current.status.value})"
            )

        previous_approved = next(
            (
                v
                for v in await versions.list_for(namespace)
                if v.status is OntologyVersionStatus.APPROVED and v.version != version
            ),
            None,
        )

        now = datetime.now(UTC)
        updated = await versions.set_status(
            namespace,
            version,
            status=OntologyVersionStatus.APPROVED,
            approved_by=actor,
            approved_at=now,
            reset_projected=True,
        )
        audit = AuditRepository(self._session)
        await audit.record(
            namespace=namespace,
            action="approved",
            actor=actor,
            subject=f"{namespace}@{version}",
        )

        if previous_approved is not None:
            await versions.set_status(
                namespace,
                previous_approved.version,
                status=OntologyVersionStatus.SUPERSEDED,
            )
            await audit.record(
                namespace=namespace,
                action="superseded",
                actor=actor,
                subject=f"{namespace}@{previous_approved.version}",
                reason=f"'{namespace}@{version}' の承認による自動遷移",
            )

        await self._session.commit()

        await self._refresh_manifest(namespace)

        named_ok = await self._project_named_graph(namespace=namespace, version=updated)
        default_ok = await self._project_default_graph(namespace=namespace, version=updated)
        if named_ok and default_ok:
            await versions.mark_projected(namespace, version)
            await self._session.commit()
            updated = await versions.get(namespace, version) or updated

        return updated

    async def reject(
        self, *, namespace: str, version: str, actor: str, reason: str
    ) -> OntologyVersion:
        """`in-review` を `draft` に戻す(理由必須)。名前付きグラフから外す。"""
        versions = VersionRepository(self._session)
        current = await versions.get(namespace, version)
        if current is None:
            raise UnknownVersionError(f"'{namespace}@{version}' が見つかりません")
        if current.status is not OntologyVersionStatus.IN_REVIEW:
            raise InvalidTransitionError(
                f"'{namespace}@{version}' は in-review ではないため reject できません"
                f"(現在の状態: {current.status.value})"
            )

        updated = await versions.set_status(
            namespace,
            version,
            status=OntologyVersionStatus.DRAFT,
            reset_projected=True,
        )
        await AuditRepository(self._session).record(
            namespace=namespace,
            action="rejected",
            actor=actor,
            subject=f"{namespace}@{version}",
            reason=reason,
        )
        await self._session.commit()

        await self._refresh_manifest(namespace)

        # draft は射影しないため、submit で射影済みの名前付きグラフを外す。
        # 存在しない場合(submit の射影がまだ完了していなかった場合)も
        # delete_graph は冪等に成功として扱う(不変条件4・FusekiStore.delete_graph)。
        # ここで削除できなくても projected_at は既に NULL にリセット済みだが、
        # `draft` は unprojected() の対象外(reconcile はここを回収しない)。
        # 削除に失敗した場合、名前付きグラフの内容が残留する既知の制約がある
        # (報告に記載)。
        try:
            await self._store.delete_graph(current.graph_iri, dataset=dataset_name(namespace))
        except SparqlStoreError:
            logger.exception(
                "名前空間 '%s' バージョン '%s' の名前付きグラフ削除に失敗しました",
                namespace,
                version,
            )

        return updated

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

        # `unprojected()` は draft を除外済み(ADR-0010 決定5)。状態によって
        # 射影先が変わる: in-review/superseded は名前付きグラフのみ、approved は
        # 名前付きグラフ + 既定グラフ。superseded の保持ポリシー(何版まで既定で
        # 名前付きグラフに残すか)はローダ(load-snapshot.sh、SUPERSEDED_RETAIN)
        # 側の再構築時の話であり、ここ(実行中のストアを正本に追従させるだけの
        # 増分回復)には適用しない(ADR-0010 が既定値を未決としているのは
        # 「再構築時に何を読み込むか」であり、既に承認済みだった版の射影を
        # 事後的に取り除く話ではないため)。
        for version in await versions.unprojected():
            try:
                turtle = await self._blob.get_version(version.blob_path)
                await self._store.put_graph(
                    version.graph_iri, turtle, dataset=dataset_name(version.namespace)
                )
                if version.status is OntologyVersionStatus.APPROVED:
                    await self._store.put_default_graph(
                        turtle, dataset=dataset_name(version.namespace)
                    )
                await versions.mark_projected(version.namespace, version.version)
                report.versions_projected.append(f"{version.namespace}@{version.version}")
            except (SparqlStoreError, BlobStoreError) as exc:
                # Blob(get_version)・ストア(put_graph/put_default_graph)いずれの
                # 失敗も専用の例外で来る契約になっているため、OSError 等を広く
                # 構える必要はない。
                report.failures.append(f"{version.namespace}@{version.version}: {exc}")

        # マニフェスト(versions/<ns>/_state.json)を PostgreSQL から再生成する
        # (ADR-0010 決定7)。マニフェストは射影であり正本ではないため、
        # reconcile が正本から作り直せることを保証する。
        for ns in namespaces:
            manifest = _build_manifest(ns.name, await versions.list_for(ns.name))
            try:
                await self._blob.put_manifest(ns.name, manifest)
            except BlobStoreError as exc:
                report.failures.append(f"{ns.name}: マニフェスト再生成に失敗 ({exc})")

        return report
