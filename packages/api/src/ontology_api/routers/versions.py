"""オントロジーのバージョンの投入と一覧。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import BlobDep, CurrentPrincipal, SessionDep, SettingsDep, StoreDep
from ontology_api.repositories.versions import AuditRepository, VersionRepository
from ontology_api.services.authorization import (
    PermissionDeniedError,
    TwoPersonApprovalError,
    require_namespace_role,
    require_platform_admin,
)
from ontology_api.services.projection import (
    AutoVersionError,
    ConcurrentUpdateError,
    DeprecationViolationError,
    InvalidTransitionError,
    ProjectionService,
    PublishOutcome,
    ReconcileReport,
    ShaclViolationError,
    UnknownNamespaceError,
    UnknownVersionError,
)
from ontology_core.blob import BlobStoreError
from ontology_core.deprecation import DeprecationCheckError
from ontology_core.diff import DiffError
from ontology_core.graphs import NamespaceNameError, validate_namespace_name, validate_version
from ontology_core.models import AuditEvent, NamespaceRole, OntologyVersion
from ontology_core.shacl import ShaclReport, ShaclValidationError
from ontology_core.turtle import TurtleSyntaxError

router = APIRouter(tags=["versions"])


async def _require(
    session: SessionDep, *, namespace: str, principal: CurrentPrincipal, required: NamespaceRole
) -> None:
    """名前空間ロールを要求する。足りなければ 403 にする(ADR-0014 決定2)。

    各ハンドラで同じ try/except を書くと、**足す操作を追加したときに権限
    チェックを書き忘れる**。1 か所にまとめて呼び出しを 1 行にする。
    """
    try:
        await require_namespace_role(
            session, namespace=namespace, principal=principal, required=required
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


# 受け付ける Turtle の最大長(文字数)。
#
# **rdflib の解析コストは約 1.0 秒/MB である**(実測: 0.12MB→0.11秒、
# 1.27MB→1.30秒、20MB→18.5秒)。解析は `asyncio.to_thread` に出しているので
# イベントループは塞がないが、スレッドプールを占有する。以前の上限
# 20,000,000 は**1 リクエストで約 20 秒スレッドを占有できる**値で、上限として
# 大きすぎた(P1-20)。
#
# 5,000,000 文字(約 5 秒)に下げる。オントロジーの定義(クラス・プロパティ・
# 制約)は実データを含まないため、企業のドメイン 1 つ分でも Turtle で
# 数百 KB の規模に収まる。5MB は実用上十分に余裕がある。
#
# これを超える規模を扱う必要が出たら、上限を上げるのではなく**非同期ジョブ
# 経路**(ACA Jobs)に回す。同期 API の上限を上げると、この占有時間がそのまま
# 伸びる。
MAX_TURTLE_LENGTH = 5_000_000


class PublishRequest(BaseModel):
    """オントロジーの投入要求。"""

    turtle: str = Field(min_length=1, max_length=MAX_TURTLE_LENGTH, description="Turtle 形式の本文")
    version: str | None = Field(default=None, description="省略時は自動採番")
    # 楽観的同時実行制御(P1-13)。HTTP の If-Match に相当する。
    # **省略できる**(最初の公開、および基準を持たない自動投入のため)が、
    # 人が編集する経路では必ず渡すべきである。渡さないと、2 人が同じ版から
    # 編集したときに後の版が前の変更を静かに消す。
    base_version: str | None = Field(
        default=None,
        description="編集の基準にした版。最新と一致しなければ 409。省略時は検査しない",
    )
    # ADR-0009 決定7。`audit_events.reason` に入り、
    # `GET /namespaces/{ns}/versions/{v}/decisions` で読み出せる。
    reason: str = Field(default="", description="この版を公開する理由")


class TransitionRequest(BaseModel):
    """`submit` / `approve` の要求。理由は任意(P2B-08、ADR-0009 決定7)。

    **必須にしていない。** 必須にすると `postdeploy` のような自動投入や、
    既存のクライアントが動かなくなる。一方で理由が空の監査は説明にならない
    ため、人が操作する経路(Web UI、Phase 2)では必ず書かせる。
    `reject` だけは最初から必須である(却下の理由が無い却下は無意味なため)。
    """

    reason: str = Field(default="", description="この遷移を行う理由")


class RejectRequest(BaseModel):
    """却下要求。理由は必須(空文字は 422)。"""

    reason: str = Field(min_length=1, description="却下の理由(必須)")


@router.post(
    "/namespaces/{namespace}/versions",
    status_code=status.HTTP_201_CREATED,
    summary="オントロジーを新しいバージョンとして公開する(同一内容の再投入は 200)",
)
async def publish_version(
    namespace: str,
    payload: PublishRequest,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
    response: Response,
) -> OntologyVersion:
    """正本に書いてからストアへ射影する。

    **同一内容の再投入は 200 を返す(201 ではない)。** `publish` は冪等で、
    `content_hash` が一致すれば既存の版をそのまま返す。新規作成していないのに
    201 Created を返すのは HTTP の意味としてずれている(P1-26)。ルートの
    `status_code` は新規作成の既定値として 201 のままにし、再利用のときだけ
    ここで 200 に落とす。
    """
    service = ProjectionService(
        session=session,
        blob=blob,
        store=store,
        graph_iri_base=settings.graph_iri_base,
        retain_superseded=settings.superseded_retain,
    )
    try:
        # `namespace` はこの後 Blob パス・グラフ IRI・Fuseki データセット名の
        # 組み立てに使われる(`ProjectionService.publish` 経由)。DB に存在しない
        # 名前空間なら結局 UnknownNamespaceError で 404 になるが、それは
        # 「たまたま検証されている」だけの経路であり、名前空間名がセキュリティ境界
        # であることの明示的な契約にはならない。パスパラメータの入口で検証する。
        validate_namespace_name(namespace)
        await _require(
            session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_STEWARD
        )
        published, outcome = await service.publish_with_outcome(
            namespace=namespace,
            turtle=payload.turtle,
            actor=principal.object_id or principal.subject,
            version=payload.version,
            base_version=payload.base_version,
            reason=payload.reason,
        )
        # **両方を明示的に設定する。** ルートの `status_code=201` は
        # OpenAPI の既定値として残すが、実際のコードはここで決める。
        # そうしないとハンドラの契約がフレームワークの既定に依存し、
        # 関数を直接呼ぶテストで検証できない。
        response.status_code = (
            status.HTTP_200_OK if outcome is PublishOutcome.REUSED else status.HTTP_201_CREATED
        )
        return published
    except UnknownNamespaceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ConcurrentUpdateError as exc:
        # P1-13: 他の人が先に公開している。正本には何も書いていない
        # (検査は Blob への書き込みより前にある)。
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AutoVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except TurtleSyntaxError as exc:
        # P1-C2: 構文が壊れた TTL。`ProjectionService.publish` は Blob へ書く
        # 前に検証しているため、ここに来た時点で正本(Blob・PostgreSQL)には
        # 何も残っていない。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.get("/namespaces/{namespace}/versions", summary="バージョンの一覧を取得する")
async def list_versions(
    namespace: str, principal: CurrentPrincipal, session: SessionDep
) -> list[OntologyVersion]:
    """名前空間のバージョン一覧を返す。

    不正な `namespace` は該当行が無いだけで空リストが返り実害はないが、
    名前空間名はセキュリティ境界(`packages/api/tests/test_isolation.py`)
    なので、パスパラメータの入口では一貫して検証する。
    """
    try:
        validate_namespace_name(namespace)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await _require(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    return await VersionRepository(session).list_for(namespace)


@router.get(
    "/namespaces/{namespace}/versions/{version}/decisions",
    summary="版の決定記録(誰が・いつ・なぜ)を返す",
)
async def list_version_decisions(
    namespace: str,
    version: str,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> list[AuditEvent]:
    """この版について記録された決定を、起きた順に返す(P2B-08、ADR-0009 決定7)。

    **「誰が承認した定義に基づく答えかを説明できること」がこの製品の中核価値
    である**(ADR-0006)。理由を書いても読み出せなければ説明にならないため、
    書き込みと同じラウンドで読み出し口を用意する。

    存在しない版は**空配列ではなく 404** にする。空配列だと「決定記録が無い版」
    と「そもそも存在しない版」の区別がつかない。

    汎用の監査照会(名前空間全体・期間・実行者での絞り込み、ページング)は
    `P2B-11` で別に用意する。ここは 1 つの版に限る。
    """
    try:
        validate_namespace_name(namespace)
        validate_version(version)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await _require(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    if await VersionRepository(session).get(namespace, version) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{namespace}@{version}' が見つかりません",
        )
    return await AuditRepository(session).list_for_subject(namespace, f"{namespace}@{version}")


@router.post(
    "/namespaces/{namespace}/versions/{version}/validate",
    summary="版を SHACL で検証する(状態は変えない)",
)
# **関数名を `validate_version` にしてはいけない。** `ontology_core.graphs` の
# 検証関数 `validate_version` を同名で上書きしてしまい、他のハンドラの
# 入口検証が黙ってこのエンドポイント関数を呼ぶようになる(実際に一度踏んだ)。
async def validate_version_shacl(
    namespace: str,
    version: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
) -> ShaclReport:
    """SHACL の検証結果を返す。**状態は変えない**(P2A-05、ADR-0005)。

    承認前にレビュー画面がこれを呼び、「この提案は制約に違反しています」を
    即座に示すための口である。`approve` は同じ検証を行い、違反があれば
    422 で拒否する(ADR-0009 決定1: 形式的に決定可能なものはブロッキング)。

    **検証できなかった場合は 502 にする。** 「制約を満たしている」と
    「確かめられなかった」を混同すると、壊れた定義を通してしまう。
    """
    service = ProjectionService(
        session=session,
        blob=blob,
        store=store,
        graph_iri_base=settings.graph_iri_base,
        retain_superseded=settings.superseded_retain,
    )
    try:
        validate_namespace_name(namespace)
        validate_version(version)
        await _require(
            session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
        )
        return await service.validate_shacl(namespace=namespace, version=version)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except UnknownVersionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (BlobStoreError, ShaclValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SHACL 検証を実行できませんでした: {exc}",
        ) from exc


@router.post(
    "/namespaces/{namespace}/versions/{version}/submit",
    summary="draft を in-review にする(名前付きグラフへ射影)",
)
async def submit_version(
    namespace: str,
    version: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
    payload: TransitionRequest | None = None,
) -> OntologyVersion:
    """ADR-0010 決定1・5。Phase 1 では権限を強制しない(認証済みの呼び出し元は誰でも実行できる)。"""
    validate_namespace_name(namespace)
    validate_version(version)
    service = ProjectionService(
        session=session,
        blob=blob,
        store=store,
        graph_iri_base=settings.graph_iri_base,
        retain_superseded=settings.superseded_retain,
    )
    try:
        await _require(
            session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_STEWARD
        )
        return await service.submit(
            namespace=namespace,
            version=version,
            actor=principal.object_id or principal.subject,
            reason=(payload.reason if payload is not None else ""),
        )
    except UnknownVersionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/namespaces/{namespace}/versions/{version}/approve",
    summary="in-review を approved にする(既定+名前付きグラフへ射影、前の版は自動 superseded)",
)
async def approve_version(
    namespace: str,
    version: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
    payload: TransitionRequest | None = None,
) -> OntologyVersion:
    """ADR-0010 決定1・3・5・6。**`maintainer` 以上が必要**(ADR-0014 決定2)。

    四眼原則が有効な名前空間では、その版を publish した主体は approve できない
    (**409**。ADR-0014 決定4)。SHACL 違反は **422**、検証を実行できなかった
    場合は **502**(「制約を満たしている」と「確かめられなかった」を混同しない)。

    承認時に**意味的差分**を計算し、`audit_events.diff` に要約を記録する
    (`P2B-09`、ADR-0016)。基準は前の `approved` 版である。**差分の計算に
    失敗しても承認は失敗しない**(差分は記述的なメタデータであって承認の
    前提条件ではない)。
    """
    validate_namespace_name(namespace)
    validate_version(version)
    service = ProjectionService(
        session=session,
        blob=blob,
        store=store,
        graph_iri_base=settings.graph_iri_base,
        retain_superseded=settings.superseded_retain,
    )
    try:
        await _require(
            session, namespace=namespace, principal=principal, required=NamespaceRole.MAINTAINER
        )
        return await service.approve(
            namespace=namespace,
            version=version,
            actor=principal.object_id or principal.subject,
            reason=(payload.reason if payload is not None else ""),
        )
    except TwoPersonApprovalError as exc:
        # **`PermissionDeniedError`(403)と分ける。** 権限不足ならロールを付与
        # すれば解決するが、四眼原則違反は「別の人に承認してもらう」しかない。
        # 同じ 403 に混ぜると、ロールを足して解決しようとして解決しない。
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DeprecationViolationError as exc:
        # P2B-03: 廃止のライフサイクル違反(ADR-0017 決定2)。**SHACL 違反とは
        # 別の例外にしている** — どちらも 422 だが、運用者が取るべき対処が
        # 違う(制約を満たすようデータを直す話と、縮め方の話)。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="\n".join([str(exc), *(p.message for p in exc.problems)]),
        ) from exc
    except ShaclViolationError as exc:
        # P2A-05: 形式的に決定可能な違反なので承認を止める(ADR-0009 決定1)。
        # 状態は変えていない(検証は遷移より前にある)。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="\n".join(part for part in (str(exc), exc.report) if part).strip(),
        ) from exc
    except (BlobStoreError, ShaclValidationError, DeprecationCheckError) as exc:
        # 「確かめられなかった」を違反として扱わない。
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"承認前の検証を実行できませんでした: {exc}",
        ) from exc
    except UnknownVersionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    "/namespaces/{namespace}/versions/{version}/deprecations",
    summary="廃止のライフサイクルを検査する(状態は変えない)",
)
async def check_version_deprecations(
    namespace: str,
    version: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
    base: Annotated[
        str | None,
        Query(description="削除の判定に使う基準の版。省略時は現在の `approved` 版"),
    ] = None,
) -> dict[str, Any]:
    """廃止に関する問題を列挙する。**状態は変えない**(`P2B-03`、ADR-0017)。

    承認前にレビュー画面がこれを呼ぶための口である。`approve` は同じ検査を
    行い、**`blocking` が `true` の問題があれば 422 で拒否する**。

    ブロックするのは 2 つだけである(ADR-0017 決定2)。

    - **`removed`**: IRI が削除された。削除ではなく `owl:deprecated` を立てる
    - **`no-successor-or-reason`**: 廃止に後継も理由も無い

    **`references-deprecated`(生きている用語が廃止済みを参照)はブロックしない。**
    SHACL の形状やマッピングが廃止された用語を正当に参照するためである。
    """
    try:
        validate_namespace_name(namespace)
        validate_version(version)
        if base is not None:
            validate_version(base)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await _require(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )

    service = ProjectionService(
        session=session,
        blob=blob,
        store=store,
        graph_iri_base=settings.graph_iri_base,
        retain_superseded=settings.superseded_retain,
    )
    versions = VersionRepository(session)
    if base is None:
        base_row = await service.current_approved(namespace=namespace, excluding=version)
    else:
        base_row = await versions.get(namespace, base)
        if base_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"基準の版 '{namespace}@{base}' が見つかりません",
            )

    try:
        problems = await service.check_deprecation_lifecycle(
            namespace=namespace, version=version, base=base_row
        )
    except UnknownVersionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (BlobStoreError, DeprecationCheckError) as exc:
        # **「問題なし」にしない。** 検証できなかったことと適合を混同しない。
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"廃止の検査を実行できませんでした: {exc}",
        ) from exc

    return {
        "namespace": namespace,
        "version": version,
        "base_version": None if base_row is None else base_row.version,
        "blocking": any(p.blocking for p in problems),
        "problems": [
            {
                "kind": p.kind.value,
                "term": p.term,
                "blocking": p.blocking,
                "message": p.message,
                "referenced": p.referenced,
            }
            for p in problems
        ],
    }


@router.get(
    "/namespaces/{namespace}/versions/{version}/diff",
    summary="2 つの版の意味的差分を計算する(状態は変えない)",
)
async def diff_version(
    namespace: str,
    version: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
    base: Annotated[
        str | None,
        Query(
            description="基準にする版。省略時は現在の `approved` 版"
            "(= この版を承認したときに `superseded` になる版)",
        ),
    ] = None,
) -> dict[str, Any]:
    """`data-analyst` が必要。**状態を変えずに差分だけを返す**(ADR-0016 決定2)。

    **保存された差分は決定についての事実、この口はその場で決定するための道具**
    である。役割が違うので別に用意している。

    基準を省略すると現在の `approved` 版を使う。基準が無い(最初の版)場合は
    `base_version` が `null` で `diff` も `null` を返す — 「何も無かった
    ところに全部追加された」という差分は情報量が無い。

    **接頭辞・トリプルの順序・空白ノードのラベルの違いは差分にならない。**
    ただし空白ノードが多い版では、トリプル単位の差分と `modified_terms` が
    得られない(`triple_status` を見ること。ADR-0016 決定5)。
    """
    try:
        validate_namespace_name(namespace)
        validate_version(version)
        if base is not None:
            validate_version(base)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await _require(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )

    service = ProjectionService(
        session=session,
        blob=blob,
        store=store,
        graph_iri_base=settings.graph_iri_base,
        retain_superseded=settings.superseded_retain,
    )
    versions = VersionRepository(session)
    target = await versions.get(namespace, version)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{namespace}@{version}' が見つかりません",
        )

    if base is None:
        base_row = await service.current_approved(namespace=namespace, excluding=version)
    else:
        base_row = await versions.get(namespace, base)
        if base_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"基準の版 '{namespace}@{base}' が見つかりません",
            )

    if base_row is None:
        # **404 にしない。** 「基準が無い」はエラーではなく、この API が
        # 答えるべき事実である(最初の版では必ずこうなる)。
        return {"namespace": namespace, "version": version, "base_version": None, "diff": None}

    try:
        diff = await service.compute_diff(namespace=namespace, base=base_row, target=target)
    except DiffError as exc:
        # 「差分が無い」と混同させない(ADR-0016)。空の差分を返さない。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except BlobStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"差分を計算できませんでした(正本の TTL を取得できません): {exc}",
        ) from exc

    return {
        "namespace": namespace,
        "version": version,
        "base_version": base_row.version,
        "diff": diff.summary(),
    }


@router.post(
    "/namespaces/{namespace}/versions/{version}/reject",
    summary="in-review を draft に戻す(理由必須。名前付きグラフから外す)",
)
async def reject_version(
    namespace: str,
    version: str,
    payload: RejectRequest,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
) -> OntologyVersion:
    """ADR-0010 決定1・5。Phase 1 では権限を強制しない。"""
    validate_namespace_name(namespace)
    validate_version(version)
    service = ProjectionService(
        session=session,
        blob=blob,
        store=store,
        graph_iri_base=settings.graph_iri_base,
        retain_superseded=settings.superseded_retain,
    )
    try:
        await _require(
            session, namespace=namespace, principal=principal, required=NamespaceRole.MAINTAINER
        )
        return await service.reject(
            namespace=namespace,
            version=version,
            actor=principal.object_id or principal.subject,
            reason=payload.reason,
        )
    except UnknownVersionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/admin/reconcile", summary="正本を基準にストアの状態を揃える")
async def reconcile(
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
) -> ReconcileReport:
    """レプリカ再作成後や射影失敗後の回復に使う。

    **`platform-admin` が必要**(ADR-0014 決定2)。名前空間をまたいでストアの
    状態を書き換える操作なので、単一の名前空間のロールでは判定できない。
    """
    try:
        require_platform_admin(principal)
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    service = ProjectionService(
        session=session,
        blob=blob,
        store=store,
        graph_iri_base=settings.graph_iri_base,
        retain_superseded=settings.superseded_retain,
    )
    return await service.reconcile()
