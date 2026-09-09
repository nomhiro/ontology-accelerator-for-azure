"""MCP から Core API へのトークン転送(ADR-0012、P1-12)のテスト。

**拒否は `ToolError` で投げること。** MCP SDK は `ToolError` 以外の例外の
メッセージを隠し、エージェントには `Error executing tool <name>` しか
届かない(実測で確認)。理由が届かなければエージェントは自力で直せない。
`pytest.raises(ToolError)` はこの契約も検証している(`ToolError` は
`ValueError` を継承していないので、`ValueError` に戻すと落ちる)。

**修正前は MCP が Core API を認証ヘッダなしで呼んでいた。** そのため
`AUTH_MODE=entra` のデプロイ環境ではツール呼び出しがすべて 401 になり、
`tools/list` だけは成功するため外からは動いているように見えていた。

実トークンを使う E2E は `scripts/verify-mcp-auth.sh`(手動)で行う。ここは
`az` ログインに依存しない単体の検証に限る(conftest の「スキップはしない」
方針と衝突させないため)。
"""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from ontology_core.auth.entra import Principal, TokenVerificationError
from ontology_core.config import AuthMode
from ontology_mcp import server


class _FakeVerifier:
    """署名検証の代役。`verify` が呼ばれたことと引数を記録する。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self._fail = fail

    def verify(self, token: str) -> Principal:
        self.calls.append(token)
        if self._fail:
            raise TokenVerificationError("署名が不正です(テスト)")
        return Principal(subject="agent-sub", object_id="agent-oid")


class _Ctx:
    """`Context` の代役。`headers` だけを持つ。

    `_forward_headers` は `headers` にしか触らないので、これで足りる。
    型注釈上は `Context` を要求しているため、渡すときは `_ctx()` で包む。
    """

    def __init__(self, headers: dict[str, str] | None) -> None:
        self.headers = headers


def _ctx(headers: dict[str, str] | None) -> Context[Any, Any]:
    """テストダブルを `Context` として渡す。"""
    return cast("Context[Any, Any]", _Ctx(headers))


def _use_auth_mode(monkeypatch: pytest.MonkeyPatch, mode: AuthMode) -> None:
    """モジュール読み込み時に確定する `_settings` を差し替える。"""
    # Settings を複製して差し替える(他のテストへ漏らさない)。
    replaced = server._settings.model_copy(update={"auth_mode": mode})
    monkeypatch.setattr(server, "_settings", replaced)


def _use_verifier(monkeypatch: pytest.MonkeyPatch, verifier: Any) -> None:
    monkeypatch.setattr(server, "_token_verifier", lambda: verifier)


# ---- AUTH_MODE=disabled(ローカル開発) ----


def test_disabled_mode_forwards_no_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    """ローカル開発では検証も転送もしない(不変条件9の逃げ道)。"""
    _use_auth_mode(monkeypatch, AuthMode.DISABLED)
    verifier = _FakeVerifier()
    _use_verifier(monkeypatch, verifier)

    assert server._forward_headers(_ctx({"authorization": "Bearer whatever"})) == {}
    # 検証すら呼ばれない。
    assert verifier.calls == []


# ---- AUTH_MODE=entra ----


def test_missing_token_is_an_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """トークンが無ければ、何を渡すべきかが分かるエラーにする。"""
    _use_auth_mode(monkeypatch, AuthMode.ENTRA)
    _use_verifier(monkeypatch, _FakeVerifier())

    with pytest.raises(ToolError) as exc_info:
        server._forward_headers(_ctx({}))

    message = str(exc_info.value)
    assert "Bearer" in message
    # 取得すべきスコープが書かれていること(エージェントが自力で直せるように)。
    assert ".default" in message


def test_headers_absent_entirely_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """stdio トランスポート等で `headers` が None のときも拒否する。"""
    _use_auth_mode(monkeypatch, AuthMode.ENTRA)
    _use_verifier(monkeypatch, _FakeVerifier())

    with pytest.raises(ToolError):
        server._forward_headers(_ctx(None))


def test_non_bearer_scheme_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_auth_mode(monkeypatch, AuthMode.ENTRA)
    verifier = _FakeVerifier()
    _use_verifier(monkeypatch, verifier)

    with pytest.raises(ToolError):
        server._forward_headers(_ctx({"authorization": "Basic dXNlcjpwYXNz"}))
    # 検証に渡す前に落とす。
    assert verifier.calls == []


def test_invalid_token_is_rejected_at_the_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """**ヘッダの存在を識別子の主張として扱わない**(ADR-0012 決定2)。

    検証が失敗したら転送しない。修正前はここに検証が無かった。
    """
    _use_auth_mode(monkeypatch, AuthMode.ENTRA)
    verifier = _FakeVerifier(fail=True)
    _use_verifier(monkeypatch, verifier)

    with pytest.raises(ToolError) as exc_info:
        server._forward_headers(_ctx({"authorization": "Bearer garbage"}))

    assert "検証に失敗" in str(exc_info.value)
    assert verifier.calls == ["garbage"]


def test_valid_token_is_forwarded_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_auth_mode(monkeypatch, AuthMode.ENTRA)
    verifier = _FakeVerifier()
    _use_verifier(monkeypatch, verifier)

    headers = server._forward_headers(_ctx({"authorization": "Bearer real.token.value"}))

    assert headers == {"Authorization": "Bearer real.token.value"}
    assert verifier.calls == ["real.token.value"]


def test_only_authorization_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Cookie` や `X-Forwarded-*` を下流へ渡さない(ADR-0012 決定3)。"""
    _use_auth_mode(monkeypatch, AuthMode.ENTRA)
    _use_verifier(monkeypatch, _FakeVerifier())

    headers = server._forward_headers(
        _ctx(
            {
                "authorization": "Bearer real.token.value",
                "cookie": "session=secret",
                "x-forwarded-for": "10.0.0.1",
                "x-custom": "nope",
            }
        )
    )

    assert list(headers) == ["Authorization"]


