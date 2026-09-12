"""ソース DB へ接続してメタデータを読む(ADR-0041、`P2A-01`)。

## 実データを 1 行も読まない

発行するのは `ontology_core.scan.CATALOG_QUERIES` だけである(決定1・3)。
**ユーザーテーブルに `SELECT` を発行しない。** このカタログは `P2A-02`
(LLM によるオントロジー候補生成)の入力になるので、サンプリングした実データは
**プロンプトへ流れて消せなくなる**。

## 接続先を allowlist で絞る

`SCAN_ALLOWED_HOSTS` に無いホストへは接続しない(決定5)。**既定は空で、
そのときスキャンは使えない。** 任意のホストへ接続できる口は、認証済みの主体に
内部ネットワークの到達性を調べる手段を与える。

## 秘密は接続のたびに解決し、DSN に埋めない

不変条件6 をそのまま適用する。秘密は `connect_args["password"]` で渡し、
**DSN には埋めない**。エンジンはスキャン 1 回ごとに作って捨てる
(`NullPool` + `dispose`)ので、接続をまたいで秘密が生き続けない。

**秘密の解決は差し替えられる**(`SecretResolver`)。Key Vault の呼び出しは
ローカルでは検証できないので、**HTTP の形だけを `httpx.MockTransport` で
固定し**、スキャン本体はローカルの PostgreSQL に対して実物で検証する。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ontology_api.repositories.scan import ScanRepository
from ontology_core.config import Settings
from ontology_core.db import ScanSourceRow
from ontology_core.scan import (
    CATALOG_QUERIES,
    ScanAuthMode,
    ScanDriver,
    build_observations,
    validate_driver,
    validate_host,
)

__all__ = [
    "ScanService",
    "SecretResolver",
    "SourceConnectionError",
    "entra_password",
    "key_vault_password",
]

logger = logging.getLogger(__name__)

#: PostgreSQL へ Entra のトークンで接続するときのスコープ。
#:
#: `ontology_core.db.engine` と同じ値である。**書き写しているのは、
#: あちらが自分の DB 用でこちらが顧客の DB 用という別の関心だからである。**
_POSTGRES_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"

#: Key Vault の API バージョン。
_VAULT_API_VERSION = "7.4"

#: Key Vault のトークンのスコープ。
_VAULT_SCOPE = "https://vault.azure.net/.default"

#: 秘密を解決する関数。**差し替えられる**(テストとローカル開発のため)。
SecretResolver = Callable[[ScanSourceRow, Settings], Awaitable[str | None]]


class SourceConnectionError(Exception):
    """ソース DB へ接続できなかった、またはメタデータを読めなかった。

    **理由を捨てない。** 「ホストに届かない」「認証に失敗した」「権限が無い」は
    運用者にとって別の状況である。
    """


async def entra_password(source: ScanSourceRow, settings: Settings) -> str | None:
    """Entra のアクセストークンをパスワードとして返す。

    **接続のたびに取る。** トークンは期限切れするので、DSN に埋めてはいけない
    (不変条件6)。
    """
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential().get_token(_POSTGRES_SCOPE).token


async def key_vault_password(source: ScanSourceRow, settings: Settings) -> str | None:
    """Key Vault から秘密を取り出す(ADR-0041 決定4)。

    **秘密の名前しかカタログに無い。** 値は接続のたびに解決する。

    REST を直接叩いているのは、`azure-keyvault-secrets` を依存に足さずに
    済ませるためである(`httpx` と `azure-identity` は既にある)。
    **HTTP の形はテストで固定する** — 実物の Key Vault はローカルで
    検証できない。

    Raises:
        SourceConnectionError: Key Vault の URL か秘密名が無いとき、
            または取得に失敗したとき。**「取れなかった」を空文字にしない。**
    """
    from azure.identity import DefaultAzureCredential

    if not settings.scan_vault_url:
        raise SourceConnectionError(
            "SCAN_VAULT_URL が設定されていないため、Key Vault から秘密を取り出せません"
        )
    if not source.vault_secret_name:
        raise SourceConnectionError(
            f"ソース '{source.name}' に vault_secret_name が設定されていません"
        )

    bearer = DefaultAzureCredential().get_token(_VAULT_SCOPE).token
    url = f"{settings.scan_vault_url.rstrip('/')}/secrets/{source.vault_secret_name}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            url,
            params={"api-version": _VAULT_API_VERSION},
            headers={"Authorization": f"Bearer {bearer}"},
        )
    if response.status_code != httpx.codes.OK:
        # **本文を載せない。** 秘密が含まれる可能性がある応答をログや例外に
        # 流さない。状態コードと秘密の名前だけで運用者は原因を切り分けられる。
        raise SourceConnectionError(
            f"Key Vault から秘密 '{source.vault_secret_name}' を取得できませんでした"
            f"(HTTP {response.status_code})"
        )
    value = response.json().get("value")
    if not isinstance(value, str):
        raise SourceConnectionError(
            f"Key Vault の応答に秘密 '{source.vault_secret_name}' の値がありません"
        )
    return value


async def default_secret_resolver(source: ScanSourceRow, settings: Settings) -> str | None:
    """`auth_mode` に応じて秘密を解決する。

    **知らない `auth_mode` は例外にする。** 黙って `None` を返すと
    「パスワード無しで接続を試みる」ことになり、失敗の理由が分からなくなる。
    """
    if source.auth_mode == ScanAuthMode.ENTRA.value:
        return await entra_password(source, settings)
    if source.auth_mode == ScanAuthMode.KEY_VAULT.value:
        return await key_vault_password(source, settings)
    raise SourceConnectionError(f"未知の auth_mode です: {source.auth_mode!r}")


class ScanService:
    """ソース DB のメタデータを読んでカタログへ積む。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        secret_resolver: SecretResolver = default_secret_resolver,
    ) -> None:
        self._session = session
        self._settings = settings
        self._resolve_secret = secret_resolver

    async def scan(self, *, source: ScanSourceRow, actor: str) -> tuple[int, int]:
        """1 回スキャンして `(run_id, テーブル数)` を返す。

        順序には理由がある。

        1. **allowlist と driver を先に確かめる**(決定5・9)。接続してから
           断るのでは、到達性を調べる手段を与えたことになる
        2. **`running` の run を先に書く**(決定7)。途中で落ちた run が
           `running` のまま残ることで「完了していない観測」が区別できる
        3. メタデータを読む(`CATALOG_QUERIES` だけ)
        4. 観測を積み、run を `succeeded` にする

        Raises:
            HostNotAllowedError: allowlist に無いホストのとき。
            UnsupportedDriverError: 対応していない driver のとき。
            SourceConnectionError: 接続・読み取りに失敗したとき。**run は
                `failed` として残る。**
        """
        validate_host(source.host, self._settings.scan_allowed_host_list)
        driver = validate_driver(source.driver)

        repo = ScanRepository(self._session)
        run = await repo.start_run(source_id=source.id, started_by=actor)
        # **ここで commit する。** 途中で落ちたときに `running` の run が
        # 残らなければ、決定7 の区別が成立しない。
        await self._session.commit()

        try:
            results = await self._read_catalog(source, driver)
        except SourceConnectionError as exc:
            await repo.fail_run(run, reason=str(exc))
            await self._session.commit()
            raise

        observations = build_observations(**results)
        await repo.record_observations(run, observations)
        await repo.finish_run(run, table_count=len(observations))
        await self._session.commit()
        return run.id, len(observations)

    async def _read_catalog(
        self, source: ScanSourceRow, driver: ScanDriver
    ) -> dict[str, list[dict[str, Any]]]:
        """`CATALOG_QUERIES` を実行して結果を返す。

        **これ以外の SQL を発行しない**(ADR-0041 決定1・3)。

        **エンジンをプールしない**(`NullPool`)。スキャンは 1 回の操作なので
        接続を使い回す理由が無く、逆に使い回すと**顧客 DB への接続がプロセスに
        残り続ける**。

        **`poolclass=None` では無効化にならない**(実測。それは「既定のプールを
        使う」の意味で、`AsyncAdaptedQueuePool` になる)。`NullPool` を明示する
        必要がある。回帰テストで固定してある。
        """
        password = await self._resolve_secret(source, self._settings)
        # **DSN にパスワードを埋めない**(不変条件6)。
        #
        # ここは `ontology_core.db.engine` と違って**値を渡している**。理由は
        # 2 つある。(1) `SecretResolver` が `async` なので、asyncpg に渡せる
        # 同期の callable にできない。(2) このエンジンは 1 回のスキャンで
        # 捨てられ(`NullPool` + `dispose`)、接続をまたいで生き続けないので
        # 期限切れの問題が起きない。**不変条件が守ろうとしているのは「DSN に
        # 残る」ことであり、そこは満たしている。**
        dsn = (
            f"{driver.value}+asyncpg://{source.username}@"
            f"{source.host}:{source.port}/{source.database}"
        )
        connect_args: dict[str, Any] = {}
        if password is not None:
            connect_args["password"] = password
        engine = create_async_engine(dsn, poolclass=NullPool, connect_args=connect_args)
        try:
            async with engine.connect() as connection:
                results: dict[str, list[dict[str, Any]]] = {}
                for name, sql in CATALOG_QUERIES.items():
                    rows = await connection.execute(text(sql))
                    results[name] = [dict(row) for row in rows.mappings()]
                return results
        except Exception as exc:  # ドライバは多様な例外を投げる
            logger.warning(
                "ソース '%s' (%s:%s/%s) のメタデータを読めませんでした: %s",
                source.name,
                source.host,
                source.port,
                source.database,
                exc,
            )
            raise SourceConnectionError(
                f"ソース '{source.name}' のメタデータを読めませんでした: {exc}"
            ) from exc
        finally:
            await engine.dispose()
