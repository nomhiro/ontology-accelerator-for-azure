"""MCP サーバーの定義。

## 設計方針

ツールの実処理は**すべて Core API に委譲する**。ストアを直接叩かないのは、
名前空間ごとの認可判定と「どのバージョンの何をエージェントへ返したか」の監査記録を
Core API 側の一箇所に集めるため(`docs/adr/0006-ontology-versioning-and-audit.md`)。

クエリのガードはここでも先に適用する。Core API 側でも同じガードが働くので二重だが、
明らかに危険な入力を境界で落としておく方が安全側に倒れる。

## 認証(ADR-0012)

**呼び出し元の Entra ID トークンを検証し、そのまま Core API へ転送する。**
MCP と Core API は同一のオーディエンス(同一アプリ登録)を共有しているため、
トークンはまさにこのリソース宛てに発行されている。MCP 仕様が禁止している
「サーバー宛てに発行されていないトークンの転送」には当たらない(根拠は
ADR-0012)。

**ヘッダの存在を識別子の主張として扱わない。** 署名・発行者・対象者を
`TokenVerifier` で検証してから転送する。Core API 側の検証が権威であり、
ここは境界での早期拒否である(クエリのガードを二重に置いているのと同じ方針)。

マネージド ID で Core API を呼ぶ案は採らなかった。Core API から見た
呼び出し元が常に MCP になり、監査の `actor` が実際のエージェントを
指さなくなるためである(ADR-0006 の帰属が壊れる)。

## エージェントへ返すエラーは `ToolError` にすること

**MCP SDK は `ToolError` 以外の例外のメッセージを隠す。** `ValueError` を
投げると、エージェントに届くのは `Error executing tool <name>` という
本文だけで、理由が失われる(実測で確認)。理由が届かなければエージェントは
自力で直せない。意図的な拒否(トークンが無い・クエリが読み取り専用でない)は
必ず `ToolError` で返す。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ontology_core.auth.entra import TokenVerificationError, TokenVerifier
from ontology_core.config import AuthMode, get_settings
from ontology_core.sparql.guards import QueryRejectedError, ensure_agent_safe_query
from ontology_mcp import __version__

_settings = get_settings()
logging.basicConfig(level=_settings.log_level.upper())
logger = logging.getLogger(__name__)

mcp = MCPServer(
    "ontology-accelerator",
    instructions=(
        "承認済みのビジネスオントロジーを参照するためのツール群。"
        "まず list_namespaces で対象の名前空間を確認し、"
        "sparql_query で読み取り専用の SPARQL クエリを実行する。"
    ),
)


@lru_cache(maxsize=1)
def _token_verifier() -> TokenVerifier:
    """Core API と同じテナント・同じオーディエンスで検証する(ADR-0012 決定1・2)。

    `TokenVerifier` は JWKS を内部でキャッシュするのでプロセス内で再利用する。
    """
    return TokenVerifier(
        tenant_id=_settings.entra_tenant_id,
        audience=_settings.entra_api_audience,
    )


def _authorization(headers: Mapping[str, str] | None) -> str:
    """受信ヘッダから `Authorization` を取り出す(名前の大文字小文字を問わない)。

    Starlette の `Headers` は大文字小文字を区別しないが、型注釈は素の
    `Mapping` なので、別のトランスポートが素の辞書を渡してきても動くように
    しておく。
    """
    if headers is None:
        return ""
    direct = headers.get("authorization") or headers.get("Authorization")
    if direct:
        return direct
    for key, value in headers.items():
        if key.lower() == "authorization":
            return value
    return ""


def _forward_headers(ctx: Context[Any, Any]) -> dict[str, str]:
    """呼び出し元のトークンを検証し、Core API へ転送するヘッダを返す。

    **転送するのは `Authorization` だけである。** `Cookie` や
    `X-Forwarded-*` のような他のクライアントヘッダは転送しない
    (ADR-0012 決定3)。

    Raises:
        ToolError: トークンが無い、または検証を通らないとき。**`ValueError`
            ではなく `ToolError` を使う。** SDK は `ToolError` 以外の
            メッセージを隠すため、理由がエージェントに届かない。
    """
    if _settings.auth_mode is AuthMode.DISABLED:
        # ローカル開発の経路(不変条件9。デプロイ環境で使ってはならない)。
        return {}

    authorization = _authorization(ctx.headers)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ToolError(
            "Bearer トークンが必要です。Core API のオーディエンス "
            f"(api://{_settings.entra_api_audience}/.default) 向けのアクセストークンを "
            "Authorization ヘッダで渡してください。"
        )

    token = authorization.split(" ", 1)[1].strip()
    try:
        principal = _token_verifier().verify(token)
    except TokenVerificationError as exc:
        # 理由は返すが、トークンそのものはログにも応答にも出さない。
        raise ToolError(f"トークンの検証に失敗しました: {exc}") from exc

    logger.debug("MCP の呼び出し元: %s", principal.object_id or principal.subject)
    # 検証を通った値をそのまま転送する(Core API 側の検証が権威)。
    return {"Authorization": authorization}


def _api_client(headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_settings.core_api_url,
        timeout=_settings.sparql_query_timeout_seconds,
        headers=headers or {},
    )


@mcp.tool()
async def list_namespaces(ctx: Context[Any, Any]) -> list[dict[str, Any]]:
    """参照できるオントロジーの名前空間を列挙する。

    Returns:
        名前空間の一覧。各要素は name / display_name / description / base_iri を持つ。

    Raises:
        ToolError: 呼び出し元のトークンが無い、または検証を通らないとき。
    """
    async with _api_client(_forward_headers(ctx)) as client:
        response = await client.get("/namespaces")
        response.raise_for_status()
        result: list[dict[str, Any]] = response.json()
        return result


@mcp.tool()
async def sparql_query(namespace: str, query: str, ctx: Context[Any, Any]) -> dict[str, Any]:
    """指定した名前空間に対して読み取り専用の SPARQL クエリを実行する。

    Args:
        namespace: 対象の名前空間の名前。`list_namespaces` で取得できる。
        query: SPARQL の SELECT または ASK クエリ。更新操作と SERVICE 句は使えない。

    Returns:
        SPARQL Results JSON 形式の結果。

    Raises:
        ToolError: クエリが読み取り専用の条件を満たさないとき、または
            呼び出し元のトークンが無い・検証を通らないとき。
    """
    try:
        ensure_agent_safe_query(query, allow_service=_settings.sparql_allow_service)
    except QueryRejectedError as exc:
        # MCP のツールエラーとしてエージェントへ理由を返す。
        # **`ValueError` では理由が隠される**(上記の理由)。以前は
        # `ValueError` だったため、拒否した理由がエージェントに届いて
        # いなかった。
        raise ToolError(str(exc)) from exc

    async with _api_client(_forward_headers(ctx)) as client:
        response = await client.post(
            f"/namespaces/{namespace}/sparql",
            json={"query": query},
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result


async def _healthz(request: Request) -> JSONResponse:
    """プロセスの生存確認。

    Core API の `/healthz` と同じ方針で、依存先(Core API・ストア)の到達性は
    含めない。Container Apps の liveness probe が依存先の一時的な不調で
    レプリカを落とさないようにするため。
    """
    del request
    return JSONResponse({"status": "ok", "version": __version__})


def _transport_security() -> TransportSecuritySettings:
    """Host ヘッダ検証(DNS リバインディング対策)の設定を組み立てる。

    SDK の既定は `host="127.0.0.1"` のみを許可するため、Container Apps の FQDN で
    アクセスすると 421 Invalid Host header になる。`MCP_ALLOWED_HOSTS` に許可する
    ホストを渡すことで、デプロイ環境でも検証を有効にしたまま動かせる。
    """
    hosts = _settings.mcp_allowed_host_list
    if not hosts:
        # ローカル開発を止めないため検証を外す。デプロイ環境では Bicep が
        # 自身の FQDN を設定するのでここには来ない。
        logger.warning(
            "MCP_ALLOWED_HOSTS が未設定のため Host ヘッダ検証を無効にします。"
            "デプロイ環境では必ず設定してください"
        )
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"https://{host}" for host in hosts],
    )


def build_app() -> Starlette:
    """Streamable HTTP の ASGI アプリを組み立てる。

    uvicorn から `ontology_mcp.server:build_app` をファクトリとして起動する。

    ヘルスチェック用の経路は、MCP のアプリを別の Starlette でラップせず
    **既存のルーターに追加する**。ラップすると内側のアプリの lifespan が
    実行されず、セッションマネージャが起動しないため。
    """
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=_transport_security(),
    )
    app.router.routes.append(Route("/healthz", _healthz, methods=["GET"]))
    return app


def main() -> None:
    """開発用のエントリポイント。"""
    if not _settings.mcp_read_only:
        logger.warning(
            "MCP_READ_ONLY=false が設定されていますが、このサーバーは書き込み経路を"
            "持ちません。設定は無視されます"
        )
    mcp.run(
        transport="streamable-http",
        stateless_http=True,
        json_response=True,
        transport_security=_transport_security(),
    )


if __name__ == "__main__":
    main()
