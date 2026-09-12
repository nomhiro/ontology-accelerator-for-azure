"""秘密の解決と接続の作り方(ADR-0041 決定4、`P2A-01`)。

**実物の Key Vault はローカルで検証できない。** だから「検証できないから
確かめない」にしないために、**HTTP の形を `httpx.MockTransport` で固定する**。

ここで固定するのは 4 つである。

1. Key Vault の**呼び出しの形**(URL・`api-version`・`Authorization`)
2. **失敗したときに応答の本文を例外へ載せない。** 秘密が入りうる
3. **「取れなかった」を空文字にしない。** どの失敗も例外になる
4. **接続をプロセスに残さない**(`NullPool`)

`ScanService.scan` 本体はローカルの PostgreSQL に対して
`packages/api/tests/test_scan_api.py` が実物で確かめている。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy.pool import NullPool

from ontology_api.services import scan as scan_service
from ontology_api.services.scan import (
    ScanService,
    SourceConnectionError,
    default_secret_resolver,
    key_vault_password,
)
from ontology_core.config import AuthMode, Settings
from ontology_core.db import ScanSourceRow
from ontology_core.scan import ScanDriver

#: 偽のアクセストークン。
#:
#: **定数に置いているのは静的解析ツールのためである。** `self.token = "..."` と
#: 書くと S105(ハードコードされた資格情報)で誤検知する(CLAUDE.md の罠)。
_FAKE_BEARER = "fake-bearer-value"

_VAULT = "https://kv-test.vault.azure.net"


class _FakeAccess:
    """`azure.identity` が返すアクセストークンの代役。"""

    def __init__(self) -> None:
        self.token = _FAKE_BEARER


class _FakeCredential:
    """`DefaultAzureCredential` の代役。**マネージド ID はローカルに無い。**"""

    def get_token(self, *scopes: str, **kwargs: Any) -> _FakeAccess:
        return _FakeAccess()


def _source(**kwargs: Any) -> ScanSourceRow:
    base: dict[str, Any] = {
        "id": 1,
        "namespace": "ns",
        "name": "sales-db",
        "driver": ScanDriver.POSTGRESQL.value,
        "host": "db.example.internal",
        "port": 5432,
        "database": "sales",
        "username": "ontology_scanner",
        "auth_mode": "key-vault-secret",
        "vault_secret_name": "sales-db-password",
        "created_by": "owner-oid",
    }
    return ScanSourceRow(**{**base, **kwargs})


def _settings(*, vault: str | None = _VAULT) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        AUTH_MODE=AuthMode.DISABLED,
        SCAN_VAULT_URL=vault or "",
    )


def _install(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Key Vault の代わりに `handler` が応答するようにする。"""
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", _FakeCredential)
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    # **`httpx` モジュール自体に当てる。** サービスは呼び出しのたびに
    # モジュール属性を引くので、同じオブジェクトに差し替えれば届く
    # (`monkeypatch` が後で元に戻す)。
    monkeypatch.setattr(httpx, "AsyncClient", factory)


# ------------------------------------------- 呼び出しの形を固定する


async def test_key_vault_の呼び出しの形(monkeypatch: pytest.MonkeyPatch) -> None:
    """**実物の Key Vault はローカルで検証できない。**

    だから HTTP の形だけを固定する。ここが崩れたら実機で初めて分かる。
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"value": "s3cr3t-from-vault"})

    _install(monkeypatch, handler)
    value = await key_vault_password(_source(), _settings())

    assert value == "s3cr3t-from-vault"
    assert len(seen) == 1
    request = seen[0]
    # **GET である**(秘密を取り出すだけ)。
    assert request.method == "GET"
    assert str(request.url).startswith(f"{_VAULT}/secrets/sales-db-password")
    assert request.url.params["api-version"] == "7.4"
    assert request.headers["Authorization"] == f"Bearer {_FAKE_BEARER}"
    # **秘密の名前は URL のパスに入る。** クエリには入れない
    # (クエリはアクセスログに流れる)。
    assert "sales-db-password" in request.url.path


async def test_末尾のスラッシュを二重にしない(monkeypatch: pytest.MonkeyPatch) -> None:
    """運用者が `SCAN_VAULT_URL` に `/` を付けても壊れない。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"value": "v"})

    _install(monkeypatch, handler)
    await key_vault_password(_source(), _settings(vault=f"{_VAULT}/"))
    assert seen[0].url.path == "/secrets/sales-db-password"


