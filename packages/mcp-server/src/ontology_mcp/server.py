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
        "定義の根拠(誰が承認したか・なぜそう決めたか)が必要なときは "
        "version_decisions を使う。"
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


#: Core API が廃止済み用語を載せてくるヘッダ(ADR-0017 決定3)。
#: `ontology_api.routers.sparql.DEPRECATED_TERMS_HEADER` と同じ値。
#: **import しないのは、MCP が Core API のコードに依存しないため**である
#: (HTTP 境界を越えて型を共有しない。ADR-0012 の分離を保つ)。
_DEPRECATED_TERMS_HEADER = "X-Ontology-Deprecated-Terms"

# 結果を切り詰めたことを伝えるヘッダ(`P2A-08`、ADR-0025 決定4)。
#
# **Core API と同じ文字列を、あえてここに書き写している。** `_DEPRECATED_TERMS_HEADER`
# と同じ理由である — MCP が Core API を HTTP で呼ぶ境界を保つため、
# `ontology_api` を import しない。
_RESULT_TRUNCATED_HEADER = "X-Ontology-Result-Truncated"
_RESULT_LIMIT_HEADER = "X-Ontology-Result-Limit"
_RESULT_TOTAL_ROWS_HEADER = "X-Ontology-Result-Total-Rows"


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

    **このエンドポイントは推論しない**(ADR-0028)。承認された定義がそのまま
    載っているだけで、OWL の含意は展開されていない。`Premium ⊑ Customer ⊑ Party`
    と定義されていても、`?s rdfs:subClassOf ex:Party` は `Customer` しか
    返さない。**「Party の部分クラスは Customer だけ」と答えてはいけない。**

    階層を辿るには**プロパティパス**を使う。

    | 知りたいこと | 書き方 |
    |---|---|
    | ある用語の下位クラス全部 | `?s rdfs:subClassOf+ ex:Party` |
    | ある個体が属するクラス全部 | `ex:alice rdf:type/rdfs:subClassOf* ?c` |
    | 上位の概念全部(SKOS) | `ex:x skos:broader+ ?c` |

    **プロパティパスで届かない含意もある。** `owl:someValuesFrom` を通じた
    含意、`owl:equivalentClass` の対称性、互いに素なクラスからの帰結は
    パスでは辿れない。それらが必要な問いには「このエンドポイントでは
    判定できない」と答えるのが正確である。

    Args:
        namespace: 対象の名前空間の名前。`list_namespaces` で取得できる。
        query: SPARQL の SELECT または ASK クエリ。更新操作と SERVICE 句は
            使えない。**推論は行われないのでプロパティパスを使うこと**(上記)。

    Returns:
        SPARQL Results JSON 形式の結果。

        **`result_truncated` が真なら、結果は全部ではない。** 上限
        (`result_limit`)で切られている。`result_total_rows` に「本当は何行
        あったか」が入る。**この場合「該当は N 件だった」と答えてはいけない** —
        「少なくとも N 件あり、上限で切られている」が正確である。絞り込みの
        条件を足して問い直すか、そのことを回答に添えること。
        切り詰めが無い場合これらのキーは付かない。

        **`deprecation_warnings` が付いていたら必ず読むこと。** 結果に現れた
        用語のうち、**廃止済み(`owl:deprecated`)のもの**の IRI が入る。
        廃止された用語は「もう使うな」という意味であり、後継が定義されている
        ことが多い。回答にその用語を使う場合は、廃止されている旨を添えるか、
        `term_owner` で責任者に確認すべき対象として示すのが正確である。
        廃止が無い場合このキーは付かない。

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

    # ---- 廃止された用語の警告(P2B-03、ADR-0017 決定3) ----
    #
    # **エージェントにはヘッダではなく本文で渡す。** Core API は標準の
    # SPARQL Results JSON を保つためヘッダに載せるが(ADR-0001 の
    # 「SPARQL 1.1 Protocol をハード境界にする」)、**エージェントはヘッダを
    # 見ない**。見えなければ廃止された用語を自信を持って使ってしまう。
    # 「AI に正しいコンテキストを渡す」ことがこの製品の目的なので、
    # ここで届かないなら警告を作る意味がない。
    #
    # ツール結果の形はこの製品が定義するものであり、SPARQL の protocol では
    # ないため、キーを 1 つ足しても矛盾しない(`head` / `results` は
    # そのまま残るので、標準の形として読む側も壊れない)。
    header = response.headers.get(_DEPRECATED_TERMS_HEADER, "")
    deprecated = [part.strip() for part in header.split(",") if part.strip()]
    if deprecated:
        result["deprecation_warnings"] = deprecated

    # ---- 結果を切り詰めたことの通知(P2A-08、ADR-0025 決定4) ----
    #
    # **廃止の警告と同じ理由でヘッダから本文へ載せ替える。** エージェントは
    # ヘッダを見ないので、見えなければ**切り詰められた結果を「全部」として
    # 回答に使ってしまう**。「AI に正しいコンテキストを渡す」ことがこの製品の
    # 目的なので、ここで届かないなら上限を強制する意味が半分失われる。
    if response.headers.get(_RESULT_TRUNCATED_HEADER) == "true":
        result["result_truncated"] = True
        limit = response.headers.get(_RESULT_LIMIT_HEADER)
        if limit is not None:
            result["result_limit"] = int(limit)
        total = response.headers.get(_RESULT_TOTAL_ROWS_HEADER)
        if total is not None:
            result["result_total_rows"] = int(total)
    return result


