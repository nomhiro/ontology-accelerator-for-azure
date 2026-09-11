"""名前空間の管理。

正本は PostgreSQL、射影は Fuseki のデータセットである。作成は「正本(DB)に書く →
射影先(データセット)を作る」の順で固定する。データセット作成に失敗しても名前空間は
残り、reconcile が後から埋める(`docs/adr/0002-triple-store-as-rebuildable-projection.md`)。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import (
    BlobDep,
    CurrentPrincipal,
    SessionDep,
    SettingsDep,
    StoreDep,
)
from ontology_api.repositories.namespaces import NamespaceExistsError, NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.services.authorization import (
    NamespaceRetiredError,
    PermissionDeniedError,
    effective_role,
    principal_id_of,
    require_namespace_role,
    require_platform_admin,
)
from ontology_api.services.projection import (
    ProjectionService,
    UnknownNamespaceError,
)
from ontology_core.blob import BlobStoreError
from ontology_core.graphs import NamespaceNameError, dataset_name, validate_namespace_name
from ontology_core.models import Namespace, NamespaceRole, NamespaceRoleAssignment
from ontology_core.sparql.client import SparqlStoreError

router = APIRouter(prefix="/namespaces", tags=["namespaces"])
logger = logging.getLogger(__name__)


class NamespaceRetire(BaseModel):
    """退役 / 退役の解除の要求(ADR-0032)。"""

    reason: str = Field(
        min_length=1,
        description="**必須**。監査証跡に残り、消せない。「なぜこの名前空間を使わなくなったか」を書く",
    )


class RetireResult(BaseModel):
    """退役 / 解除の結果(ADR-0032)。"""

    namespace: Namespace
    note: str = Field(
        description="運用者への申し送り。**解除の直後はストアが空である**ことを含む(ADR-0032 決定3)"
    )


class NamespaceCreate(BaseModel):
    """名前空間の作成要求。"""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$", examples=["retail-core"])
    display_name: str = Field(examples=["小売ドメイン"])
    description: str = ""
    base_iri: str = Field(examples=["https://example.com/ontology/retail#"])
    # 四眼原則(ADR-0014 決定4)。**既定は有効**。
    # 無効にするのは明示的な選択であるべきで、逆(既定で緩く、締めるのを忘れる)は
    # 事故になる。同梱サンプルの名前空間だけ postdeploy が false を渡す。
    require_two_person_approval: bool = Field(
        default=True,
        description="有効なら、その版を publish した主体は approve できない",
    )


class RoleGrant(BaseModel):
    """ロール付与の要求(ADR-0014)。"""

    principal_id: str = Field(
        description="Entra のオブジェクト ID。UPN や表示名ではない",
        examples=["00000000-0000-0000-0000-000000000000"],
    )
    role: NamespaceRole = Field(examples=[NamespaceRole.DATA_STEWARD])


@router.get("", summary="名前空間の一覧を取得する")
async def list_namespaces(principal: CurrentPrincipal, session: SessionDep) -> list[Namespace]:
    """呼び出し元が参照できる名前空間を返す(P2A-06、ADR-0014)。

    **権限の無い名前空間は返さない。** 名前だけでも漏れると、どのドメインの
    オントロジーを持っているかが分かってしまう。`platform-admin` は
    すべての名前空間で `owner` として扱われるため全件見える。

    権限が無い場合は 403 ではなく**空配列**を返す。一覧は「見えるものを返す」
    操作であり、見えるものが 0 件であることは異常ではない。
    """
    all_namespaces = await NamespaceRepository(session).list_all()
    visible: list[Namespace] = []
    for candidate in all_namespaces:
        role = await effective_role(session, namespace=candidate.name, principal=principal)
        if role is not None and role.covers(NamespaceRole.DATA_ANALYST):
            visible.append(candidate)
    return visible


@router.post("", status_code=status.HTTP_201_CREATED, summary="名前空間を作成する")
async def create_namespace(
    payload: NamespaceCreate,
    principal: CurrentPrincipal,
    session: SessionDep,
    store: StoreDep,
) -> Namespace:
    """名前空間を作成し、対応する Fuseki データセットを用意する。

    順序は「正本(DB)に書く → 射影先(データセット)を作る」。逆にしない。
    データセット作成に失敗した場合も名前空間は残る。reconcile が後から埋める。

    **作成には `platform-admin` が必要**(ADR-0014 決定3)。名前空間がまだ
    無いので名前空間ロールでは判定できない。`AUTH_MODE=disabled` の
    ローカル開発では `Principal.local_dev()` が `platform-admin` を持つ。

    **作成した主体に `owner` を自動付与する。** 同じトランザクションで行う。
    そうしないと**作った本人が何もできない名前空間**ができる。
    """
    try:
        require_platform_admin(principal)
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    repo = NamespaceRepository(session)
    creator = principal_id_of(principal)
    try:
        namespace = await repo.create(
            name=payload.name,
            display_name=payload.display_name,
            description=payload.description,
            base_iri=payload.base_iri,
            created_by=creator,
            require_two_person_approval=payload.require_two_person_approval,
        )
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except NamespaceExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await RoleRepository(session).grant(
        namespace=namespace.name,
        principal_id=creator,
        role=NamespaceRole.OWNER,
        granted_by=creator,
    )

    try:
        await store.create_dataset(dataset_name(namespace.name))
    except SparqlStoreError:
        logger.exception(
            "名前空間 '%s' のデータセット作成に失敗しました。reconcile で回復します",
            namespace.name,
        )

    return namespace


@router.get("/{namespace}", summary="名前空間を 1 件取得する")
async def get_namespace(
    namespace: str, principal: CurrentPrincipal, session: SessionDep
) -> Namespace:
    """名前空間を取得する。`data-analyst` 以上が必要(ADR-0014 決定2)。

    **存在確認を権限確認より先に行う。** 逆にすると、権限の無い呼び出し元が
    403 と 404 の違いから名前空間の存在を推測できてしまう……という懸念は
    あるが、ここでは**存在しないものに 404 を返す**方を採る。名前空間名は
    一覧 API で権限のある範囲しか見えないため、名前を当てるには既に名前を
    知っている必要がある。403/404 を統一して隠すのは、運用時の切り分けを
    難しくする割に得るものが小さい。
    """
    # **パスパラメータと取得結果で名前を分ける。** 同じ名前にすると
    # 404 のメッセージが `Namespace` オブジェクトの repr になる。
    found = await NamespaceRepository(session).get(namespace)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{namespace}' が見つかりません",
        )
    try:
        await require_namespace_role(
            session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return found


async def _retire_common(
    *,
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
    reason: str,
    unretire: bool,
) -> RetireResult:
    """退役 / 解除の共通処理(ADR-0032)。

    **`owner` が必要。** 名前空間の退役は射影を止める操作なので、削除と同じ
    最上位に置く(ADR-0014 決定2)。
    """
    try:
        validate_namespace_name(namespace)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    try:
        await require_namespace_role(
            session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    service = ProjectionService(
        session=session,
        blob=blob,
        store=store,
        graph_iri_base=settings.graph_iri_base,
        retain_superseded=settings.superseded_retain,
    )
    actor = principal_id_of(principal)
    try:
        if unretire:
            updated = await service.unretire(
                namespace=namespace,
                actor=actor,
                reason=reason,
                actor_type=principal.actor_type,
            )
            note = (
                "退役を解除しました。**この時点でストアは空です** — "
                "POST /admin/reconcile を実行するか、次のレプリカ再作成を待って"
                "ください(ADR-0032 決定3)。"
            )
        else:
            updated = await service.retire(
                namespace=namespace,
                actor=actor,
                reason=reason,
                actor_type=principal.actor_type,
            )
            note = (
                "退役しました。**正本(Blob の TTL・版・監査)は残っています**"
                "(不変条件7)。止まったのは射影と内容の増設だけです。"
                "版の一覧・決定記録・監査・PROV-O は引き続き読めます。"
            )
    except UnknownNamespaceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NamespaceRetiredError as exc:
        # **403 にしない。** 権限の問題ではないのでロールを付与しても解決しない。
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RetireResult(namespace=updated, note=note)


@router.post(
    "/{namespace}/retire",
    summary="名前空間を退役させる(削除ではない。`owner` が必要)",
)
async def retire_namespace(
    namespace: str,
    payload: NamespaceRetire,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
) -> RetireResult:
    """名前空間を退役させる([ADR-0032](../../../../../docs/adr/0032-namespace-retirement.md))。

    **削除ではない。** Blob の TTL も PostgreSQL の版と監査もそのまま残る
    (不変条件7)。止まるのは**射影**と**内容の増設**だけである。

    | 経路 | 退役中 |
    |---|---|
    | `publish` / `submit` / `approve` / `reject` | **409** |
    | `POST .../sparql` | **409**(0 行を静かに返さない) |
    | `POST .../questions`(改訂) | **409** |
    | `PUT .../mappings`(宣言) | **409** |
    | 版の一覧・決定記録・監査・PROV-O・健全性・差分 | 通る |
    | ロール・用語の責任者・マッピングの取り消し | 通る(片付け) |

    **`DELETE /namespaces/{ns}` が 409 で案内する先がここである。** 公開済み
    オントロジーを含む名前空間は削除しない(ADR-0024 決定4 の選択肢 2)。

    **戻せる**(`POST .../unretire`)。正本は無傷なので、戻すのは列を消すだけ
    である。
    """
    return await _retire_common(
        namespace=namespace,
        principal=principal,
        session=session,
        blob=blob,
        store=store,
        settings=settings,
        reason=payload.reason,
        unretire=False,
    )


@router.post(
    "/{namespace}/unretire",
    summary="名前空間の退役を解除する(`owner` が必要)",
)
async def unretire_namespace(
    namespace: str,
    payload: NamespaceRetire,
    principal: CurrentPrincipal,
    session: SessionDep,
    blob: BlobDep,
    store: StoreDep,
    settings: SettingsDep,
) -> RetireResult:
    """退役を解除する(ADR-0032 決定1)。

    **解除の直後はストアが空である。** `retire` が `projected_at` を `NULL` に
    戻してあるので、`POST /admin/reconcile` を回すか次のレプリカ再作成を
    待てば射影が戻る。**応答の `note` がそのことを伝える。**

    退役していない名前空間に対しては **409** を返す(何も起きないことを
    成功として返さない)。
    """
    return await _retire_common(
        namespace=namespace,
        principal=principal,
        session=session,
        blob=blob,
        store=store,
        settings=settings,
        reason=payload.reason,
        unretire=True,
    )


@router.delete(
    "/{namespace}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="名前空間を削除する",
)
async def delete_namespace(
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    store: StoreDep,
    blob: BlobDep,
) -> None:
    """名前空間を削除する。

    順序は作成と対にして「正本(DB)から消す → 射影先(データセット)を消す」にする。

    ただし Blob の当該名前空間プレフィックス配下に公開済み TTL が 1 件でも残って
    いれば 409 Conflict で拒否し、PG 行・データセットのどちらも変更しない。
    `containers/fuseki/load-snapshot.sh` は PostgreSQL を一切見ず Blob だけを見て
    名前空間ごとの TDB2 を再構築するため、Blob を消さずに PG 行だけ消すと
    レプリカ再作成(デプロイ・スケールイベント等)で削除済みのオントロジーが
    復活し、認証済みの呼び出し元に返ってしまう(ブランチ全体レビュー C-1)。
    判定を PostgreSQL の版数に依拠すると、publish 失敗で残った孤児 TTL
    (PG に記録される前に Blob 書き込みだけ成功した場合)を見逃すため、
    **復活源そのものである Blob 本体**を見る。オントロジーは不変リビジョン
    (ADR-0006)なので、この削除経路で Blob を消す実装にはしない。公開済み
    オントロジーを含む名前空間の削除は Phase 2(監査経路)で対応する。

    **削除には `owner` が必要**(ADR-0014 決定2)。名前空間の削除は
    取り返しがつかない操作なので最上位に置く。
    """
    # `namespace` はこの後 Blob のプレフィックス・Fuseki のデータセット名の組み立てに
    # 使われる。DB に存在しない名前空間なら結局 404 になるが、それは「たまたま
    # 検証されている」だけであり、`publish_version` / `list_versions` /
    # `run_query` と同じく名前空間名がセキュリティ境界であることの明示的な契約に
    # するため、パスパラメータの入口で検証する(final-fix-brief.md 修正5 / O-2)。
    try:
        validate_namespace_name(namespace)
    except NamespaceNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        await require_namespace_role(
            session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    # **行ロックを取る**(ADR-0024 決定1)。**位置が本質** — 下の Blob の
    # 検査より前でなければならない。`publish` も同じ行ロックを取るので、
    # 「Blob は空か」の確認から行の削除までの間に publish が割り込めない。
    #
    # これが無いと、publish が Blob に `.ttl` を書いた直後にこの削除が
    # commit してしまい、**Blob に TTL があって PostgreSQL には何も無い**
    # 状態が残る。ローダは Blob だけを見て再構築するので、削除したはずの
    # 名前空間が次のレプリカ再作成で復活する(`P2B-12`)。
    if await NamespaceRepository(session).get_locked(namespace) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{namespace}' が見つかりません",
        )

    # Blob 一覧が取れないときは**削除を進めない**(fail-closed)。Blob は
    # ローダが再構築の入力に使う「復活源」であり、中身を確認できないまま
    # 名前空間を消すと、消えたように見えて後から復活する状態を作る
    # (review-branch-report.md の C-1)。生の BlobStoreError を漏らすと
    # 原因の分からない 500 になるので、意図した拒否として 503 で返す。
    try:
        remaining = await blob.list_versions(namespace=namespace)
    except BlobStoreError as exc:
        logger.exception("名前空間 '%s' の Blob 一覧を取得できませんでした", namespace)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "正本の Blob ストレージに到達できないため、削除を中止しました。"
                "削除済みデータが射影の再構築で復活するのを防ぐため、Blob の"
                f"中身を確認できない状態では削除しません: {exc}"
            ),
        ) from exc
    if remaining:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"名前空間 '{namespace}' には公開済みオントロジーの Blob が "
                f"{len(remaining)} 件残っているため削除できません: "
                f"{', '.join(remaining)}. "
                "**公開済みオントロジーは削除しません**(不変条件7)。"
                f"使わなくなった名前空間は POST /namespaces/{namespace}/retire で"
                "退役させてください(正本は残り、射影と内容の増設だけが止まります。"
                "ADR-0032)。"
            ),
        )

    deleted = await NamespaceRepository(session).delete(namespace)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{namespace}' が見つかりません",
        )

    # 正本(PostgreSQL)の削除をここで commit してから射影(データセット)を消す。
    # `SessionDep`(`session_scope`)はリクエスト終了後にしか commit しないため、
    # ここで確定させないと実行順は「PG(未コミット) → Fuseki(耐久化) → PG commit」
    # になり、途中で落ちると PG 行が残ったままデータセットだけ消える(publish 側で
    # `ProjectionService.publish` が確立した「耐久化は正本 → 射影の順」という原則が
    # 削除側では守られていない状態になる。final-fix-brief.md 修正6 / O-3)。
    await session.commit()

    try:
        await store.delete_dataset(dataset_name(namespace))
    except SparqlStoreError:
        logger.exception(
            "名前空間 '%s' のデータセット削除に失敗しました。reconcile で回復します",
            namespace,
        )


@router.get("/{namespace}/roles", summary="名前空間のロール付与を一覧する")
async def list_namespace_roles(
    namespace: str, principal: CurrentPrincipal, session: SessionDep
) -> list[NamespaceRoleAssignment]:
    """付与の一覧を返す。`owner` が必要(ADR-0014 決定2)。

    **`data-analyst` には見せない。** 誰がどの権限を持っているかは、
    その名前空間を管理する立場の人が知るべき情報である。
    """
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
            session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return await RoleRepository(session).list_for(namespace)


@router.put("/{namespace}/roles", summary="ロールを付与する(既存があれば置き換える)")
async def grant_namespace_role(
    namespace: str,
    payload: RoleGrant,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> NamespaceRoleAssignment:
    """ロールを付与する。`owner` が必要(ADR-0014 決定2)。

    **冪等である**(既にあれば置き換える)。1 人が 1 つの名前空間に持つロールは
    1 つなので、付与のやり直しは昇格・降格であり重複エラーにする理由がない。
    """
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
            session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    return await RoleRepository(session).grant(
        namespace=namespace,
        principal_id=payload.principal_id,
        role=payload.role,
        granted_by=principal_id_of(principal),
    )


@router.delete(
    "/{namespace}/roles/{principal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="ロールを取り消す",
)
async def revoke_namespace_role(
    namespace: str,
    principal_id: str,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> None:
    """ロールを取り消す。`owner` が必要(ADR-0014 決定2)。

    **最後の `owner` は取り消せない**(409)。取り消した結果、誰もその名前空間を
    管理できなくなる。回復には `platform-admin` が必要になり、それを持たない
    利用者は手詰まりになる。`platform-admin` 自身の暗黙の owner は
    ここでは数に入れない(付与として存在しないため)。
    """
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
            session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    repo = RoleRepository(session)
    assignments = await repo.list_for(namespace)
    target = next((a for a in assignments if a.principal_id == principal_id), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"名前空間 '{namespace}' に '{principal_id}' の付与はありません",
        )
    if target.role is NamespaceRole.OWNER:
        owners = sum(1 for a in assignments if a.role is NamespaceRole.OWNER)
        if owners <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"名前空間 '{namespace}' の最後の owner は取り消せません。"
                    "先に別の主体へ owner を付与してください"
                ),
            )
    await repo.revoke(namespace=namespace, principal_id=principal_id)