def test_authorization_header_name_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """素の辞書を渡すトランスポートでも動くこと。"""
    _use_auth_mode(monkeypatch, AuthMode.ENTRA)
    _use_verifier(monkeypatch, _FakeVerifier())

    for name in ("Authorization", "authorization", "AUTHORIZATION"):
        headers = server._forward_headers(_ctx({name: "Bearer t"}))
        assert headers == {"Authorization": "Bearer t"}


# ---- ツールが実際に転送していること ----


async def test_list_namespaces_sends_the_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ヘルパが正しくても、ツールが使っていなければ意味がない。

    修正前はツールが `_api_client()` を引数なしで呼んでいた。ここが本題。
    """
    _use_auth_mode(monkeypatch, AuthMode.ENTRA)
    _use_verifier(monkeypatch, _FakeVerifier())

    sent: dict[str, Any] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, Any]]:
            return [{"name": "retail-core"}]

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            sent["headers"] = dict(kwargs.get("headers") or {})

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, path: str) -> _FakeResponse:
            sent["path"] = path
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = await server.list_namespaces(_ctx({"authorization": "Bearer t"}))

    assert result == [{"name": "retail-core"}]
    assert sent["path"] == "/namespaces"
    assert sent["headers"] == {"Authorization": "Bearer t"}


async def test_sparql_query_sends_the_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_auth_mode(monkeypatch, AuthMode.ENTRA)
    _use_verifier(monkeypatch, _FakeVerifier())

    sent: dict[str, Any] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"results": {"bindings": []}}

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            sent["headers"] = dict(kwargs.get("headers") or {})

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, path: str, json: dict[str, Any]) -> _FakeResponse:
            sent["path"] = path
            sent["json"] = json
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = await server.sparql_query(
        "retail-core", "SELECT * WHERE { ?s ?p ?o }", _ctx({"authorization": "Bearer t"})
    )

    assert result == {"results": {"bindings": []}}
    assert sent["path"] == "/namespaces/retail-core/sparql"
    assert sent["headers"] == {"Authorization": "Bearer t"}


async def test_unsafe_query_is_rejected_before_touching_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """危険なクエリは境界で落ちる(トークンの有無に関わらず)。"""
    _use_auth_mode(monkeypatch, AuthMode.ENTRA)
    verifier = _FakeVerifier()
    _use_verifier(monkeypatch, verifier)

    with pytest.raises(ToolError):
        await server.sparql_query(
            "retail-core",
            "INSERT DATA { <urn:a> <urn:b> <urn:c> }",
            _ctx({"authorization": "Bearer t"}),
        )
    assert verifier.calls == []


async def test_version_decisions_sends_the_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2B-08: 決定記録のツールも呼び出し元のトークンを転送する。

    監査の `actor` が実際のエージェントを指すためには、このツールも
    同じ経路を通る必要がある(ADR-0012)。
    """
    _use_auth_mode(monkeypatch, AuthMode.ENTRA)
    _use_verifier(monkeypatch, _FakeVerifier())

    sent: dict[str, Any] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, Any]]:
            return [{"action": "approved", "reason": "想定質問を満たす"}]

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            sent["headers"] = dict(kwargs.get("headers") or {})

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, path: str) -> _FakeResponse:
            sent["path"] = path
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = await server.version_decisions(
        "retail-core", "1.0.0", _ctx({"authorization": "Bearer t"})
    )

    assert result == [{"action": "approved", "reason": "想定質問を満たす"}]
    assert sent["path"] == "/namespaces/retail-core/versions/1.0.0/decisions"
    assert sent["headers"] == {"Authorization": "Bearer t"}