@mcp.tool()
async def version_decisions(
    namespace: str, version: str, ctx: Context[Any, Any]
) -> list[dict[str, Any]]:
    """ある版について「誰が・いつ・なぜ」そう決めたかを返す。

    定義の根拠を確認するために使う。`sparql_query` が返すのは定義そのもので、
    その定義を**誰が承認したのか・なぜそう決めたのか**は含まれない。
    答えの根拠を説明する必要があるときにこのツールを使う。

    Args:
        namespace: 対象の名前空間の名前。`list_namespaces` で取得できる。
        version: 対象のバージョン。

    Returns:
        決定記録の一覧(起きた順)。各要素は action / actor / occurred_at /
        reason / diff を持つ。`reason` が空の記録もある(理由の記載は任意)。

    Raises:
        ToolError: 呼び出し元のトークンが無い、または検証を通らないとき。
    """
    async with _api_client(_forward_headers(ctx)) as client:
        response = await client.get(f"/namespaces/{namespace}/versions/{version}/decisions")
        response.raise_for_status()
        result: list[dict[str, Any]] = response.json()
        return result


@mcp.tool()
async def term_owner(namespace: str, term_iri: str, ctx: Context[Any, Any]) -> dict[str, Any]:
    """ある用語について「誰に聞けばよいか」を返す(ADR-0015、`P2B-04`)。

    定義に疑問があるとき、あるいは定義が答えを出すのに足りないときに、
    **確認すべき相手**を示すために使う。`version_decisions` が「誰が承認したか」
    (過去の行為者)を返すのに対し、こちらは**現在の責任者**を返す。両者は別物で、
    承認した人が今も担当しているとは限らない。

    Args:
        namespace: 対象の名前空間の名前。`list_namespaces` で取得できる。
        term_iri: 対象の用語の絶対 IRI(例 `https://example.com/ontology/retail#Product`)。

    Returns:
        `source` と `principal_ids` を持つ辞書。**`source` を必ず見ること。**

        - `term-owner`: その用語の責任者そのもの(`principal_ids` は 1 件)
        - `namespace-owners`: **その用語には責任者がいない。** 名前空間の
          責任者へ回している(1 件以上)。回答の際は「この用語の担当者は
          設定されていない」ことを添えるのが正確である
        - `unresolved`: 誰にも回せない(`principal_ids` は空)

    Raises:
        ToolError: 呼び出し元のトークンが無い、または検証を通らないとき。
    """
    async with _api_client(_forward_headers(ctx)) as client:
        response = await client.get(
            f"/namespaces/{namespace}/term-owners/resolve",
            params={"term_iri": term_iri},
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result


@mcp.tool()
async def term_mappings(namespace: str, ctx: Context[Any, Any]) -> list[dict[str, Any]]:
    """他の領域の用語との対応(領域間マッピング)を返す(ADR-0023、`P2B-10`)。

    **同じ言葉が領域によって違う意味を持つときに使う。** 営業の「優良顧客」と
    経理の「優良顧客」は別の定義でありうる。このシステムは**それを一つに
    統合しない** — 名前空間を分けたまま、対応関係を明示的な成果物として
    記録する(ADR-0009 決定8)。

    **`predicate` を必ず見ること。** `exactMatch` と `closeMatch` は意味が
    違い、後者は**交換可能とは限らない**。

    Args:
        namespace: 対象の名前空間の名前。`list_namespaces` で取得できる。

    Returns:
        マッピングの一覧。各件は次を持つ。

        - `source_term` / `target_term` / `predicate`: 対応関係そのもの
        - `reason`: **なぜ同じ(近い)と言えるのか。** 回答に使うときはこれを
          読むこと。理由が用途を限定していることがある
        - `reciprocal`: 相手側も同じ対応を宣言しているか。**偽は異常ではない**
          (相手がまだ宣言していないだけ)。ただし**片側の主張**であることは
          回答に添えるのが正確である
        - `disputed`: **相互に宣言されていて述語が食い違っている。** 真なら
          「両者の見解が一致していない」ことを必ず回答に添えること。
          `counterpart_predicate` に相手側の主張が入る

    Raises:
        ToolError: 呼び出し元のトークンが無い、または検証を通らないとき。
    """
    async with _api_client(_forward_headers(ctx)) as client:
        response = await client.get(f"/namespaces/{namespace}/mappings")
        response.raise_for_status()
        result: list[dict[str, Any]] = response.json()
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
