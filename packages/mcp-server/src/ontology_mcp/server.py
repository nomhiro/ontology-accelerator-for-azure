"""MCP サーバーの定義。

## 設計方針

ツールの実処理は**すべて Core API に委譲する**。ストアを直接叩かないのは、
名前空間ごとの認可判定と「どのバージョンの何をエージェントへ返したか」の監査記録を
Core API 側の一箇所に集めるため(`docs/adr/0006-ontology-versioning-and-audit.md`)。

クエリのガードはここでも先に適用する。Core API 側でも同じガードが働くので二重だが、
明らかに危険な入力を境界で落としておく方が安全側に倒れる。

## Phase 0 の実装状況

`list_namespaces` と `sparql_query` を Core API 経由で実装している。呼び出し元の
Entra ID トークンを Core API へ引き継ぐ処理は Phase 1 で実装する(現時点では
`AUTH_MODE=disabled` のローカル開発でのみ通る)。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ontology_core.config import get_settings
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


def _api_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_settings.core_api_url,
        timeout=_settings.sparql_query_timeout_seconds,
    )


@mcp.tool()
async def list_namespaces() -> list[dict[str, Any]]:
    """参照できるオントロジーの名前空間を列挙する。

    Returns:
        名前空間の一覧。各要素は name / display_name / description / base_iri を持つ。
    """
    async with _api_client() as client:
        response = await client.get("/namespaces")
        response.raise_for_status()
        result: list[dict[str, Any]] = response.json()
        return result


@mcp.tool()
async def sparql_query(namespace: str, query: str) -> dict[str, Any]:
    """指定した名前空間に対して読み取り専用の SPARQL クエリを実行する。

    Args:
        namespace: 対象の名前空間の名前。`list_namespaces` で取得できる。
        query: SPARQL の SELECT または ASK クエリ。更新操作と SERVICE 句は使えない。

    Returns:
        SPARQL Results JSON 形式の結果。

    Raises:
        ValueError: クエリが読み取り専用の条件を満たさないとき。
    """
    try:
        ensure_agent_safe_query(query, allow_service=_settings.sparql_allow_service)
    except QueryRejectedError as exc:
        # MCP のツールエラーとしてエージェントへ理由を返す。
        raise ValueError(str(exc)) from exc

    async with _api_client() as client:
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
