"""ソース DB のスキャン(ADR-0041、`P2A-01`)。

Phase 2 柱 A の入口である。**カタログは `P2A-02`(LLM によるオントロジー候補
生成)の入力になる。**

## パスワードをここで受け取らない

登録の本文に秘密の欄が無い(決定4)。**リクエストで受け取ると、ログ・監査・
例外・再送の経路に一斉に載る。** 運用者が Key Vault に自分で入れ、
**名前だけを登録する**。

## 既定ではスキャンを使えない

`SCAN_ALLOWED_HOSTS` に無いホストへは接続しない(決定5)。既定は空である —
**「設定が無ければどこへでも」にしない**(不変条件11)。

## ソースは名前空間に属する

登録と実行は `owner`、閲覧は `data-analyst`(決定8)。「誰がどの顧客 DB へ
接続できるか」を名前空間の境界の外に出さない(不変条件5)。

## 登録と削除は監査に残す。スキャンは残さない

ソースの登録は**このシステムに顧客 DB への到達手段を与える**行為なので、
`audit_events`(追記専用)に残す。削除も残す — **`scan_sources` の行は
消えるが、消したという事実は消えない。**

個々のスキャンは `scan_runs` が誰といつを持っているので、監査に二重で
書かない。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ontology_api.dependencies import (
    CurrentPrincipal,
    ScanSecretDep,
    SessionDep,
    SettingsDep,
)
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.scan import ScanRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.services.authorization import (
    PermissionDeniedError,
    principal_id_of,
    require_namespace_role,
)
from ontology_api.services.scan import ScanService, SourceConnectionError
from ontology_core.graphs import NamespaceNameError, validate_namespace_name
from ontology_core.models import NamespaceRole, ScanRun, ScanSource, ScanTable
from ontology_core.scan import (
    SUPPORTED_DRIVERS,
    HostNotAllowedError,
    ScanAuthMode,
    ScanDriver,
    UnsupportedDriverError,
    validate_driver,
    validate_host,
)

router = APIRouter(prefix="/namespaces", tags=["scan"])


class ScanSourceRegister(BaseModel):
    """ソースの登録要求(ADR-0041 決定4)。

    **秘密の欄が無い。** パスワードは Key Vault に置き、`vault_secret_name` で
    名前だけを渡す。
    """

    name: str = Field(
        min_length=1,
        max_length=63,
        description="運用者が付ける名前。名前空間の中で一意",
        examples=["sales-db"],
    )
    driver: str = Field(
        default=ScanDriver.POSTGRESQL.value,
        description=f"対応しているのは {sorted(SUPPORTED_DRIVERS)} だけ。"
        "**対応外は 422 で断る**(黙って空のカタログを作らない)",
    )
    host: str = Field(
        min_length=1,
        description="**`SCAN_ALLOWED_HOSTS` に無いホストは登録できない**(決定5)",
        examples=["db.example.internal"],
    )
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, examples=["sales"])
    username: str = Field(min_length=1, examples=["ontology_scanner"])
    auth_mode: str = Field(
        default=ScanAuthMode.ENTRA.value,
        description="`entra`(マネージド ID)/ `key-vault-secret`",
    )
    vault_secret_name: str | None = Field(
        default=None,
        description="Key Vault の秘密の**名前**。`key-vault-secret` のときは必須。**値は渡さない**",
    )


class ScanSourceRemove(BaseModel):
    """ソースの削除要求。"""

    reason: str = Field(
        min_length=1,
        description="**必須**。監査証跡に残り、消せない。"
        "**カタログは CASCADE で消えるが、消したという事実は残る**",
    )


async def _prepare(
    session: SessionDep,
    *,
    namespace: str,
    principal: CurrentPrincipal,
    required: NamespaceRole,
) -> None:
    """名前空間名を検証し、存在と権限を確かめる。"""
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


@router.post(
    "/{namespace}/scan-sources",
    status_code=status.HTTP_201_CREATED,
    summary="スキャン対象のソース DB を登録する(`owner`。秘密は受け取らない)",
)
async def register_scan_source(
    namespace: str,
    payload: ScanSourceRegister,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> ScanSource:
    """ソースを登録する。`owner` が必要。

    **パスワードを受け取らない**(ADR-0041 決定4)。リクエストで秘密を受け取ると
    **ログ・監査・例外・再送の経路に一斉に載る**。運用者が Key Vault に入れ、
    `vault_secret_name` で名前だけを渡す。

    **`SCAN_ALLOWED_HOSTS` に無いホストは 403 で断る**(決定5)。登録の時点で
    断るのは、**登録できてしまうと「後で接続できるはず」という誤解が残る**
    ためである。既定は空なので、設定するまでスキャンは使えない。

    **対応していない driver は 422 で断る**(決定9)。黙って空のカタログを
    作ると「テーブルが 1 件も無い DB」として読まれる。
    """
    await _prepare(session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER)

    try:
        validate_driver(payload.driver)
    except UnsupportedDriverError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    if payload.auth_mode not in {mode.value for mode in ScanAuthMode}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"auth_mode は {sorted(m.value for m in ScanAuthMode)} のいずれかです",
        )
    if payload.auth_mode == ScanAuthMode.KEY_VAULT.value and not payload.vault_secret_name:
        # **「秘密の名前が無いまま登録できる」を許さない。** 許すと、
        # スキャンの時点で初めて分かる設定漏れになる。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="auth_mode が key-vault-secret のときは vault_secret_name が必要です",
        )

    try:
        # **規則の定義は 1 か所に置く。** ここで `not in` を書き直すと、
        # 「空なら拒否」(不変条件11)が 2 か所に分かれて片方だけ変わりうる。
        validate_host(payload.host, settings.scan_allowed_host_list)
    except HostNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    repo = ScanRepository(session)
    if await repo.get_source(namespace=namespace, name=payload.name) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ソース '{payload.name}' は既に登録されています",
        )

    actor = principal_id_of(principal)
    created = await repo.create_source(
        namespace=namespace,
        name=payload.name,
        driver=payload.driver,
        host=payload.host,
        port=payload.port,
        database=payload.database,
        username=payload.username,
        auth_mode=payload.auth_mode,
        vault_secret_name=payload.vault_secret_name,
        created_by=actor,
    )
    # **監査に残す。** このシステムに顧客 DB への到達手段を与える行為である。
    # **接続先だけを書き、秘密の名前も書かない** — 監査は広く読めるので
    # (`data-analyst`)、秘密の在り処まで広げない。
    await AuditRepository(session).record(
        namespace=namespace,
        action="scan-source-registered",
        actor=actor,
        actor_type=principal.actor_type,
        subject=f"{namespace}/scan-sources/{payload.name}",
        reason=f"{payload.driver}://{payload.host}:{payload.port}/{payload.database}",
    )
    await session.commit()
    return created


@router.get("/{namespace}/scan-sources", summary="ソースを一覧する(`data-analyst`)")
async def list_scan_sources(
    namespace: str,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> list[ScanSource]:
    """登録済みのソースを返す。`data-analyst` が必要。

    **秘密は含まれない**(そもそも保存していない)。`vault_secret_name` は
    秘密の**名前**であって値ではない。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    return await ScanRepository(session).list_sources(namespace)


