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
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.mappings import MappingRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.questions import QuestionSetRepository
from ontology_api.repositories.versions import AuditRepository, VersionRepository
from ontology_api.services.authorization import (
    NamespaceRetiredError,
    TwoPersonApprovalError,
)
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.competency import (
    CompetencyReport,
    QuestionFileError,
    evaluate_questions_on_turtle,
    parse_questions,
)
from ontology_core.deprecation import (
    DeprecationProblem,
    ProblemKind,
    check_deprecation,
    deprecated_terms,
    has_blocking,
)
from ontology_core.diff import DiffError, OntologyDiff, diff_ontologies
from ontology_core.graphs import dataset_name, version_graph_iri
from ontology_core.models import Namespace, OntologyVersion, OntologyVersionStatus
from ontology_core.retention import ProjectionTarget, decide_projection
from ontology_core.shacl import ShaclReport, validate_turtle_with_shacl
from ontology_core.sparql.client import SparqlStore, SparqlStoreError
from ontology_core.turtle import validate_turtle

logger = logging.getLogger(__name__)

__all__ = [
    "AutoVersionError",
    "CompetencyEvaluationError",
    "CompetencyViolationError",
    "CriteriaSelfRevision",
    "CriteriaSelfRevisionError",
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


class DeprecationViolationError(Exception):
    """廃止のライフサイクルに反しているため承認できない(ADR-0017 決定2)。

    **`ShaclViolationError` と分けている。** どちらも 422 だが、運用者が
    取るべき対処が違う — SHACL 違反は制約を満たすようデータを直す話で、
    こちらは「削除ではなく廃止する」「後継か理由を書く」という縮め方の話
    である。同じ例外に混ぜると、報告を読んでも何を直すか分からない。
    """

    def __init__(self, message: str, *, problems: list[DeprecationProblem]) -> None:
        super().__init__(message)
        self.problems = problems


class ShaclViolationError(Exception):
    """SHACL の制約に違反しているため承認できない(P2A-05、ADR-0005)。

    ADR-0009 決定1 は「形式的に決定可能なもの(構文、プロファイル適合、
    論理的整合性)は機械が確定的に判定し、ブロッキングとする」としている。
    SHACL 適合性は形式的に決定可能なので、**承認を止める**。

    **`publish` では止めない。** `draft` は編集途中でありうる(ADR-0010 決定1 が
    publish と approve を分離したのはこのため)。止めるのは「エージェントが
    答えの根拠にする現行版」にする瞬間である。
    """

    def __init__(self, message: str, report: str = "") -> None:
        super().__init__(message)
        self.report = report


class CompetencyViolationError(Exception):
    """想定質問に答えられないため承認できない(ADR-0022 決定3、`P2B-14`)。

    ADR-0009 決定1 は「合意済みの規約はテストとして機械が実行する」と決めて
    いる。想定質問はまさにそれなので、**落ちたら承認を止める**(422)。
    落ちても通るなら、それは「合意済みの規約」ではなく参考情報である。

    **`CompetencyEvaluationError` と分ける。** どちらも承認を止めるが、
    こちらは 422(基準を満たしていない)、あちらは 502(確かめられなかった)
    である。運用者が取るべき対処が違う — 前者はオントロジーか基準を直す話、
    後者は質問集合が重すぎる・壊れているという話である。
    """

    def __init__(self, message: str, *, report: CompetencyReport) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class CriteriaSelfRevision:
    """審査される側が受け入れ基準を書き換えたという事実(ADR-0029 決定2)。

    **事実の検出と、止めるかの判断を分ける**(決定6)。このオブジェクトは
    四眼原則が無効な名前空間でも作られ、レビュー用の口から見える。
    止めるかどうかだけを `require_two_person_approval` で分岐させる。

    Attributes:
        revision: 有効な質問集合の改訂番号。
        author: 版を書いた主体(= 基準を書き換えた主体)。
        revised_at: 基準が書き換えられた時刻。
        version_created_at: 版が publish された時刻。**境界はこちら** —
            版の内容は publish で固定されるので、著者はその時点で合否を
            知っている(ADR-0029 決定2)。
    """

    revision: int
    author: str
    revised_at: datetime
    version_created_at: datetime

    def message(self) -> str:
        return (
            f"この版を書いた主体 '{self.author}' が、版を publish した後に"
            f"受け入れ基準(質問集合の改訂 {self.revision})を書き換えています"
            f"(版: {self.version_created_at.isoformat()} / "
            f"改訂: {self.revised_at.isoformat()})。"
            "**受け入れ基準は、その基準で審査される側が書き換えてはなりません。**"
            "別の主体が基準を改訂し直して(内容が同じでもよい)から承認してください"
        )


class CriteriaSelfRevisionError(Exception):
    """審査される側が基準を書き換えたため承認できない(ADR-0029 決定2)。

    **`CompetencyViolationError` と分ける。** どちらも 422 だが、
    運用者が取るべき対処が違う — あちらは「オントロジーか基準を直す」、
    こちらは「**別の主体に基準を確認してもらう**」である。同じ例外に混ぜると、
    基準をさらに緩めて解決しようとして解決しない(むしろ悪化する)。
    """

    def __init__(self, message: str, *, detail: CriteriaSelfRevision) -> None:
        super().__init__(message)
        self.detail = detail


class CompetencyEvaluationError(Exception):
    """想定質問を評価できなかった(ADR-0022 決定5)。

    **「評価できなかった」を合格に丸めない。** 予算超過・質問集合の破損・
    TTL の解析失敗はいずれもここに来る。呼び出し元は 502 にする
    (SHACL の `ShaclValidationError` と同じ扱い)。
    """


class ConcurrentUpdateError(Exception):
    """基準バージョンが最新と一致しない(他の人が先に公開している)。

    2 人が同じ版から編集して公開すると、後の版に前の変更が含まれない。
    自動採番の経路では版番号も衝突しないため、**検出も警告もされずに前の
    変更が消える**(P1-13)。HTTP の `If-Match` に相当する楽観的同時実行制御で
    これを検出する。呼び出し元は 409 に対応させる。
    """


class InvalidTransitionError(Exception):
    """現在の状態から許されない遷移を要求したことを表す(例: draft を approve)。"""


#: 退役した名前空間の版に入れる `projection` の値(ADR-0032 決定2)。
#:
#: **`schema` を上げずに退役を表現するための鍵である。** `projection` は
#: schema 2 のローダが**そのまま従う**欄で、`skip:*` は「読み込まない。理由を
#: ログに出す」として既に実装されている(`projection_targets` /
#: `build_namespace_tdb`)。つまり**ローダを 1 行も変えずに退役が効く。**
#:
#: `P2B-C1` の教訓(安全に関わる指示を新しい schema にだけ載せない)を
#: そのまま適用した形である。
RETIRED_PROJECTION = "skip:retired"


def _build_manifest(
    namespace: str,
    versions: list[OntologyVersion],
    *,
    retain_superseded: int,
    retired: bool = False,
) -> dict[str, Any]:
    """PostgreSQL 上の状態からマニフェスト(ADR-0010 決定7)を組み立てる。

    **退役している名前空間は 1 版も射影しない**(ADR-0032 決定2)。
    `current` を `null` にし、全版の `projection` を `skip:retired` にする。
    `retired: true` も載せるが、**それは観測のためだけ**で、無視されても
    安全は崩れない。

    **schema 2 から、各版の射影先(`projection`)を載せる**(ADR-0019 決定1)。
    保持ポリシーの判断は `ontology_core.retention` の 1 箇所にあり、**ローダは
    判断済みの結果を解釈するだけ**である。以前は判断がローダのシェル関数に
    あったため、`reconcile` は食い違いを避けて `superseded` の在否を不問に
    しており、**保持ポリシーを誰も強制していなかった**。

    `draft` は含めない(ローダが射影しないため渡す必要が無い。ADR-0010 決定8)。
    `current` は `approved` の版で、存在しなければ `None`(JSON では `null`)。
    """
    decided = decide_projection(versions, retain_superseded=retain_superseded)
    included = [v for v in versions if v.status is not OntologyVersionStatus.DRAFT]
    current = next(
        (
            v.version
            for v in included
            if decided.get(v.version) is ProjectionTarget.NAMED_AND_DEFAULT
        ),
        None,
    )
    return {
        "schema": 2,
        "namespace": namespace,
        # **退役していれば現行版は無い**(ADR-0032 決定2)。
        "current": None if retired else current,
        "retain_superseded": max(0, retain_superseded),
        "retired": retired,
        "versions": [
            {
                "version": v.version,
                "status": v.status.value,
                "projection": (RETIRED_PROJECTION if retired else decided[v.version].value),
            }
            for v in included
        ],
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


class PublishOutcome(StrEnum):
    """`publish` が新規作成したのか、既存の版を再利用したのか(P1-26)。

    HTTP の意味を正しくするために必要になった。同一内容(`content_hash` が
    一致)の再投入は既存の版をそのまま返す冪等な操作なので、**201 Created では
    なく 200 OK** を返すべきである。
    """

    CREATED = "created"
    REUSED = "reused"


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
    # 正本の状態から射影されているべきでないのに残っていた名前付きグラフ(削除済み)。
    #
    # 主な発生源は `reject` の `delete_graph` 失敗(P1-17)。`draft` は
    # `unprojected()` の対象外(ADR-0010 決定5)なので、バージョン単位の
    # 回収経路では拾えない。**これは orphan_datasets / orphan_blobs と違い
    # 自動削除する。** 判断できる根拠が違うためである: グラフ IRI は
    # `<GRAPH_IRI_BASE>/<名前空間>/<版>` として**このシステムが組み立てたもの**で、
    # かつ PostgreSQL がどの版が射影されるべきかの正本なので、
    # 「自分たちの接頭辞に一致し、かつ正本が射影を求めていない」グラフは
    # 定義上残留である。データセットや Blob と違い、失っても正本から
    # 再構築できる(ADR-0002)。
    graphs_removed: list[str] = field(default_factory=list)
    # データセット内にあるが、自分たちの IRI 接頭辞に一致しないグラフ。
    #
    # `GRAPH_IRI_BASE` を変更した後の古いグラフや、持ち込みストアにある
    # 別用途のグラフがこれに当たる。**報告するが削除しない**
    # (orphan_datasets と同じ保守的な方針)。
    foreign_graphs: list[str] = field(default_factory=list)
    # 正本が射影を求めているのにストアに無い名前付きグラフ(P1-19)。
    #
    # **主な発生源はローダによる名前空間のスキップである。** ローダは
    # マニフェストが取得できない・不正な名前空間を丸ごとスキップする
    # (1 件の設定不備が他の名前空間を全滅させないため)。その結果
    # データセットは存在するが空になり、**エージェントから見ると
    # 「データが無い」と区別がつかない**。気づく手段がローダのログしか
    # なかった(P1-19)。
    #
    # `projected_at` は過去に射影したときのまま残るため `unprojected()` では
    # 拾えない。ここが唯一の検出手段になる。
    #
    # **検出したものは修復する**(ADR-0013 決定2)。`projected_at` は
    # 書き込み経路の知識であり「ストアが今それを保持している」という主張では
    # ない(ADR-0013 決定1)。ストアに無いと**観測された**なら、それは
    # 再射影すべき根拠である。修復できたものは `graphs_repaired` に入るが、
    # **この一覧からは消さない**(ADR-0013 決定5)。修復した事実を隠すと
    # 上流の原因(Blob へ到達できない、`GRAPH_IRI_BASE` の食い違い、
    # ローダのクラッシュ)が見えなくなる。
    #
    # `superseded` は**報告も修復もしない**(ADR-0013 決定3)。ストアに載るかを
    # 決めるのはローダだけ(`SUPERSEDED_RETAIN`)であり、reconcile が再射影すると
    # 再構築のたびに保持ポリシーを打ち消す。報告もしないのは、既定構成で毎回
    # ノイズが出て本当の異常が埋もれるため。
    missing_graphs: list[str] = field(default_factory=list)
    # 上記の欠落のうち、この実行で再射影できたもの(ADR-0013 決定5)。
    #
    # **これが空でないことは成功報告ではなく、上流に問題があるという信号である。**
    # 正常な運用ではストアの内容が失われることはない。空でないなら、ローダの
    # スキップか、ストアの再作成か、手動操作が起きている。
    graphs_repaired: list[str] = field(default_factory=list)
    # 保持ポリシーの外に出たので削除した名前付きグラフ(P2B-02、ADR-0019 決定4)。
    #
    # **`graphs_removed`(正本に無い残留)とは分ける。** 前者は「正本にあるが
    # 載せない版」、後者は「正本に無い版」で、運用者が読むべき意味が違う。
    retention_removed: list[str] = field(default_factory=list)
    # 退役しているので射影しなかった名前空間(ADR-0032 決定4)。
    #
    # **黙って飛ばさない。** 報告に出ないと、運用者は「reconcile を回したのに
    # 射影が戻らない」を故障として調べ始める。退役は意図した状態である。
    retired_namespaces: list[str] = field(default_factory=list)
    # 退役した名前空間にまだ残っていたので消した名前付きグラフ(決定4)。
    #
    # `retire` の `delete_dataset` は失敗を握り潰す(不変条件3)ので、
    # ここが最後の回収経路である。
    retired_graphs_removed: list[str] = field(default_factory=list)


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
        retain_superseded: int = 0,
    ) -> None:
        """
        Args:
            retain_superseded: 名前付きグラフに残す `superseded` の個数
                (ADR-0019 決定2)。**既定を 0 にしているのは既存の呼び出しを
                壊さないため**で、ルータは `Settings.superseded_retain` を渡す。
        """
        self._session = session
        self._blob = blob
        self._store = store
        self._base = graph_iri_base
        self._retain_superseded = retain_superseded

    async def publish(
        self,
        *,
        namespace: str,
        turtle: str,
        actor: str,
        version: str | None = None,
        base_version: str | None = None,
        reason: str = "",
    ) -> OntologyVersion:
        """オントロジーを新しいバージョンとして公開する(版だけを返す)。

        **`publish_with_outcome` の薄いラッパである。** 新規作成か再利用かを
        知る必要があるのは HTTP のステータスコードを決めるルータだけなので、
        既存の呼び出し(テストを含め 50 箇所以上)を巻き込まないよう、
        単純な戻り値の版を残している。
        """
        published, _ = await self.publish_with_outcome(
            namespace=namespace,
            turtle=turtle,
            actor=actor,
            version=version,
            base_version=base_version,
            reason=reason,
        )
        return published

    async def publish_with_outcome(
        self,
        *,
        namespace: str,
        turtle: str,
        actor: str,
        version: str | None = None,
        base_version: str | None = None,
        reason: str = "",
    ) -> tuple[OntologyVersion, PublishOutcome]:
        """オントロジーを新しいバージョンとして公開する。

        同一内容(content_hash が一致)の再投入は既存のバージョンを返す(冪等)。

        Args:
            base_version: 編集の基準にした版(P1-13)。渡した場合、名前空間の
                最新版と一致しなければ `ConcurrentUpdateError` にする。
                省略すると検査しない(既存の呼び出しとの後方互換)。

        Raises:
            ConcurrentUpdateError: `base_version` が最新版と一致しないとき。
        """
        namespaces = NamespaceRepository(self._session)
        # **行ロックを取る**(ADR-0024 決定1)。**位置が本質** — 下の
        # `put_version`(Blob への `.ttl` 書き込み)より前でなければならない。
        #
        # これが無いと、`DELETE /namespaces/{name}` の「Blob は空か」の確認と
        # PostgreSQL の行の削除の間にこの publish が入り込み、**Blob に TTL が
        # あって PostgreSQL には何も無い**状態ができる。ローダは PostgreSQL を
        # 見ず Blob だけを見て再構築するので、**削除したはずの名前空間が次の
        # レプリカ再作成で復活する**。
        locked = await namespaces.get_locked(namespace)
        if locked is None:
            raise UnknownNamespaceError(f"名前空間 '{namespace}' が見つかりません")
        # **退役した名前空間には内容を増やせない**(ADR-0032 決定5)。
        # 行ロックを取った直後に見る — 退役の処理も同じ行ロックを取るので、
        # 「退役中でないことを確かめてから Blob に書く」までが排他される。
        if locked.retired:
            raise NamespaceRetiredError(
                f"名前空間 '{namespace}' は退役しています"
                f"(理由: {locked.retired_reason})。publish はできません。"
                "続けるなら POST /namespaces/{namespace}/unretire で戻してください"
            )

        versions = VersionRepository(self._session)
        content_hash = hashlib.sha256(turtle.encode("utf-8")).hexdigest()
        if (existing := await versions.find_by_hash(namespace, content_hash)) is not None:
            # 冪等な一致。**新規作成していない**ので REUSED を返す(P1-26)。
            return existing, PublishOutcome.REUSED

        # ---- 基準バージョンの検査(P1-13) ----
        #
        # **内容ハッシュによる冪等判定より後、正本への書き込みより前**に置く。
        # 位置が本質である。
        #   - 冪等判定より前に置くと、タイムアウト後の再送(同じ本文・同じ
        #     base_version)が「最新が進んでいる」として 409 になる。これは
        #     競合ではなく再送であり、弾いてはいけない
        #   - 書き込みより後に置くと、弾く前に Blob へ書いてしまう
        latest = await versions.latest_for(namespace)
        if base_version is not None:
            actual = latest.version if latest is not None else None
            if actual != base_version:
                raise ConcurrentUpdateError(
                    f"基準バージョン '{base_version}' は名前空間 '{namespace}' の"
                    f"最新バージョン '{actual}' と一致しません。"
                    "最新を取得してから編集し直してください。"
                )

        # ---- 系譜を決める(ADR-0027 決定1・2、`P2A-15`) ----
        #
        # **3 状態を区別する。** `edited_from` を 1 本の nullable 列で扱うと
        # 「宣言されなかった」が「派生していない」として読める。
        #
        #   1. `base_version` を渡した       → その版から編集した(記録済み)
        #   2. 渡さなかったが版が 1 つも無い → 先行する版が無かった(記録済み)
        #   3. 渡さず、既に版がある           → **分からない**
        #
        # 2 は推測ではない。**この時点で名前空間の行ロックを持っている**ので
        # (上の `get_locked`)、`latest is None` は「この名前空間に版が
        # 存在しない」という測った事実である。
        #
        # 3 で「当時の最新版」を書いてはいけない。それは ADR-0026 決定2 が
        # 拒否したもの(承認の順序を派生として主張する)を正本に書き込む形
        # である。
        if base_version is not None:
            edited_from, edited_from_recorded = base_version, True
        elif latest is None:
            edited_from, edited_from_recorded = None, True
        else:
            edited_from, edited_from_recorded = None, False

        resolved = version or _next_version(latest)
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
        # rdflib の解析は同期・CPU バウンドで、実測で約 1.0 秒/MB かかる
        # (手元の環境、単純なトリプルの繰り返しで計測。実運用の TTL は
        # 語彙が複雑になり得るためさらに遅くなる可能性がある)。
        # `PublishRequest` が許す上限は 5,000,000 文字なので約 5 秒である
        # (P1-20 で 20MB から下げた)。`publish` は async であり、
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
        outcome = PublishOutcome.CREATED
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
                    edited_from=edited_from,
                    edited_from_recorded=edited_from_recorded,
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
            # 競合に負けた側。**自分はこの版を作っていない**ので REUSED に
            # する(P1-26)。勝った側が 201 を受け取り、負けた側は 200 を受け取る。
            outcome = PublishOutcome.REUSED
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
                reason=reason,
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

        return recorded, outcome

    async def retire(self, *, namespace: str, actor: str, reason: str) -> Namespace:
        """名前空間を退役させる(ADR-0032)。**削除ではない。**

        正本(Blob の TTL・PostgreSQL の版と監査)はそのまま残る(不変条件7)。
        止まるのは射影と内容の増設だけである。

        順序には理由がある。

        1. **行ロックを取る**(ADR-0024 決定1 と同じ経路)。`publish` も同じ
           行ロックを取るので、「退役を書く」と「Blob に書く」が排他される
        2. 退役の状態を書き、監査に記録する(**同一トランザクション**)
        3. 全版の `projected_at` を `NULL` に戻す。`unretire` したときに
           `reconcile` が拾えるようにするため(不変条件10 —
           `projected_at` は書き込み経路の知識である)
        4. マニフェストを更新する(全版 `skip:retired`。決定2)
        5. ストアからデータセットを消す

        **4 と 5 が失敗しても退役は失敗させない**(不変条件3)。マニフェストが
        既に `skip:retired` なので、**次の再構築で必ず止まる**。

        Raises:
            UnknownNamespaceError: 名前空間が無いとき。
            NamespaceRetiredError: 既に退役しているとき(冪等にしない —
                理由と時刻を黙って上書きすると、最初の退役の記録が消える)。
        """
        namespaces = NamespaceRepository(self._session)
        locked = await namespaces.get_locked(namespace)
        if locked is None:
            raise UnknownNamespaceError(f"名前空間 '{namespace}' が見つかりません")
        if locked.retired:
            raise NamespaceRetiredError(
                f"名前空間 '{namespace}' は既に退役しています"
                f"({locked.retired_at:%Y-%m-%dT%H:%M:%SZ}、理由: {locked.retired_reason})"
            )

        updated = await namespaces.set_retired(
            namespace, actor=actor, reason=reason, at=datetime.now(UTC)
        )
        assert updated is not None  # 行ロックを持っているので消えない
        await AuditRepository(self._session).record(
            namespace=namespace,
            action="retired",
            actor=actor,
            subject=namespace,
            reason=reason,
        )
        versions = VersionRepository(self._session)
        for version in await versions.list_for(namespace):
            await versions.clear_projected(namespace, version.version)
        await self._session.commit()

        await self._refresh_manifest(namespace)
        try:
            await self._store.delete_dataset(dataset_name(namespace))
        except SparqlStoreError:
            # **退役は失敗させない**(不変条件3)。マニフェストが既に
            # `skip:retired` なので次の再構築で必ず止まり、`reconcile` も
            # 残骸を消す(決定4)。
            logger.exception(
                "名前空間 '%s' のデータセット削除に失敗しました。reconcile で回収します",
                namespace,
            )
        return updated

    async def unretire(self, *, namespace: str, actor: str, reason: str) -> Namespace:
        """退役を解除する(ADR-0032 決定1)。

        **正本は無傷なので、戻すのは列を消すだけである。** 戻せないと、
        誤って退役させた運用者に DB を直接触る以外の回復手段が無い。

        **解除の直後はストアが空である。** `projected_at` は `retire` で
        `NULL` に戻してあるので、`POST /admin/reconcile` を回すか次の
        レプリカ再作成を待てば射影が戻る。**そのことを呼び出し元が伝える。**

        Raises:
            UnknownNamespaceError: 名前空間が無いとき。
            NamespaceRetiredError: 退役していないとき(何も起きないことを
                成功として返さない)。
        """
        namespaces = NamespaceRepository(self._session)
        locked = await namespaces.get_locked(namespace)
        if locked is None:
            raise UnknownNamespaceError(f"名前空間 '{namespace}' が見つかりません")
        if not locked.retired:
            raise NamespaceRetiredError(f"名前空間 '{namespace}' は退役していません")

        updated = await namespaces.set_retired(namespace, actor=None, reason=None, at=None)
        assert updated is not None
        await AuditRepository(self._session).record(
            namespace=namespace,
            action="unretired",
            actor=actor,
            subject=namespace,
            reason=reason,
        )
        await self._session.commit()
        await self._refresh_manifest(namespace)
        return updated

    async def _ensure_not_retired(self, namespace: str) -> None:
        """退役していないことを確かめる(ADR-0032 決定5)。

        **状態遷移の入口で呼ぶ。** 退役した名前空間で版の状態を動かすと、
        マニフェストは `skip:retired` のままなのに PostgreSQL の状態だけが
        変わり、**正本とマニフェストが食い違う**。
        """
        row = await NamespaceRepository(self._session).get(namespace)
        if row is not None and row.retired:
            raise NamespaceRetiredError(
                f"名前空間 '{namespace}' は退役しています"
                f"(理由: {row.retired_reason})。版の状態は変えられません"
            )

    async def _refresh_manifest(self, namespace: str) -> None:
        """PostgreSQL の現在の状態からマニフェストを作り直して Blob へ書く。

        マニフェストは射影であり正本ではない(ADR-0010 決定7)。失敗しても
        正本への書き込みを失敗させず、ログに残すだけにする(不変条件3と同じ
        考え方)。`reconcile()` が全名前空間について再生成するため、ここで
        失敗しても永久には失われない。
        """
        versions_list = await VersionRepository(self._session).list_for(namespace)
        # **退役の状態を読む**(ADR-0032 決定2)。ここを読み忘れると、
        # 退役した名前空間の次の publish 以外の更新でマニフェストが現役に
        # 戻り、射影が復活する。
        row = await NamespaceRepository(self._session).get(namespace)
        manifest = _build_manifest(
            namespace,
            versions_list,
            retain_superseded=self._retain_superseded,
            retired=row is not None and row.retired,
        )
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
                "reconcile の回収対象として残ります",
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
                "reconcile の回収対象として残ります",
                namespace,
                version.version,
            )
            return False
        return True

    async def submit(
        self, *, namespace: str, version: str, actor: str, reason: str = ""
    ) -> OntologyVersion:
        """`draft` を `in-review` にし、名前付きグラフへ射影する(ADR-0010 決定1)。"""
        await self._ensure_not_retired(namespace)
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
            reason=reason,
        )
        await self._session.commit()

        await self._refresh_manifest(namespace)

        if await self._project_named_graph(namespace=namespace, version=updated):
            await versions.mark_projected(namespace, version)
            await self._session.commit()
            updated = await versions.get(namespace, version) or updated

        return updated

    async def validate_shacl(self, *, namespace: str, version: str) -> ShaclReport:
        """版の TTL を SHACL で検証して報告を返す(P2A-05)。

        レビュー画面が承認前に呼ぶ想定(ADR-0005: 専門家がレビューする画面で
        「この提案は制約に違反しています」と即座に示す)。**状態を変えない。**

        Raises:
            UnknownVersionError: 版が無いとき。
            BlobStoreError: 正本から TTL を読めなかったとき。**違反として
                扱わない**(検証できなかったことと違反ゼロを混同しない)。
            ShaclValidationError: 検証を実行できなかったとき。同上。
        """
        current = await VersionRepository(self._session).get(namespace, version)
        if current is None:
            raise UnknownVersionError(f"'{namespace}@{version}' が見つかりません")
        turtle = await self._blob.get_version(current.blob_path)
        # pyshacl は同期・CPU バウンドなので、TTL の構文検証(P1-C2)と同じく
        # 別スレッドへ逃がす。イベントループを塞ぐと他のリクエストが進めない。
        return await asyncio.to_thread(validate_turtle_with_shacl, turtle)

    async def check_criteria_authorship(
        self, *, namespace: str, version: str
    ) -> CriteriaSelfRevision | None:
        """審査される側が受け入れ基準を書き換えていないか調べる(ADR-0029 決定2)。

        **状態を変えない。** レビュー用の口と `approve` の両方から呼ぶ
        (`validate_shacl` / `evaluate_competency_questions` と同じ形)。

        **事実を返すだけで、止めるかは判断しない**(決定6)。
        `approve` が `require_two_person_approval` を見て 422 にする。
        四眼原則を切っている運用者にも、何が起きたかは見えるべきである。

        条件は 2 つの一致である。

        1. 有効な質問集合の改訂の `created_by` == その版の `created_by`
        2. その改訂の `created_at` >= その版の `created_at`

        **境界は版の `created_at`(publish した時刻)である**(決定2)。
        版の内容は publish の時点で固定されるので(不変条件7)、著者はそこで
        合否を知っている。submit の時刻にすると、publish から submit までの
        間の書き換えを見逃す。

        **基準が「緩くなった」かは判定しない**(決定2)。緩めたのか締めたのかを
        機械的に決めるには改訂前後で同じ版を評価して比べる必要があり、
        その評価自体が未評価になりうる(ADR-0022 決定5)。**測れないものを
        条件に入れない** — 規則は「審査される側が基準を書かない」であって、
        方向は問わない。

        **ストアには問い合わせない**(不変条件15)。質問集合も版も正本
        (PostgreSQL)にある。

        Raises:
            UnknownVersionError: 版が無いとき。
        """
        current = await VersionRepository(self._session).get(namespace, version)
        if current is None:
            raise UnknownVersionError(f"'{namespace}@{version}' が見つかりません")

        question_set = await QuestionSetRepository(self._session).active(namespace)
        if question_set is None:
            # 基準を定めていない。審査されるものが無いので問題も無い
            # (ADR-0022 決定7)。
            return None
        if question_set.created_by != current.created_by:
            return None
        if question_set.created_at < current.created_at:
            # 版より前に定められた基準である。**これは正常な統制である**
            # (ADR-0029 のコンテキストの経路 3)。
            return None
        return CriteriaSelfRevision(
            revision=question_set.revision,
            author=current.created_by,
            revised_at=question_set.created_at,
            version_created_at=current.created_at,
        )

    async def evaluate_competency_questions(
        self, *, namespace: str, version: str
    ) -> tuple[int | None, CompetencyReport]:
        """版の TTL に対して想定質問を評価する(ADR-0022 決定3、`P2B-14`)。

        **状態を変えない。** レビュー画面と `approve` の両方から呼ぶ
        (`validate_shacl` と同じ形)。

        戻り値の第 1 要素は評価に使った質問集合の改訂番号。**`None` は
        「質問集合が無い」= 基準を定めていないことを表す**(基準を満たして
        いないのではない。ADR-0022 決定7)。その場合の報告は空である。

        **ストアには問い合わせない。** `approve` の時点でその版はまだ射影
        されていないし、承認をストアの可用性に依存させるのは不変条件3 が
        守ろうとしているものの逆である(ADR-0022 決定3)。

        Raises:
            UnknownVersionError: 版が無いとき。
            BlobStoreError: 正本から TTL を読めなかったとき。**「合格」に
                しない**(評価できなかったことと合格を混同しない)。
            CompetencyEvaluationError: 質問集合が壊れている、または TTL を
                解析できないとき。同上。
        """
        current = await VersionRepository(self._session).get(namespace, version)
        if current is None:
            raise UnknownVersionError(f"'{namespace}@{version}' が見つかりません")

        question_set = await QuestionSetRepository(self._session).active(namespace)
        if question_set is None:
            return None, CompetencyReport()

        try:
            questions = parse_questions(
                question_set.content,
                where=f"'{namespace}' の質問集合(改訂 {question_set.revision})",
            )
        except QuestionFileError as exc:
            # **壊れた質問集合を「基準なし」に丸めない。** 丸めると、質問集合を
            # 壊すことが検査を無効化する手段になる。
            raise CompetencyEvaluationError(str(exc)) from exc

        turtle = await self._blob.get_version(current.blob_path)
        # rdflib の評価は同期・CPU バウンドなので別スレッドへ逃がす
        # (SHACL 検証と同じ理由)。
        try:
            report = await asyncio.to_thread(
                evaluate_questions_on_turtle,
                turtle,
                questions,
                graph_iri=version_graph_iri(self._base, namespace, version),
            )
        except QuestionFileError as exc:
            raise CompetencyEvaluationError(str(exc)) from exc
        return question_set.revision, report

    async def check_deprecation_lifecycle(
        self, *, namespace: str, version: str, base: OntologyVersion | None
    ) -> list[DeprecationProblem]:
        """廃止のライフサイクルに関する問題を列挙する(`P2B-03`、ADR-0017)。

        **状態を変えない。** レビュー画面と `approve` の両方から呼ぶ。

        Raises:
            UnknownVersionError: 版が無いとき。
            BlobStoreError: 正本から TTL を読めなかったとき。**「問題なし」に
                しない**(検証できなかったことと適合を混同しない)。
            DeprecationCheckError: Turtle を解析できなかったとき。同上。
        """
        current = await VersionRepository(self._session).get(namespace, version)
        if current is None:
            raise UnknownVersionError(f"'{namespace}@{version}' が見つかりません")
        turtle = await self._blob.get_version(current.blob_path)
        base_turtle = None if base is None else await self._blob.get_version(base.blob_path)
        problems = await asyncio.to_thread(check_deprecation, turtle, base_turtle=base_turtle)
        problems.extend(await self._mapped_by_others(namespace=namespace, turtle=turtle))
        return problems

    async def _mapped_by_others(self, *, namespace: str, turtle: str) -> list[DeprecationProblem]:
        """廃止する用語へ他の名前空間がマッピングを張っていることを報告する。

        ADR-0030 決定5。**廃止する側にも見せる。**

        **権限の論点が生じない。** `term_mappings` は PostgreSQL にあり、
        これは**この名前空間の用語に対する `incoming`**(=この名前空間自身の
        依存の情報)である。`GET .../mappings?direction=incoming` が既に同じ
        ものを同じ権限で見せている。

        **ブロックしない**(決定6)。ブロックすると、マッピングを張るだけで
        相手の廃止を止められる — **張られた側には従う手段が無い**
        (逆向きの書き込み口が無いので相手の行を消せない)。ADR-0017 決定2 の
        「ブロックが妥当なのは従う正当な手段が常にあるときだけ」に反する。
        """
        deprecated = await asyncio.to_thread(deprecated_terms, turtle)
        if not deprecated:
            return []
        found: list[DeprecationProblem] = []
        incoming = await MappingRepository(self._session).incoming_targets(deprecated)
        for term in sorted(deprecated):
            sources = incoming.get(term, [])
            if not sources:
                continue
            found.append(
                DeprecationProblem(
                    kind=ProblemKind.MAPPED_BY_OTHERS,
                    term=term,
                    blocking=False,
                    message=(
                        f"'{term}' を廃止しますが、他の名前空間から"
                        f"{len(sources)} 件のマッピングが張られています"
                        f"({', '.join(sorted({ns for ns, _ in sources}))})。"
                        "**承認は止めません** — 相手のマッピングを理由に廃止を"
                        "止めると、マッピングを張るだけで廃止を封じられます。"
                        "後継(`dcterms:isReplacedBy`)を書いてあれば、"
                        "相手はそれを見て張り替えられます"
                    ),
                )
            )
        return found

    async def current_approved(self, *, namespace: str, excluding: str) -> OntologyVersion | None:
        """現在の `approved` 版を返す(自分自身は除く)。

        承認の基準(ADR-0016 決定2 / ADR-0017 決定4)を 1 か所で決める。
        """
        return next(
            (
                v
                for v in await VersionRepository(self._session).list_for(namespace)
                if v.status is OntologyVersionStatus.APPROVED and v.version != excluding
            ),
            None,
        )

    async def compute_diff(
        self, *, namespace: str, base: OntologyVersion, target: OntologyVersion
    ) -> OntologyDiff:
        """2 つの版の意味的差分を返す(`P2B-09`、ADR-0016)。

        **`asyncio.to_thread` に出す。** 空白ノードの正規化は最大で数秒
        CPU を使う(ADR-0016 決定5 の実測)。SHACL 検証と同じ扱いにして
        イベントループを塞がない。

        Raises:
            DiffError: どちらかの Turtle が解析できないとき。
            BlobStoreError: Blob へ到達できないとき。
        """
        del namespace  # 呼び出し側の可読性のために受け取るが、Blob パスで足りる
        base_ttl = await self._blob.get_version(base.blob_path)
        target_ttl = await self._blob.get_version(target.blob_path)
        return await asyncio.to_thread(diff_ontologies, base_ttl, target_ttl)

    async def _diff_summary_for_audit(
        self, *, namespace: str, base: OntologyVersion | None, target: OntologyVersion
    ) -> str | None:
        """監査に載せる差分の要約(JSON)を返す。失敗しても `None` を返す。

        **差分の計算に失敗しても承認を失敗させない**(ADR-0016 決定「差分の
        計算に失敗したら承認を失敗させる」を却下)。差分は説明のための記述的な
        メタデータであって承認の前提条件ではない。Blob への到達不能で承認が
        止まるのは、不変条件3(射影の失敗は正本への書き込みを失敗させない)と
        同じ向きの誤りである。

        基準が無い場合(最初の承認)は `None` を返す。「何も無かったところに
        全部追加された」という差分は情報量が無い(ADR-0016 決定2)。
        """
        if base is None:
            return None
        try:
            diff = await self.compute_diff(namespace=namespace, base=base, target=target)
        except (DiffError, BlobStoreError):
            logger.exception(
                "'%s@%s' と '%s' の差分を計算できませんでした。diff は null のまま記録します",
                namespace,
                target.version,
                base.version,
            )
            return None
        summary = diff.summary()
        summary["base_version"] = base.version
        return json.dumps(summary, ensure_ascii=False)

    async def approve(
        self, *, namespace: str, version: str, actor: str, reason: str = ""
    ) -> OntologyVersion:
        """`in-review` を `approved` にする。前の `approved` は自動で `superseded`
        にする(ADR-0010 決定3)。既定グラフ + 名前付きグラフへ射影する(決定5・6)。

        処理の順序には理由がある。

        1. **四眼原則**(P2A-06、ADR-0014 決定4)。状態を変える前に判定する
        2. **SHACL 検証**(P2A-05)。**位置が本質** — 承認後の検証は意味が無い
        3. **廃止のライフサイクル**(P2B-03、ADR-0017 決定2・4)。同上
        4. **想定質問**(P2B-14、ADR-0022 決定3)。同上。**ストアには問い
           合わせない** — この時点でこの版はまだ射影されていない
        5. 状態遷移と前の版の `superseded`
        6. **意味的差分**(P2B-09、ADR-0016)。承認の可否に影響しないので
           状態遷移の後でよい。失敗しても承認は失敗させない
        7. マニフェストの更新と射影

        2〜4 はいずれも「形式的に決定可能なものは機械が確定的に判定する」
        (ADR-0009 決定1)に対応する。**すべて状態遷移より前にある。**

        呼び出し元のロール判定(`maintainer` 以上)はルータで行う。
        """
        # **退役した名前空間では承認しない**(ADR-0032 決定5)。承認は
        # 既定グラフへの射影を伴うので、退役の目的(射影を止める)と衝突する。
        await self._ensure_not_retired(namespace)
        versions = VersionRepository(self._session)
        current = await versions.get(namespace, version)
        if current is None:
            raise UnknownVersionError(f"'{namespace}@{version}' が見つかりません")
        if current.status is not OntologyVersionStatus.IN_REVIEW:
            raise InvalidTransitionError(
                f"'{namespace}@{version}' は in-review ではないため approve できません"
                f"(現在の状態: {current.status.value})"
            )

        # ---- 四眼原則(P2A-06、ADR-0014 決定4) ----
        #
        # **提案者は「その版を publish した主体」で判定する**(ADR-0014 の根拠)。
        # `submit` は「レビューに出す」という事務的な操作でありうる(他人の
        # draft を代わりに submit することは自然に起こる)ため、内容の責任は
        # publish 側にある。
        #
        # **`platform-admin` もここは飛び越えられない**(決定5)。管理者が自分の
        # 提案を自分で承認できてしまうと、四眼原則が「管理者以外への制約」に
        # 成り下がり、規制対応の文脈で意味を失う。そのため呼び出し元は
        # ロール判定とは独立にこの検査を通る。
        namespace_row = await NamespaceRepository(self._session).get(namespace)
        if (
            namespace_row is not None
            and namespace_row.require_two_person_approval
            and current.created_by == actor
        ):
            raise TwoPersonApprovalError(
                f"'{namespace}@{version}' を publish した主体は approve できません"
                "(四眼原則が有効です)。別の maintainer 以上に承認を依頼してください"
            )

        # ---- SHACL 検証(P2A-05、ADR-0005 / ADR-0009 決定1) ----
        # **状態を変える前に検証する。位置が本質。** 承認後に検証しても、
        # 既定グラフに載った後の検査になって意味が無い(TTL の構文検証を
        # Blob 書き込みの前に置いているのと同じ理由。P1-C2)。
        #
        # 検証を**実行できなかった**場合(Blob へ到達できない、pyshacl の
        # 実行時エラー)は違反として扱わず、そのまま呼び出し元へ伝える。
        # 「制約を満たしている」と「確かめられなかった」を混同すると、
        # 壊れた定義を承認してしまう。
        shacl_report = await asyncio.to_thread(
            validate_turtle_with_shacl, await self._blob.get_version(current.blob_path)
        )
        if not shacl_report.conforms:
            raise ShaclViolationError(
                f"'{namespace}@{version}' は SHACL の制約に違反しているため承認できません: "
                + " / ".join(shacl_report.messages()),
                report=shacl_report.shapes_report or shacl_report.data_report,
            )

        previous_approved = await self.current_approved(namespace=namespace, excluding=version)

        # ---- 廃止のライフサイクル(P2B-03、ADR-0017 決定2・4) ----
        # **状態を変える前に検査する**(SHACL 検証と同じ理由)。
        #
        # ブロックするのは「IRI の削除」と「後継も理由も無い廃止」だけである。
        # 生きている用語からの廃止済み用語への参照は**報告に留める** —
        # SHACL の形状やマッピングが正当に参照するため。
        #
        # ADR-0016 決定6 は削除のブロックを P2B-03 に送った。**廃止の経路が
        # 無い状態で削除を禁じると、縮める正当な手段が 1 つも無くなる**ため
        # である。その順序の依存がここで解ける。
        deprecation_problems = await self.check_deprecation_lifecycle(
            namespace=namespace, version=version, base=previous_approved
        )
        if has_blocking(deprecation_problems):
            raise DeprecationViolationError(
                f"'{namespace}@{version}' は廃止のライフサイクルに反しているため承認できません",
                problems=[p for p in deprecation_problems if p.blocking],
            )

        # ---- 想定質問(P2B-14、ADR-0022 決定3) ----
        # **状態を変える前に検査する**(SHACL 検証と同じ理由)。
        #
        # **ストアには問い合わせない。** この時点でこの版はまだ射影されて
        # いないし、承認をストアの可用性に依存させるのは不変条件3 が守ろうと
        # しているものの逆である。正本の TTL に対して rdflib で評価する。
        #
        # **質問集合が無い名前空間は素通りする**(決定7)。「基準を定めて
        # いない」は「基準を満たしていない」ではない。定めていないことは
        # 健全性指標(`competency_question_count`)で見える。
        #
        # **基準の出自を先に見る**(ADR-0029 決定2)。位置が本質 —
        # 基準そのものが正当でないなら、その基準で評価しても意味が無い。
        #
        # **検出は常に行い、止めるかだけを四眼原則の設定で分岐させる**
        # (決定6)。設定を別に作らないのは、片方だけ切って「四眼原則を
        # 有効にしたつもり」になれる状態を作らないためである(決定3)。
        #
        # **`platform-admin` の分岐は無い**(決定4)。管理者が飛び越えられる
        # なら、四眼原則が「管理者以外への制約」に成り下がる(不変条件12)。
        self_revision = await self.check_criteria_authorship(namespace=namespace, version=version)
        if (
            self_revision is not None
            and namespace_row is not None
            and namespace_row.require_two_person_approval
        ):
            raise CriteriaSelfRevisionError(
                f"'{namespace}@{version}' は受け入れ基準の出自のため承認できません: "
                + self_revision.message(),
                detail=self_revision,
            )

        question_revision, competency = await self.evaluate_competency_questions(
            namespace=namespace, version=version
        )
        if question_revision is not None:
            if not competency.evaluated_all:
                # **「評価していない」を合格に丸めない**(決定5)。422 ではなく
                # 502 相当として扱う — 基準を満たしていないのではなく、
                # 確かめられなかったのである。
                raise CompetencyEvaluationError(
                    f"'{namespace}@{version}' の想定質問を評価しきれませんでした"
                    f"(予算 {competency.elapsed_seconds:.1f} 秒を超過。"
                    f"未評価 {len(competency.not_evaluated)} 件)"
                )
            if not competency.conforms:
                raise CompetencyViolationError(
                    f"'{namespace}@{version}' は想定質問に答えられないため承認できません"
                    f"(質問集合の改訂 {question_revision}): " + " / ".join(competency.messages()),
                    report=competency,
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
        # ---- 意味的差分(P2B-09、ADR-0016) ----
        #
        # **基準は「この承認によって superseded になる版」である**(決定2)。
        # publish 時ではなく approve 時に計算するのは、draft が数週間放置され
        # うるため — publish 時点の「現行」は承認時点の「現行」と違う。
        #
        # **状態遷移の後に置いてよい。** SHACL 検証と違い、差分は承認の可否に
        # 影響しない(決定6: 報告するだけでブロックしない)ので、前段に置く
        # 理由が無い。
        diff_summary = await self._diff_summary_for_audit(
            namespace=namespace, base=previous_approved, target=updated
        )

        # **どの基準で通ったかを監査に残す**(ADR-0022 決定8)。基準は改訂
        # されうるので、版だけでは「何を満たして承認されたのか」を後から
        # 復元できない。
        audit_reason = reason
        if question_revision is not None:
            note = (
                f"想定質問: 質問集合の改訂 {question_revision} の "
                f"{len(competency.results)} 件すべてに合格"
            )
            audit_reason = f"{reason} / {note}" if reason else note

        audit = AuditRepository(self._session)
        await audit.record(
            namespace=namespace,
            action="approved",
            actor=actor,
            subject=f"{namespace}@{version}",
            reason=audit_reason,
            diff=diff_summary,
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
        await self._ensure_not_retired(namespace)
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
            # **退役した名前空間のデータセットは作り直さない**(ADR-0032 決定4)。
            # `retire` が消したものを reconcile が作り直すと、退役が効かない。
            if ns.retired:
                continue
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
        # **退役した名前空間は射影しない**(ADR-0032 決定4)。`retire` は
        # `projected_at` を `NULL` に戻すので(決定3)、これが無いと
        # **reconcile が即座に再射影してしまう**。
        retired_names = {ns.name for ns in namespaces if ns.retired}
        report.retired_namespaces = sorted(retired_names)
        for version in await versions.unprojected():
            if version.namespace in retired_names:
                continue
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

        # 名前付きグラフ単位の残留検出(ADR-0010 補記1、P1-17)。
        #
        # **バージョン単位の回収(上の unprojected ループ)では拾えない乖離がある。**
        # `reject` は `draft` に戻してから `delete_graph` を呼ぶが、この削除の
        # 失敗は握り潰される(不変条件3と同じ扱い)。`draft` は `unprojected()`
        # の対象外なので、残留した名前付きグラフを誰も回収しない。
        # そこで「ストアに実際にあるグラフ」と「正本が射影を求めるグラフ」を
        # 突き合わせる。射影の失敗ではなく**射影の取り消しの失敗**を回収する経路。
        #
        # 上の射影ループより後に置く(そこで新たに射影したグラフを
        # 残留と誤判定しないため。順序が本質)。
        for ns in namespaces:
            dataset = dataset_name(ns.name)
            # `version_graph_iri` と同じ組み立て方(base の末尾 `/` を落とす)。
            prefix = f"{self._base.rstrip('/')}/{ns.name}/"
            try:
                actual = await self._store.list_graphs(dataset)
            except SparqlStoreError as exc:
                if ns.retired:
                    # 退役してデータセットを消してあるなら一覧は失敗する。
                    # **これは故障ではない。**
                    continue
                report.failures.append(f"{ns.name}: 名前付きグラフ一覧の取得に失敗 ({exc})")
                continue
            if ns.retired:
                # **退役した名前空間は「1 版も載せない」が正本の主張である**
                # (ADR-0032 決定2)。残っていれば乖離なので消す(決定4)。
                # ADR-0013 の「観測された乖離を直す」そのものである。
                for graph_iri in sorted(actual):
                    if not graph_iri.startswith(prefix):
                        report.foreign_graphs.append(f"{ns.name}: {graph_iri}")
                        continue
                    try:
                        await self._store.delete_graph(graph_iri, dataset=dataset)
                    except SparqlStoreError as exc:
                        report.failures.append(f"{ns.name}: {graph_iri} の削除に失敗 ({exc})")
                        continue
                    report.retired_graphs_removed.append(f"{ns.name}: {graph_iri}")
                continue
            ns_versions = await versions.list_for(ns.name)
            expected = {
                version.graph_iri
                for version in ns_versions
                if version.status is not OntologyVersionStatus.DRAFT
            }
            # ---- 保持ポリシーで在否を決める(P2B-02、ADR-0019 決定1・4) ----
            #
            # **以前は `superseded` の在否をどちらも不問にしていた。** 判断が
            # ローダのシェル関数にあり、食い違いを避けるために「判断を持たない
            # 側に降りた」結果である。**そのため保持ポリシーを誰も強制して
            # いなかった** — `approve` は前の版の名前付きグラフを外さないので、
            # ストアの内容が「再構築したかどうか」で変わっていた。
            #
            # 判断が `ontology_core.retention` の 1 箇所に集まったので、
            # ここで在否を決められる。
            decided = decide_projection(ns_versions, retain_superseded=self._retain_superseded)
            must_exist = {
                version.graph_iri: version.status.value
                for version in ns_versions
                if decided[version.version].loads_named
            }
            # 保持ポリシーが「載せない」と決めた版のグラフ。**正本にはあるが
            # ストアには置かない**ので、残留していれば削除する。
            must_not_exist = {
                version.graph_iri: version.status.value
                for version in ns_versions
                if not decided[version.version].loads_named
            }
            # ---- 欠落した名前付きグラフを報告して修復する(ADR-0013) ----
            by_iri = {v.graph_iri: v for v in ns_versions}
            for graph_iri in sorted(set(must_exist) - set(actual)):
                report.missing_graphs.append(f"{ns.name}: {graph_iri} ({must_exist[graph_iri]})")
                if await self._project_named_graph(namespace=ns.name, version=by_iri[graph_iri]):
                    report.graphs_repaired.append(
                        f"{ns.name}: {graph_iri} ({must_exist[graph_iri]})"
                    )
                else:
                    report.failures.append(f"{ns.name}: {graph_iri} の再射影に失敗")

            # ---- 保持ポリシーの外に出たグラフを削除する(ADR-0019 決定4) ----
            #
            # **`approve` は外さない**(不変条件3: 正本への書き込みの成否を射影の
            # 操作に依存させない)。回収する仕組みがあるなら、最初からそこに任せる。
            for graph_iri in sorted(set(must_not_exist) & set(actual)):
                try:
                    await self._store.delete_graph(graph_iri, dataset=dataset)
                except SparqlStoreError as exc:
                    report.failures.append(f"{ns.name}: {graph_iri} の削除に失敗 ({exc})")
                    continue
                report.retention_removed.append(
                    f"{ns.name}: {graph_iri} ({must_not_exist[graph_iri]})"
                )

            # ---- 既定グラフが空でないことを別に確認する(ADR-0013 決定4) ----
            #
            # `list_graphs` は名前付きグラフしか返さないので、既定グラフの欠落は
            # 上のループには映らない。**エージェントが読むのは既定グラフ**
            # (ADR-0010 決定6)なので、ここを検査対象から外すと最も害の大きい
            # 障害を見逃す。
            # **保持ポリシーの決定と同じ基準で現行版を選ぶ。** `approved` が
            # 2 つある状態(本来起こらない)でも、既定グラフに載るのは
            # `decide_projection` が選んだ 1 つだけである。
            current_approved = next(
                (
                    v
                    for v in ns_versions
                    if decided[v.version] is ProjectionTarget.NAMED_AND_DEFAULT
                ),
                None,
            )
            if current_approved is not None:
                try:
                    has_content = await self._store.has_default_graph_content(dataset)
                except SparqlStoreError as exc:
                    report.failures.append(f"{ns.name}: 既定グラフの確認に失敗 ({exc})")
                else:
                    if not has_content:
                        label = f"{ns.name}: 既定グラフが空 (approved {current_approved.version})"
                        report.missing_graphs.append(label)
                        if await self._project_default_graph(
                            namespace=ns.name, version=current_approved
                        ):
                            report.graphs_repaired.append(label)
                        else:
                            report.failures.append(f"{ns.name}: 既定グラフの再射影に失敗")
            for graph_iri in actual:
                if not graph_iri.startswith(prefix):
                    report.foreign_graphs.append(f"{ns.name}: {graph_iri}")
                    continue
                if graph_iri in expected:
                    continue
                try:
                    await self._store.delete_graph(graph_iri, dataset=dataset)
                    report.graphs_removed.append(graph_iri)
                except SparqlStoreError as exc:
                    report.failures.append(f"{ns.name}: {graph_iri} の削除に失敗 ({exc})")

        # マニフェスト(versions/<ns>/_state.json)を PostgreSQL から再生成する
        # (ADR-0010 決定7)。マニフェストは射影であり正本ではないため、
        # reconcile が正本から作り直せることを保証する。
        for ns in namespaces:
            manifest = _build_manifest(
                ns.name,
                await versions.list_for(ns.name),
                retain_superseded=self._retain_superseded,
            )
            try:
                await self._blob.put_manifest(ns.name, manifest)
            except BlobStoreError as exc:
                report.failures.append(f"{ns.name}: マニフェスト再生成に失敗 ({exc})")

        return report
