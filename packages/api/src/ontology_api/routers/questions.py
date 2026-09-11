"""想定質問の集合(ADR-0022、`P2B-14`)。

## 受け入れ基準を書き換えられるなら、通ったことは保証にならない

質問集合は**名前空間ごとの不変改訂**である(決定1・2)。版ごとに持つと
新しい版が自分の合格条件を自分で書き換えられ、テストが通ったことが何の
保証にもならなくなる。四眼原則(ADR-0014 決定4)と同じ論点である。

## 書き込みは `owner`、読み取りは `data-analyst`

`approve` は `maintainer` 以上である。**承認する主体が合格条件も書き換え
られると検査が意味を失う**ので、書き込みは 1 段上に置く(決定6)。

**ただしこれは「書き換えを防ぐ」仕組みではなく「書き換えが見える」仕組み
である。** ロールは階層なので `owner` は基準を書き換えて自分で承認できる。
その代わり改訂は不変で、`reason` が必須で、監査に残り、**減った質問の id が
監査の記録に出る**。防止ではなく可視化であることを、ここに書いておく。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import BlobDep, CurrentPrincipal, SessionDep, SettingsDep, StoreDep
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.questions import QuestionSetRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.services.authorization import (
    NamespaceRetiredError,
    PermissionDeniedError,
    ensure_not_retired,
    require_namespace_role,
)
from ontology_api.services.projection import (
    CompetencyEvaluationError,
    ProjectionService,
    UnknownVersionError,
)
from ontology_core.blob import BlobStoreError
from ontology_core.competency import (
    MAX_QUESTION_SET_BYTES,
    CompetencyReport,
    QuestionFileError,
    parse_questions,
)
from ontology_core.graphs import NamespaceNameError, validate_namespace_name, validate_version
from ontology_core.models import (
    CompetencyQuestionOutcome,
    CompetencyRunReport,
    NamespaceRole,
    QuestionSet,
    QuestionSetSummary,
)

router = APIRouter(prefix="/namespaces", tags=["competency-questions"])


class QuestionSetRevise(BaseModel):
    """質問集合の新しい改訂(ADR-0022 決定1)。"""

    content: str = Field(
        description="質問ファイル(YAML)の本文。`samples/retail-core.questions.yaml` と同じ形式",
        max_length=MAX_QUESTION_SET_BYTES,
    )
    reason: str = Field(
        min_length=1,
        description="この改訂を入れる理由。**必須である** — "
        "基準を緩めたことが理由なしに起きてはいけない(ADR-0022 決定6)",
    )


async def _prepare(
    session: SessionDep,
    *,
    namespace: str,
    principal: CurrentPrincipal,
    required: NamespaceRole,
) -> None:
    """名前空間名を検証し、存在と権限を確かめる(`term_owners.py` と同じ形)。"""
    try:
        validate_namespace_name(namespace)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if await NamespaceRepository(session).get(namespace) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{namespace}' が見つかりません",
        )
    try:
        await require_namespace_role(
            session, namespace=namespace, principal=principal, required=required
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def _to_outcomes(report: CompetencyReport) -> tuple[CompetencyQuestionOutcome, ...]:
    return tuple(
        CompetencyQuestionOutcome(
            id=r.question.id,
            question=r.question.question,
            expect=r.question.expect.value,
            passed=r.passed,
            detail=r.detail,
        )
        for r in report.results
    )


@router.get("/{namespace}/questions", summary="有効な想定質問の集合を返す")
async def get_question_set(
    namespace: str, principal: CurrentPrincipal, session: SessionDep
) -> QuestionSet:
    """有効な改訂(最大の `revision`)を本文込みで返す。`data-analyst` で読める。

    **質問集合が無ければ 404 にする。** 「基準を定めていない」は事実だが、
    本文を返す口としては返すものが無い。**定めていないことを知りたい経路は
    健全性指標(`competency_question_count`)である**(ADR-0022 決定7)。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    question_set = await QuestionSetRepository(session).active(namespace)
    if question_set is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{namespace}' には想定質問の集合がありません"
            "(基準を定めていない状態です。承認はブロックされません)",
        )
    return question_set