@router.post(
    "/{namespace}/scan-sources/{name}/remove",
    summary="ソースを削除する(`owner`。理由必須)",
)
async def remove_scan_source(
    namespace: str,
    name: str,
    payload: ScanSourceRemove,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> dict[str, Any]:
    """ソースを削除する。`owner` が必要。

    **`DELETE` ではなく `POST` である。** このリポジトリは**理由が必須の
    操作を本文で受ける**形に揃えている(`/retire`、`/reject`、
    `/access-log/purge`、想定質問の改訂)。`DELETE` に本文を付ける形は
    プロキシやクライアントによって落とされるうえ、理由をクエリに載せると
    アクセスログへ流れる。

    **カタログも CASCADE で消える。** 観測は積む設計だが(決定6)、ソースを
    消すのは運用者の明示的な操作である。**消したという事実は監査に残る** —
    `audit_events` は追記専用なので消えない。
    """
    await _prepare(session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER)
    repo = ScanRepository(session)
    if not await repo.delete_source(namespace=namespace, name=name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ソース '{name}' が見つかりません",
        )
    await AuditRepository(session).record(
        namespace=namespace,
        action="scan-source-removed",
        actor=principal_id_of(principal),
        actor_type=principal.actor_type,
        subject=f"{namespace}/scan-sources/{name}",
        reason=payload.reason,
    )
    await session.commit()
    return {"namespace": namespace, "name": name, "removed": True}


@router.post(
    "/{namespace}/scan-sources/{name}/scan",
    summary="スキャンを 1 回実行する(`owner`。メタデータだけを読む)",
)
async def run_scan(
    namespace: str,
    name: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    secret_resolver: ScanSecretDep,
) -> dict[str, Any]:
    """ソース DB のメタデータを読んでカタログへ積む。`owner` が必要。

    **実データを 1 行も読まない**(ADR-0041 決定1)。発行するのは
    `ontology_core.scan.CATALOG_QUERIES` だけである。このカタログは `P2A-02`
    (LLM によるオントロジー候補生成)の入力になるので、**サンプリングした
    実データはプロンプトへ流れて消せなくなる**。

    **統計は「無い」と「0」を区別する**(決定2)。`ANALYZE` が走っていない
    テーブルの行数は `null` で、`0` は「測った 0」である。`n_distinct` の
    負の値は**比率**なので、絶対数に換算せず別の欄に入れる。

    **途中で落ちた run は `running` のまま残る**(決定7)。読み手は
    `succeeded` で絞る — 半端なカタログを「テーブルが少ない DB」として
    読ませない。
    """
    await _prepare(session, namespace=namespace, principal=principal, required=NamespaceRole.OWNER)
    repo = ScanRepository(session)
    source = await repo.get_source(namespace=namespace, name=name)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ソース '{name}' が見つかりません",
        )

    service = ScanService(session=session, settings=settings, secret_resolver=secret_resolver)
    try:
        run_id, table_count = await service.scan(source=source, actor=principal_id_of(principal))
    except HostNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except UnsupportedDriverError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except SourceConnectionError as exc:
        # **502 にする。** こちらの入力の誤りではなく、外部の依存に届かなかった
        # ことである(`SparqlStoreError` を 502 にしているのと同じ判断)。
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return {
        "namespace": namespace,
        "source": name,
        "run_id": run_id,
        "table_count": table_count,
    }


