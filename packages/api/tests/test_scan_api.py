"""ソース DB のスキャン(ADR-0041、`P2A-01`)。

**このファイルでいちばん重要なのは `test_実物の_PostgreSQL_を読む` である。**
自分自身の DB をスキャンして、**実物のカタログに対して**スキーマが読めることを
確かめる。フェイクでは `information_schema` の書き方も `reltuples` の
`-1` も再現できない。

ここで固定するのは 5 つである。

1. **秘密を受け取らない**(決定4)。登録の本文に秘密の欄が無い
2. **既定ではスキャンを使えない**(決定5)。`SCAN_ALLOWED_HOSTS` が空なら 403
3. **「無い」と「0」を区別する**(決定2)。`ANALYZE` が走っていない行数は `null`
4. **途中で落ちた run と完了した run を混ぜない**(決定7)
5. **登録と削除が監査に残る。** `scan_sources` の行は消えるが、消したという
   事実は消えない
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.scan import ScanRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.routers.scan import (
    ScanSourceRegister,
    ScanSourceRemove,
    get_scan_catalog,
    list_scan_runs,
    list_scan_sources,
    register_scan_source,
    remove_scan_source,
    run_scan,
)
from ontology_core.auth.entra import Principal
from ontology_core.config import AuthMode, Settings
from ontology_core.db import ScanSourceRow
from ontology_core.models import ActorType, NamespaceRole, PlatformRole

_NS = "scan-ns"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_OWNER = Principal(subject="owner", object_id="owner-oid", actor_type=ActorType.USER)
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")

#: ローカルの PostgreSQL。**自分自身をスキャンする。**
_HOST = os.environ.get("POSTGRES_HOST", "localhost")
_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
_DATABASE = os.environ.get("POSTGRES_DATABASE", "ontology")
_USER = os.environ.get("POSTGRES_USER", "ontology")
_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "localdev")


def _settings(*, allowed: str | None = None) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        AUTH_MODE=AuthMode.DISABLED,
        SCAN_ALLOWED_HOSTS=_HOST if allowed is None else allowed,
    )


async def _local_password(source: ScanSourceRow, settings: Settings) -> str | None:
    """ローカルの PostgreSQL のパスワードを返す差し替え。

    **本番の経路(マネージド ID / Key Vault)はローカルで検証できない。**
    そこで**秘密の解決だけを差し替え、スキャン本体は実物に対して確かめる**。
    """
    return _PASSWORD


async def _setup(session: AsyncSession) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri="https://e.example/scan#",
        created_by=_ADMIN.object_id,
    )
    repo = RoleRepository(session)
    for principal, role in (
        (_OWNER, NamespaceRole.OWNER),
        (_ANALYST, NamespaceRole.DATA_ANALYST),
    ):
        await repo.grant(
            namespace=_NS,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await session.commit()


def _payload(**kwargs: Any) -> ScanSourceRegister:
    base: dict[str, Any] = {
        "name": "self",
        "driver": "postgresql",
        "host": _HOST,
        "port": _PORT,
        "database": _DATABASE,
        "username": _USER,
        "auth_mode": "entra",
    }
    return ScanSourceRegister(**{**base, **kwargs})


async def _register(
    session: AsyncSession,
    *,
    principal: Principal = _OWNER,
    settings: Settings | None = None,
    **kwargs: Any,
) -> Any:
    return await register_scan_source(
        namespace=_NS,
        payload=_payload(**kwargs),
        principal=principal,
        session=session,
        settings=settings or _settings(),
    )


# ------------------------------------- 秘密を受け取らない(決定4)


def test_登録の本文に秘密の欄が無い() -> None:
    """**これは構造で固定する価値がある**(ADR-0041 決定4)。

    リクエストで秘密を受け取ると、**ログ・監査・例外・再送の経路に一斉に
    載る**。欄を足した瞬間にこのテストが落ちる。
    """
    # **欄を足したらここが落ちる。** 落ちたら、その欄が秘密の**値**を運んで
    # いないことを確かめてから加える。
    # `vault_secret_name` は Key Vault の秘密の**名前**であって値ではない。
    assert set(ScanSourceRegister.model_fields) == {
        "name",
        "driver",
        "host",
        "port",
        "database",
        "username",
        "auth_mode",
        "vault_secret_name",
    }


@pytest.mark.integration
async def test_保存するのは秘密の名前だけ(session: AsyncSession) -> None:
    await _setup(session)
    created = await _register(
        session, name="vaulted", auth_mode="key-vault-secret", vault_secret_name="sales-db-password"
    )
    assert created.vault_secret_name == "sales-db-password"
    row = await ScanRepository(session).get_source(namespace=_NS, name="vaulted")
    assert row is not None
    # **値を持つ列が無い。** 列が増えたらここで気づく。
    assert not any("password" in column.name for column in row.__table__.columns)


@pytest.mark.integration
async def test_key_vault_なのに名前が無ければ_422(session: AsyncSession) -> None:
    """**スキャンの時点で初めて分かる設定漏れにしない。**"""
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _register(session, auth_mode="key-vault-secret")
    assert exc.value.status_code == 422


# --------------------------------- 既定では使えない(決定5)


@pytest.mark.integration
async def test_allowlist_が空なら登録できない(session: AsyncSession) -> None:
    """**「設定が無ければどこへでも」にしない**(ADR-0041 決定5)。

    任意のホストへ接続できる口は、認証済みの主体に**内部ネットワークの
    到達性を調べる手段**を与える。SPARQL の `SERVICE` を既定で禁止したのと
    同じ判断である。

    **登録の時点で断る** — 登録できてしまうと「後で接続できるはず」という
    誤解が残る。
    """
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _register(session, settings=_settings(allowed=""))
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_allowlist_に無いホストは登録できない(session: AsyncSession) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _register(session, host="evil.example", settings=_settings(allowed="db.example"))
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_対応外の_driver_は_422(session: AsyncSession) -> None:
    """**黙って空のカタログを作らない**(決定9)。"""
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _register(session, driver="mysql")
    assert exc.value.status_code == 422


# ------------------------------------------------- 権限(決定8)


@pytest.mark.integration
async def test_登録には_owner_が必要(session: AsyncSession) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _register(session, principal=_ANALYST)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_一覧は_data_analyst_で読める(session: AsyncSession) -> None:
    await _setup(session)
    await _register(session)
    listed = await list_scan_sources(namespace=_NS, principal=_ANALYST, session=session)
    assert [s.name for s in listed] == ["self"]


@pytest.mark.integration
async def test_ロールが無い主体は一覧も読めない(session: AsyncSession) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await list_scan_sources(namespace=_NS, principal=_STRANGER, session=session)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_同じ名前は_409(session: AsyncSession) -> None:
    await _setup(session)
    await _register(session)
    with pytest.raises(HTTPException) as exc:
        await _register(session)
    assert exc.value.status_code == 409


# --------------------------- 実物の PostgreSQL を読む(決定1・2)


@pytest.mark.integration
async def test_実物の_PostgreSQL_を読む(session: AsyncSession) -> None:
    """**このファイルの中心である。** 自分自身の DB をスキャンする。

    フェイクでは `information_schema` の書き方も `pg_class.reltuples` の
    `-1` も再現できない。**実物に対して確かめる。**

    自分の DB をスキャンしているので、`namespaces` / `ontology_versions` /
    `scan_sources` といった**このテストが作ったスキーマ自身**が見える。
    """
    await _setup(session)
    await _register(session)
    result = await run_scan(
        namespace=_NS,
        name="self",
        principal=_OWNER,
        session=session,
        settings=_settings(),
        secret_resolver=_local_password,
    )
    assert result["table_count"] > 0

    catalog = await get_scan_catalog(
        namespace=_NS, name="self", principal=_ANALYST, session=session, run_id=None
    )
    tables = {t["table_name"]: t for t in catalog["tables"]}
    # 自分自身のテーブルが見える。
    assert "namespaces" in tables
    assert "ontology_versions" in tables
    assert tables["namespaces"]["kind"] == "table"

    columns = {c["column_name"]: c for c in tables["ontology_versions"]["columns"]}
    assert columns["id"]["is_primary_key"] is True
    # **外部キーの参照先が正確に入る**(`pg_constraint` の配列を順序付きで
    # 展開しているので複合キーでも崩れない)。
    assert columns["namespace"]["referenced_table"] == "namespaces"
    assert columns["namespace"]["referenced_column"] == "name"
    # `information_schema` 由来の型と NULL 可。
    assert columns["namespace"]["data_type"] == "character varying"
    assert columns["namespace"]["is_nullable"] is False


@pytest.mark.integration
async def test_分析されていないテーブルの行数は_null(session: AsyncSession) -> None:
    """**`0` を作らない**(ADR-0041 決定2)。

    テストが作り直したばかりのテーブルには `ANALYZE` が走っていないので、
    `reltuples` は `-1` である。**実測でこの状態が普通だった。**
    """
    await _setup(session)
    await _register(session)
    await run_scan(
        namespace=_NS,
        name="self",
        principal=_OWNER,
        session=session,
        settings=_settings(),
        secret_resolver=_local_password,
    )
    catalog = await get_scan_catalog(
        namespace=_NS, name="self", principal=_ANALYST, session=session, run_id=None
    )
    tables = {t["table_name"]: t for t in catalog["tables"]}
    assert tables["namespaces"]["estimated_rows"] is None, (
        "ANALYZE が走っていないのに行数を書いている(0 を作ってはいけない)"
    )


@pytest.mark.integration
async def test_実データが1件もカタログに入らない(session: AsyncSession) -> None:
    """**`pg_stats` は実データを持っている**(ADR-0041 決定1)。

    `most_common_vals` と `histogram_bounds` に入るのは**列の値そのもの**
    である。だから「`pg_stats` を読んでいるからメタデータだけ」とは言えない。

    ここでは**実データを入れて `ANALYZE` を走らせたテーブル**を用意し、
    その値がカタログに一切現れないことを確かめる。**`ANALYZE` を走らせるのが
    要である** — 走らせなければ `pg_stats` に行が無く、**漏れる実装でも
    このテストが通ってしまう。**

    このカタログは `P2A-02` で LLM のプロンプトへ流れる。**プロンプトへ流れた
    実データは消せない。**
    """
    import json

    from sqlalchemy import text

    await _setup(session)
    await _register(session)

    marker = "kaiin-himitsu-0f3a91"
    await session.execute(text("DROP TABLE IF EXISTS scan_probe"))
    await session.execute(text("CREATE TABLE scan_probe (v text)"))
    await session.execute(text("INSERT INTO scan_probe (v) VALUES (:v)"), {"v": marker})
    await session.commit()
    # `ANALYZE` はトランザクションの中で走らせられる(`VACUUM` は走らせられない)。
    await session.execute(text("ANALYZE scan_probe"))
    await session.commit()

    try:
        await run_scan(
            namespace=_NS,
            name="self",
            principal=_OWNER,
            session=session,
            settings=_settings(),
            secret_resolver=_local_password,
        )
        catalog = await get_scan_catalog(
            namespace=_NS, name="self", principal=_ANALYST, session=session, run_id=None
        )
        assert marker not in json.dumps(catalog, ensure_ascii=False), (
            "カタログに実データが入っている(pg_stats の most_common_vals を読んでいる)"
        )

        # **見ていないから漏れていない、ではない。** その列はちゃんと見えている。
        tables = {t["table_name"]: t for t in catalog["tables"]}
        assert "scan_probe" in tables
        columns = tables["scan_probe"]["columns"]
        assert [c["column_name"] for c in columns] == ["v"]
        # `ANALYZE` が走ったので行数は**測れている**。これは「無い」ではなく
        # 「測った 1」である(決定2)。
        assert tables["scan_probe"]["estimated_rows"] == 1
        # **実測**: 1 行で全値が異なるので PostgreSQL は `n_distinct = -1`
        # (=「全行が異なる」の**比率**)を入れる。**絶対数に換算しない**
        # (決定2)ので、`estimated_distinct` は `None` で `distinct_ratio` が
        # `1.0` になる。
        assert columns[0]["estimated_distinct"] is None
        assert columns[0]["distinct_ratio"] == 1.0
        assert columns[0]["null_fraction"] == 0.0
    finally:
        # **後片付けを忘れない。** `scan_probe` は SQLAlchemy のメタデータに
        # 無いので、`session` フィクスチャの `drop_all` では消えない。
        await session.execute(text("DROP TABLE IF EXISTS scan_probe"))
        await session.commit()


# --------------------- 完了していない観測を混ぜない(決定7)


@pytest.mark.integration
async def test_接続に失敗した_run_は_failed_で残る(session: AsyncSession) -> None:
    """**`table_count` を `0` にしない**(ADR-0041 決定7)。

    「0 件だった」と「まだ分からない」を混ぜない。
    """
    await _setup(session)
    await _register(session)

    async def _wrong_password(source: ScanSourceRow, settings: Settings) -> str | None:
        return "this-is-not-the-password"

    with pytest.raises(HTTPException) as exc:
        await run_scan(
            namespace=_NS,
            name="self",
            principal=_OWNER,
            session=session,
            settings=_settings(),
            secret_resolver=_wrong_password,
        )
    # **502 にする。** こちらの入力の誤りではなく、外部の依存に届かなかった
    # ことである。
    assert exc.value.status_code == 502

    runs = await list_scan_runs(
        namespace=_NS, name="self", principal=_ANALYST, session=session, limit=50
    )
    assert [r.status for r in runs] == ["failed"]
    assert runs[0].table_count is None, "失敗した run に件数を書いている"
    assert runs[0].failure_reason is not None, "理由を捨てている"


@pytest.mark.integration
async def test_カタログは完了した_run_だけを既定で返す(session: AsyncSession) -> None:
    """**半端な観測を「これが今のスキーマ」として読ませない**(決定7)。"""
    await _setup(session)
    await _register(session)

    async def _wrong_password(source: ScanSourceRow, settings: Settings) -> str | None:
        return "nope"

    with pytest.raises(HTTPException):
        await run_scan(
            namespace=_NS,
            name="self",
            principal=_OWNER,
            session=session,
            settings=_settings(),
            secret_resolver=_wrong_password,
        )

    catalog = await get_scan_catalog(
        namespace=_NS, name="self", principal=_ANALYST, session=session, run_id=None
    )
    assert catalog["run"] is None, "失敗した run をカタログとして返している"
    assert catalog["tables"] == []


@pytest.mark.integration
async def test_スキャンしていなければ_404_にしない(session: AsyncSession) -> None:
    """「まだスキャンしていない」はエラーではなく、答えるべき事実である。"""
    await _setup(session)
    await _register(session)
    catalog = await get_scan_catalog(
        namespace=_NS, name="self", principal=_ANALYST, session=session, run_id=None
    )
    assert catalog["run"] is None
    assert catalog["tables"] == []


@pytest.mark.integration
async def test_run_を明示すれば失敗した観測も読める(session: AsyncSession) -> None:
    """**どこまで読めたかは診断の材料である**(決定7)。

    そのとき `run` に状態が入るので、読み手は半端な観測だと分かる。
    """
    await _setup(session)
    await _register(session)

    async def _wrong_password(source: ScanSourceRow, settings: Settings) -> str | None:
        return "nope"

    with pytest.raises(HTTPException):
        await run_scan(
            namespace=_NS,
            name="self",
            principal=_OWNER,
            session=session,
            settings=_settings(),
            secret_resolver=_wrong_password,
        )
    runs = await list_scan_runs(
        namespace=_NS, name="self", principal=_ANALYST, session=session, limit=50
    )
    catalog = await get_scan_catalog(
        namespace=_NS, name="self", principal=_ANALYST, session=session, run_id=runs[0].id
    )
    assert catalog["run"]["status"] == "failed"


async def test_落ちたスキャンは_running_のまま残る(session: AsyncSession) -> None:
    """**これが決定7 の本体である**(変異テストで見つけた穴)。

    秘密の解決が `SourceConnectionError` **以外**で落ちると、`scan()` は
    `fail_run` を通らずに抜ける(マネージド ID が使えない環境の
    `CredentialUnavailableError` などがこれに当たる)。そのとき

    - `running` の run が**残っていれば**「完了していない観測」として読める
    - 残っていなければ、**スキャンを試みた事実そのものが消える**

    だから `start_run` の直後に commit している。**ここを外すと、落ちた
    スキャンが「一度も実行されていない」と同じ見た目になる。**
    """
    await _setup(session)
    await _register(session)

    async def _explode(source: ScanSourceRow, settings: Settings) -> str | None:
        # `SourceConnectionError` ではない例外。**想定していない落ち方**を表す。
        raise RuntimeError("マネージド ID が使えない")

    with pytest.raises(RuntimeError, match="マネージド ID"):
        await run_scan(
            namespace=_NS,
            name="self",
            principal=_OWNER,
            session=session,
            settings=_settings(),
            secret_resolver=_explode,
        )

    # **未 commit の変更を捨てる。** プロセスが落ちたのと同じ状態にする。
    await session.rollback()

    runs = await list_scan_runs(
        namespace=_NS, name="self", principal=_ANALYST, session=session, limit=50
    )
    assert [r.status for r in runs] == ["running"], "落ちたスキャンの記録が消えている"
    assert runs[0].finished_at is None
    assert runs[0].table_count is None


# ------------------------------------------ 監査に残す


@pytest.mark.integration
async def test_登録と削除が監査に残る(session: AsyncSession) -> None:
    """**`scan_sources` の行は消えるが、消したという事実は消えない。**

    `audit_events` は追記専用である(ADR-0011 決定2)。
    """
    await _setup(session)
    await _register(session)
    removed = await remove_scan_source(
        namespace=_NS,
        name="self",
        payload=ScanSourceRemove(reason="このソースは使わなくなった"),
        principal=_OWNER,
        session=session,
    )
    assert removed["removed"] is True

    page = await AuditRepository(session).query(namespace=_NS)
    actions = [e.action for e in page.events]
    assert "scan-source-registered" in actions
    assert "scan-source-removed" in actions
    # **主体の種別も運ぶ**(ADR-0035 決定6)。
    assert all(e.actor_type is ActorType.USER for e in page.events)


@pytest.mark.integration
async def test_監査に秘密の在り処を書かない(session: AsyncSession) -> None:
    """**監査は `data-analyst` で広く読める**(ADR-0026 決定1)。

    秘密の在り処までそこに広げない。
    """
    await _setup(session)
    await _register(
        session, name="vaulted", auth_mode="key-vault-secret", vault_secret_name="top-secret-name"
    )
    page = await AuditRepository(session).query(namespace=_NS)
    for event in page.events:
        assert "top-secret-name" not in (event.reason or "")


@pytest.mark.integration
async def test_削除するとカタログも消える(session: AsyncSession) -> None:
    """観測は積む設計だが(決定6)、ソースを消すのは運用者の明示的な操作である。"""
    await _setup(session)
    await _register(session)
    await run_scan(
        namespace=_NS,
        name="self",
        principal=_OWNER,
        session=session,
        settings=_settings(),
        secret_resolver=_local_password,
    )
    await remove_scan_source(
        namespace=_NS,
        name="self",
        payload=ScanSourceRemove(reason="消す"),
        principal=_OWNER,
        session=session,
    )
    assert await ScanRepository(session).list_sources(_NS) == []


@pytest.mark.integration
async def test_削除には_owner_が必要(session: AsyncSession) -> None:
    await _setup(session)
    await _register(session)
    with pytest.raises(HTTPException) as exc:
        await remove_scan_source(
            namespace=_NS,
            name="self",
            payload=ScanSourceRemove(reason="消す"),
            principal=_ANALYST,
            session=session,
        )
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_無いソースは_404(session: AsyncSession) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await run_scan(
            namespace=_NS,
            name="missing",
            principal=_OWNER,
            session=session,
            settings=_settings(),
            secret_resolver=_local_password,
        )
    assert exc.value.status_code == 404


# ------------------------------------ 名前空間の境界(不変条件5)


@pytest.mark.integration
async def test_他の名前空間の_run_を_id_で読めない(session: AsyncSession) -> None:
    """**`run_id` は連番なので当てられる**(変異テストで見つけた穴)。

    `scan_runs` を `id` だけで引くと、**別の名前空間のソースのカタログが
    読めてしまう**(不変条件5)。ソースで絞ってから引く。
    """
    await _setup(session)
    other = "scan-other-run-ns"
    await NamespaceRepository(session).create(
        name=other,
        display_name=other,
        description="",
        base_iri="https://e.example/other-run#",
        created_by=_ADMIN.object_id,
    )
    repo = RoleRepository(session)
    for principal, role in (
        (_OWNER, NamespaceRole.OWNER),
        (_ANALYST, NamespaceRole.DATA_ANALYST),
    ):
        await repo.grant(
            namespace=other,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await session.commit()

    # もう一方の名前空間だけにソースを作り、スキャンする。
    await register_scan_source(
        namespace=other,
        payload=_payload(),
        principal=_OWNER,
        session=session,
        settings=_settings(),
    )
    result = await run_scan(
        namespace=other,
        name="self",
        principal=_OWNER,
        session=session,
        settings=_settings(),
        secret_resolver=_local_password,
    )
    foreign_run_id = result["run_id"]

    # こちらの名前空間にも同じ名前のソースを作る(スキャンはしない)。
    await _register(session)

    with pytest.raises(HTTPException) as exc:
        await get_scan_catalog(
            namespace=_NS,
            name="self",
            principal=_ANALYST,
            session=session,
            run_id=foreign_run_id,
        )
    assert exc.value.status_code == 404, "他の名前空間の run を id で読めている"


@pytest.mark.integration
async def test_他の名前空間のソースは見えない(session: AsyncSession) -> None:
    """**名前空間名はセキュリティ境界である**(不変条件5)。"""
    await _setup(session)
    other = "scan-other-ns"
    await NamespaceRepository(session).create(
        name=other,
        display_name=other,
        description="",
        base_iri="https://e.example/other#",
        created_by=_ADMIN.object_id,
    )
    await RoleRepository(session).grant(
        namespace=other,
        principal_id=_OWNER.object_id,
        role=NamespaceRole.OWNER,
        granted_by=_ADMIN.object_id,
    )
    await session.commit()
    await _register(session)

    assert await ScanRepository(session).list_sources(other) == []
    assert await ScanRepository(session).get_source(namespace=other, name="self") is None