# ------------------------------------- 失敗を秘密の漏洩に変えない


async def test_失敗しても応答の本文を例外に載せない(monkeypatch: pytest.MonkeyPatch) -> None:
    """**これが決定4 のいちばん大事なところである。**

    例外のメッセージはログにも API の応答にも流れる。Key Vault の応答本文に
    秘密が含まれる可能性がある以上、**状態コードと秘密の名前だけを書く**。
    """
    leak = "kore-wa-himitsu-de-aru"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": leak}})

    _install(monkeypatch, handler)
    with pytest.raises(SourceConnectionError) as exc:
        await key_vault_password(_source(), _settings())

    message = str(exc.value)
    assert leak not in message, "例外に応答の本文が載っている"
    assert "403" in message, "状態コードを捨てている(原因の切り分けに要る)"
    assert "sales-db-password" in message


async def test_値が無ければ例外にする(monkeypatch: pytest.MonkeyPatch) -> None:
    """**「取れなかった」を空文字にしない**(決定4)。

    空文字を返すと「パスワード無しで接続を試みる」ことになり、失敗の理由が
    「認証に失敗した」にすり替わる。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "https://kv/secrets/x"})

    _install(monkeypatch, handler)
    with pytest.raises(SourceConnectionError, match="値がありません"):
        await key_vault_password(_source(), _settings())


async def test_vault_の_URL_が無ければ例外にする() -> None:
    with pytest.raises(SourceConnectionError, match="SCAN_VAULT_URL"):
        await key_vault_password(_source(), _settings(vault=None))


async def test_秘密の名前が無ければ例外にする() -> None:
    with pytest.raises(SourceConnectionError, match="vault_secret_name"):
        await key_vault_password(_source(vault_secret_name=None), _settings())


async def test_知らない_auth_mode_は例外にする() -> None:
    """**黙って `None` を返さない。**

    `None` を返すとパスワード無しで接続を試み、`auth_mode` の綴り間違いが
    「認証に失敗した」として現れる。
    """
    with pytest.raises(SourceConnectionError, match="未知の auth_mode"):
        await default_secret_resolver(_source(auth_mode="oauth"), _settings())


# ------------------------------------------- 接続を残さない


async def test_接続をプロセスに残さない(monkeypatch: pytest.MonkeyPatch) -> None:
    """**`poolclass=None` では無効化にならない**(実測)。

    それは「既定のプールを使う」の意味で、`AsyncAdaptedQueuePool` になる。
    顧客 DB への接続がプロセスに残り続けるので、`NullPool` を明示する。

    **`dispose` も確かめる。** プールを外しても、エンジンを捨てなければ
    意味が半分になる。
    """
    captured: dict[str, Any] = {}
    disposed: list[bool] = []

    class _Engine:
        def connect(self) -> Any:
            raise RuntimeError("ここでは接続しない")

        async def dispose(self) -> None:
            disposed.append(True)

    def fake_create(dsn: str, **kwargs: Any) -> _Engine:
        captured["dsn"] = dsn
        captured.update(kwargs)
        return _Engine()

    monkeypatch.setattr(scan_service, "create_async_engine", fake_create)

    async def resolver(source: ScanSourceRow, settings: Settings) -> str | None:
        return "localdev"

    service = ScanService(
        session=None,  # type: ignore[arg-type]
        settings=_settings(),
        secret_resolver=resolver,
    )
    with pytest.raises(SourceConnectionError):
        await service._read_catalog(_source(), ScanDriver.POSTGRESQL)

    assert captured["poolclass"] is NullPool
    assert disposed == [True], "エンジンを捨てていない"
    # **DSN にパスワードが入っていない**(不変条件6)。
    assert "localdev" not in captured["dsn"]
    assert captured["connect_args"]["password"] == "localdev"