@router.get(
    "/{namespace}/scan-sources/{name}/runs",
    summary="スキャンの履歴を新しい順に返す(`data-analyst`)",
)
async def list_scan_runs(
    namespace: str,
    name: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200, description="返す件数")] = 50,
) -> list[ScanRun]:
    """スキャンの履歴を返す。`data-analyst` が必要。

    **`status` を必ず見ること。** `running` のまま残った run は「完了して
    いない観測」である(決定7)。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    repo = ScanRepository(session)
    source = await repo.get_source(namespace=namespace, name=name)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ソース '{name}' が見つかりません",
        )
    return await repo.list_runs(source_id=source.id, limit=limit)


@router.get(
    "/{namespace}/scan-sources/{name}/catalog",
    summary="最後に完了したスキャンのカタログを返す(`data-analyst`)",
)
async def get_scan_catalog(
    namespace: str,
    name: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    run_id: Annotated[
        int | None,
        Query(description="特定の run を指定する。省略時は**最後に完了した** run"),
    ] = None,
) -> dict[str, Any]:
    """カタログを返す。`data-analyst` が必要。

    **省略時は `succeeded` の run だけを選ぶ**(ADR-0041 決定7)。`running` や
    `failed` の run を「これが今のスキーマ」として読ませない。

    **`run_id` を明示すれば `failed` の run も読める** — どこまで読めたかは
    診断の材料である。そのとき応答の `run` に状態が入るので、読み手は
    半端な観測だと分かる。

    完了した run が無ければ `run` が `null`、`tables` が空になる。
    **404 にしない** — 「まだスキャンしていない」はエラーではなく、この API が
    答えるべき事実である。
    """
    await _prepare(
        session, namespace=namespace, principal=principal, required=NamespaceRole.DATA_ANALYST
    )
    repo = ScanRepository(session)
    source = await repo.get_source(namespace=namespace, name=name)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ソース '{name}' が見つかりません",
        )

    if run_id is None:
        run = await repo.latest_succeeded_run(source_id=source.id)
    else:
        run = await repo.get_run(source_id=source.id, run_id=run_id)
        if run is None:
            # **名前空間とソースで絞ってから引く。** 他のソースの run を
            # id だけで読めてはいけない(不変条件5)。
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"run {run_id} が見つかりません",
            )

    tables: list[ScanTable] = [] if run is None else await repo.list_catalog(run_id=run.id)
    return {
        "namespace": namespace,
        "source": name,
        "run": None if run is None else run.model_dump(mode="json"),
        "tables": [table.model_dump(mode="json") for table in tables],
    }
