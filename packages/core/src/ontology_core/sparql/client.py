"""トリプルストアへのアクセス抽象。

## 境界の引き方

読み書きは **SPARQL 1.1 Protocol** に限定する。ここが実装非依存の境界であり、
`SPARQL_QUERY_ENDPOINT` を差し替えれば既存の GraphDB / Stardog / Neptune などを
そのまま使える。

一方、**データセットの管理操作(作成・削除・一覧)は SPARQL 1.1 の範囲外**で、
ストアごとに固有の管理 API になる。名前空間の隔離をデータセット単位で行う設計上
避けられないため、この抽象はクエリだけでなく管理操作も包む。ストアを差し替える際に
書き換えが必要なのは、実質この管理操作の実装だけになる。

## 書き込みについて

トリプルストアは正本ではなく、正本(PostgreSQL + Blob)から再構築できる射影である。
したがって書き込みメソッドを呼ぶのは Core API だけで、必ず「正本に書く → 射影する」
の順に行う。ストアへ直接書いた内容はレプリカが入れ替わると失われる。
詳細は `docs/adr/0002-triple-store-as-rebuildable-projection.md` を参照。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any, Self

import httpx

__all__ = ["FusekiStore", "SparqlStore", "SparqlStoreError"]


class SparqlStoreError(RuntimeError):
    """ストアとのやり取りに失敗したことを表す。"""


class SparqlStore(ABC):
    """トリプルストアの抽象。"""

    # ---- SPARQL 1.1 Protocol(実装非依存) ----

    @abstractmethod
    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
        """SELECT / ASK を実行し SPARQL Results JSON を返す。"""

    @abstractmethod
    async def update(self, sparql: str, *, dataset: str) -> None:
        """SPARQL Update を実行する。Core API からのみ呼ぶこと。"""

    @abstractmethod
    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None:
        """Graph Store Protocol で名前付きグラフを置き換える。Core API からのみ呼ぶこと。"""

    # ---- 管理操作(ストア固有) ----

    @abstractmethod
    async def list_datasets(self) -> list[str]:
        """データセット名の一覧を返す。"""

    @abstractmethod
    async def create_dataset(self, dataset: str) -> None:
        """データセットを作成する。名前空間の隔離に用いる。"""

    @abstractmethod
    async def delete_dataset(self, dataset: str) -> None:
        """データセットを削除する。"""


class FusekiStore(SparqlStore):
    """Apache Jena Fuseki 実装。

    Example:
        >>> store = FusekiStore(
        ...     query_endpoint="http://fuseki/{dataset}/sparql",
        ...     update_endpoint="http://fuseki/{dataset}/update",
        ...     gsp_endpoint="http://fuseki/{dataset}/data",
        ...     admin_endpoint="http://fuseki/$/",
        ... )
    """

    def __init__(
        self,
        *,
        query_endpoint: str,
        update_endpoint: str,
        gsp_endpoint: str,
        admin_endpoint: str,
        admin_auth: tuple[str, str] | None = None,
        timeout_seconds: int = 30,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._query_endpoint = query_endpoint
        self._update_endpoint = update_endpoint
        self._gsp_endpoint = gsp_endpoint
        self._admin_endpoint = admin_endpoint.rstrip("/") + "/"
        self._admin_auth = admin_auth
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

    # ---- SPARQL 1.1 Protocol ----

    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
        response = await self._client.post(
            self._resolve(self._query_endpoint, dataset),
            content=sparql.encode("utf-8"),
            headers={
                "Content-Type": "application/sparql-query; charset=utf-8",
                "Accept": "application/sparql-results+json",
            },
        )
        self._raise_for_status(response, "クエリ")
        result: dict[str, Any] = response.json()
        return result

    async def update(self, sparql: str, *, dataset: str) -> None:
        response = await self._client.post(
            self._resolve(self._update_endpoint, dataset),
            content=sparql.encode("utf-8"),
            headers={"Content-Type": "application/sparql-update; charset=utf-8"},
        )
        self._raise_for_status(response, "更新")

    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None:
        response = await self._client.put(
            self._resolve(self._gsp_endpoint, dataset),
            params={"graph": graph_iri},
            content=turtle.encode("utf-8"),
            headers={"Content-Type": "text/turtle; charset=utf-8"},
        )
        self._raise_for_status(response, "グラフの置き換え")

    # ---- 管理操作(Fuseki 固有) ----

    async def list_datasets(self) -> list[str]:
        response = await self._client.get(
            f"{self._admin_endpoint}datasets", auth=self._admin_auth or httpx.USE_CLIENT_DEFAULT
        )
        self._raise_for_status(response, "データセット一覧の取得")
        payload: dict[str, Any] = response.json()
        return [str(entry["ds.name"]).lstrip("/") for entry in payload.get("datasets", [])]

    async def create_dataset(self, dataset: str) -> None:
        response = await self._client.post(
            f"{self._admin_endpoint}datasets",
            data={"dbName": dataset, "dbType": "tdb2"},
            auth=self._admin_auth or httpx.USE_CLIENT_DEFAULT,
        )
        self._raise_for_status(response, "データセットの作成")

    async def delete_dataset(self, dataset: str) -> None:
        response = await self._client.delete(
            f"{self._admin_endpoint}datasets/{dataset}",
            auth=self._admin_auth or httpx.USE_CLIENT_DEFAULT,
        )
        self._raise_for_status(response, "データセットの削除")

    # ---- 内部 ----

    @staticmethod
    def _resolve(template: str, dataset: str) -> str:
        """エンドポイントのテンプレートにデータセット名を埋める。

        `{dataset}` を含むテンプレートは名前空間ごとにデータセットを切り替える構成、
        含まない場合は単一データセット構成として扱う。
        """
        if "{dataset}" in template:
            return template.format(dataset=dataset)
        return template

    @staticmethod
    def _raise_for_status(response: httpx.Response, operation: str) -> None:
        if response.is_success:
            return
        raise SparqlStoreError(
            f"{operation}に失敗しました (HTTP {response.status_code}): {response.text[:500]}"
        )
