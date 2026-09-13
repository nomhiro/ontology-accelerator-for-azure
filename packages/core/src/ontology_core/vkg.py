"""仮想グラフ(Ontop VKG)への照会(ADR-0046、`P3-01`)。

## トリプルストアの抽象と別にする理由

`ontology_core.sparql.client.SparqlStore` は**射影先**の抽象である。
データセットを作り、名前付きグラフを置き換え、更新を流す
(不変条件2 の書き込み順序の最後に来るもの)。

仮想グラフはそのどれもしない。**書き込む先が無い**(正本は顧客の
リレーショナル DB であり、こちらは読むだけである)。実測で、Ontop の
エンドポイントは SPARQL Update を **415 で拒否し、`/update` 系の経路を
持たない** — つまり読み取り専用であることが構造で保証されている。

`SparqlStore` の抽象に押し込めると、`update` / `put_graph` /
`create_dataset` を「例外を投げる実装」で埋めることになる。それは
ADR-0034 が `construct` を抽象メソッドにした理由(既定実装で例外を
投げると持ち込みストアで黙って 500 になる)の裏返しであり、
**契約に嘘を書く**ことになる。

## 失敗の本文を通さない

**Ontop のエラー本文を呼び出し元に渡してはいけない**(ADR-0046 決定10)。

実測: マッピングが参照する関係が無いとき、Ontop は
`Cannot find relation "x"."y" (available choices: [...])` を返し、
**その `available choices` に接続ユーザから見える全関係が入る**
(こちらの制御平面の表も含まれていた)。これは顧客 DB のスキーマの
開示であり、照会者が知ってよい情報ではない。

`FusekiStore._raise_for_status` は本文を 500 文字まで含める。
**あちらは自分の射影先**なので、漏れるのは自分が書いた内容と
呼び出し元自身のクエリである。ここは相手が違う。
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Any, Self

import httpx

__all__ = [
    "VIRTUAL_GRAPH_SPARQL_MEDIA_TYPE",
    "VirtualGraphClient",
    "VirtualGraphError",
    "VirtualGraphNotConfiguredError",
    "resolve_endpoint",
]

logger = logging.getLogger(__name__)

#: 仮想グラフへ送るクエリの `Content-Type`。
VIRTUAL_GRAPH_SPARQL_MEDIA_TYPE = "application/sparql-query; charset=utf-8"


class VirtualGraphError(RuntimeError):
    """仮想グラフへの照会が失敗したことを表す。

    **原因の詳細は入れない。** 詳細はログに出す(ADR-0046 決定10)。
    """


class VirtualGraphNotConfiguredError(VirtualGraphError):
    """仮想グラフのエンドポイントが設定されていないことを表す。

    **「設定されていない」を「データが無い」と混ぜない**(ADR-0046 決定11)。
    仮想グラフを立てていない環境で照会すると、混ぜた実装では
    **空の結果**が返る。空の結果は「そのソースには該当する行が無い」と
    見分けがつかないので、運用者は設定漏れに気づけない。
    """


def resolve_endpoint(template: str | None, *, namespace: str, source: str) -> str:
    """エンドポイントのテンプレートに名前空間とソース名を埋める。

    Ontop の 1 インスタンスは **1 つの DB しか見ない**(`--db-url` が 1 つ)。
    そのためソースごとにインスタンスが立つ構成になり、宛先もソースごとに
    変わる。テンプレートは `{namespace}` と `{source}` を受け付ける。

    プレースホルダを含まないテンプレートは**単一インスタンス構成**として
    扱う(ローカル開発がこれ)。`FusekiStore._resolve` と同じ形にしてある。

    Raises:
        VirtualGraphNotConfiguredError: テンプレートが未設定または空のとき。
    """
    if template is None or not template.strip():
        raise VirtualGraphNotConfiguredError(
            "仮想グラフのエンドポイントが設定されていません。"
            "VKG_ENDPOINT_TEMPLATE を設定してください"
        )
    resolved = template
    if "{namespace}" in resolved:
        resolved = resolved.replace("{namespace}", namespace)
    if "{source}" in resolved:
        resolved = resolved.replace("{source}", source)
    return resolved


class VirtualGraphClient:
    """仮想グラフの SPARQL エンドポイントへの読み取り専用クライアント。

    Example:
        ```python
        async with VirtualGraphClient(timeout_seconds=30) as client:
            result = await client.query(
                "SELECT * WHERE { ?s ?p ?o } LIMIT 10",
                endpoint="http://ontop:8080/sparql",
            )
        ```

    **`update` は無い。** 実測で拒否されることを確かめたうえで、
    こちらにも経路を置かない(ADR-0046 決定3)。
    """

    def __init__(
        self,
        *,
        timeout_seconds: int = 30,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """自前で生成した HTTP クライアントを閉じる。"""
        if self._owns_client:
            await self._client.aclose()

    async def query(self, sparql: str, *, endpoint: str) -> dict[str, Any]:
        """`SELECT` / `ASK` を実行し SPARQL Results JSON を返す。

        Raises:
            VirtualGraphError: 到達できない、エラー応答、または応答が
                SPARQL Results JSON でないとき。
        """
        payload = await self._send(
            sparql,
            endpoint=endpoint,
            accept="application/sparql-results+json",
            operation="クエリ",
        )
        try:
            parsed = payload.json()
        except ValueError as exc:
            logger.warning("仮想グラフ %s の応答を JSON として読めませんでした", endpoint)
            raise VirtualGraphError("仮想グラフの応答を解釈できませんでした") from exc
        if not isinstance(parsed, dict):
            raise VirtualGraphError("仮想グラフの応答が SPARQL Results JSON ではありません")
        return parsed

    async def construct(self, sparql: str, *, endpoint: str) -> str:
        """`CONSTRUCT` / `DESCRIBE` を実行し Turtle を返す。

        Raises:
            VirtualGraphError: 到達できない、またはエラー応答のとき。
        """
        response = await self._send(
            sparql,
            endpoint=endpoint,
            accept="text/turtle",
            operation="クエリ(RDF)",
        )
        return response.text

    async def _send(
        self,
        sparql: str,
        *,
        endpoint: str,
        accept: str,
        operation: str,
    ) -> httpx.Response:
        try:
            response = await self._client.post(
                endpoint,
                content=sparql.encode("utf-8"),
                headers={"Content-Type": VIRTUAL_GRAPH_SPARQL_MEDIA_TYPE, "Accept": accept},
            )
        except httpx.HTTPError as exc:
            # 宛先は残す(運用者が設定を直すのに必要)。**相手の本文は残さない。**
            logger.warning("仮想グラフ %s に到達できませんでした: %s", endpoint, exc)
            raise VirtualGraphError(f"仮想グラフに到達できませんでした ({operation})") from exc

        if response.is_success:
            return response

        # **本文をログにだけ出す**(ADR-0046 決定10)。呼び出し元へは
        # ステータスコードまでしか渡さない。
        logger.warning(
            "仮想グラフ %s が %s に失敗しました (HTTP %s): %s",
            endpoint,
            operation,
            response.status_code,
            response.text[:2000],
        )
        raise VirtualGraphError(
            f"仮想グラフの{operation}が失敗しました (HTTP {response.status_code})。"
            "詳細はサーバのログを確認してください"
        )