@router.get("/{namespace}/questions/revisions", summary="想定質問の集合の改訂を一覧する")
async def list_question_set_revisions(
    namespace: str, principal: CurrentPrincipal, session: SessionDep
) -> list[QuestionSetSummary]:
    """改訂の履歴を新しい順に返す(本文は含まない)。`data-analyst` で読める。

    **基準がいつ・誰に・なぜ変えられたかの履歴そのものである。** ADR-0022
    決定6 の「防止ではなく可視化」がここで効く。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    return await QuestionSetRepository(session).revisions(namespace)


@router.post(
    "/{namespace}/questions",
    status_code=status.HTTP_201_CREATED,
    summary="想定質問の集合に新しい改訂を足す(`owner` が必要)",
)
async def revise_question_set(
    namespace: str,
    payload: QuestionSetRevise,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> QuestionSet:
    """新しい改訂を足す。**`owner` が必要**(ADR-0022 決定6)。

    **`PUT` ではなく `POST` である。** 既存の改訂を置き換えるのではなく、
    新しい改訂を作る(改訂は不変。決定1)。

    **本文は保存する前に解析して検証する。** 検証を通らない質問集合を保存
    すると、`approve` が「評価できなかった」で永久に止まる名前空間ができる。
    不正な本文は **422** である。

    **減った質問の id を監査に残す。** 基準を緩めたことが一覧できるように
    するため(決定6)。
    """
    await _prepare(session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER)
    # **退役した名前空間では基準を改訂しない**(ADR-0032 決定5)。
    # 作れない版の受け入れ基準を書き換える意味が無い。
    try:
        await ensure_not_retired(session, namespace=namespace, doing="想定質問の改訂")
    except NamespaceRetiredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    try:
        questions = parse_questions(payload.content, where=f"'{namespace}' の質問集合")
    except QuestionFileError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    repository = QuestionSetRepository(session)
    previous = await repository.active(namespace)
    removed: list[str] = []
    if previous is not None:
        try:
            previous_ids = {q.id for q in parse_questions(previous.content)}
        except QuestionFileError:
            # 前の改訂が壊れていても新しい改訂は入れられるべきである
            # (壊れた状態から抜け出す唯一の手段がこの口である)。
            previous_ids = set()
        removed = sorted(previous_ids - {q.id for q in questions})

    actor = principal.object_id or principal.subject
    created = await repository.add(
        namespace=namespace,
        content=payload.content,
        question_count=len(questions),
        actor=actor,
        reason=payload.reason,
    )

    note = f"質問 {created.question_count} 件"
    if removed:
        note += f" / 削除された質問: {', '.join(removed)}"
    await AuditRepository(session).record(
        namespace=namespace,
        action="questions-revised",
        actor=actor,
        subject=f"{namespace}#questions@{created.revision}",
        reason=f"{payload.reason} / {note}",
    )
    # **同一トランザクションで書く。** これがこのテーブルを PostgreSQL に
    # 置いた理由である(ADR-0022 決定1)。
    await session.commit()
    return created


@router.post(
    "/{namespace}/versions/{version}/questions/run",
    summary="版が想定質問に答えられるか試す(状態は変えない)",
)
async def run_question_set(
    namespace: str,
    version: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
) -> CompetencyRunReport:
    """想定質問を評価して報告を返す。**状態は変えない**(ADR-0022 決定9)。

    **これが無いと、承認を試して 422 を食らうまで基準を満たすか分からない。**
    SHACL の `.../validate` と同じ位置づけである。

    評価は**正本の TTL** に対して行う(決定3)。ストアには問い合わせない。

    **質問集合が無ければ `revision` が `None` で `conforms` は真になる。**
    「基準を定めていない」は「基準を満たしていない」ではない(決定7)。

    **`criteria_self_revised` が埋まっていたら必ず読むこと**(ADR-0029)。
    その版を書いた主体が、版を publish した後に受け入れ基準を書き換えている。
    四眼原則が有効な名前空間では `approve` が 422 で止まる。
    **無効な名前空間でも埋まる** — 止まらないが、承認する前に見えるべき
    事実である(決定6)。
    """
    try:
        validate_namespace_name(namespace)
        validate_version(version)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    service = ProjectionService(
        session=session,
        blob=blob,
        store=store,
        graph_iri_base=settings.graph_iri_base,
        retain_superseded=settings.superseded_retain,
    )
    try:
        revision, report = await service.evaluate_competency_questions(
            namespace=namespace, version=version
        )
    except UnknownVersionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (BlobStoreError, CompetencyEvaluationError) as exc:
        # 「評価できなかった」を合格として返さない(ADR-0022 決定3・5)。
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"想定質問を評価できませんでした: {exc}",
        ) from exc

    # **基準の出自も返す**(ADR-0029 決定6)。四眼原則の設定に関わらず、
    # 事実は見えるべきである。止めるのは `approve` の仕事。
    self_revision = await service.check_criteria_authorship(namespace=namespace, version=version)

    return CompetencyRunReport(
        namespace=namespace,
        version=version,
        revision=revision,
        conforms=report.conforms,
        results=_to_outcomes(report),
        not_evaluated=report.not_evaluated,
        elapsed_seconds=report.elapsed_seconds,
        criteria_self_revised=None if self_revision is None else self_revision.message(),
    )
