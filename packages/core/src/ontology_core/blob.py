"""正本 TTL の Blob 読み書き。

Blob のレイアウトは `<prefix><namespace>/<version>.ttl`。
Fuseki のローダ(containers/fuseki/load-snapshot.sh)がこのレイアウトを前提に
名前空間ごとのデータセットを組み立てるため、**変更するときは両方を直す**。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Self

from azure.core.credentials import AzureNamedKeyCredential, AzureSasCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import AzureError
from azure.storage.blob.aio import BlobServiceClient, ContainerClient

from ontology_core.graphs import validate_namespace_name, validate_version

__all__ = ["BlobStoreError", "OntologyBlobStore", "blob_path_for", "manifest_path_for"]


class BlobStoreError(RuntimeError):
    """Blob とのやり取りに失敗したことを表す。

    `ontology_core.sparql.client.SparqlStoreError` と対になる存在。呼び出し側
    (`ProjectionService.reconcile` 等)は「Blob の問題は必ず `BlobStoreError` で
    来る」契約に依拠して、per-item の失敗として記録して続行するか、正本への
    書き込みをロールバックするかといったトランザクションの境界を判断する。
    `azure.core.exceptions.AzureError`(接続不能・404 等)が生のまま漏れると、
    その判断が壊れて想定していない例外が外側の処理を誤発動させる。
    """


@asynccontextmanager
async def _wrap_errors(operation: str) -> AsyncIterator[None]:
    """`AzureError` を `BlobStoreError` に包む。

    `FusekiStore._send` が httpx の例外を `SparqlStoreError` に包むのと同じ考え方。
    Blob 操作(upload / download / list)ごとに個別の try/except を書くと重複するため
    ここに集約する。
    """
    try:
        yield
    except AzureError as exc:
        raise BlobStoreError(f"{operation}に失敗しました: {exc}") from exc


# `BlobServiceClient.__init__` の `credential` 引数の型そのまま。
# マネージド ID(`DefaultAzureCredential` 等)は `AsyncTokenCredential`、
# ローカル開発の Azurite では接続文字列由来のキーが渡される。
BlobCredential = (
    str
    | dict[str, str]
    | AzureNamedKeyCredential
    | AzureSasCredential
    | AsyncTokenCredential
    | None
)


def blob_path_for(prefix: str, namespace: str, version: str) -> str:
    """正本 TTL の Blob 上のパスを返す。"""
    validate_namespace_name(namespace)
    validate_version(version)
    return f"{prefix.rstrip('/')}/{namespace}/{version}.ttl"


def manifest_path_for(prefix: str, namespace: str) -> str:
    """名前空間ごとの承認状態マニフェスト(ADR-0010 決定7)の Blob 上のパスを返す。

    `_state.json` は版ファイルと衝突しない。`ontology_core.graphs._VERSION_PATTERN`
    (`[A-Za-z0-9][A-Za-z0-9.+-]{0,63}`)は先頭が英数字であることを要求し、`_`
    始まりの名前を版として許可しない。したがって `validate_version` を通る限り
    `_state.json`(`_state` は上記パターンにマッチしない)という名前の版ファイルは
    作られ得ず、この名前を安全に予約できる。**この根拠が失われて `_VERSION_PATTERN`
    が `_` 始まりを許すように変更されると、この予約は壊れる。変更する場合は
    ここも見直すこと。**
    """
    validate_namespace_name(namespace)
    return f"{prefix.rstrip('/')}/{namespace}/_state.json"


class OntologyBlobStore:
    """承認済みオントロジー(正本)の置き場。

    クライアントの所有権(`aclose()` で何を閉じるか)は生成元によって異なる。

      - `from_account_url` / `from_connection_string`: いずれも `BlobServiceClient` を
        新しく作るため、**`OntologyBlobStore` がその所有権を持つ**。`aclose()` は
        コンテナクライアントに加えてこの `BlobServiceClient` も閉じる。呼び出し側は
        何も後始末しなくてよい。
      - `from_client`: 呼び出し側が渡した既存の `BlobServiceClient` を使うだけで、
        **所有権は呼び出し側に残る**。`aclose()` はコンテナクライアントのみ閉じ、
        渡された `BlobServiceClient` は閉じない。呼び出し側が自分で `close()` すること
        (`packages/api/tests/conftest.py` の `blob_store` フィクスチャがこの形)。

    `get_container_client()` が返す `ContainerClient` は親の transport をラップするだけの
    `AsyncTransportWrapper` を内部で使っており、その `close()` は**意図的に no-op**
    (子クライアントが親の共有 transport を誤って閉じないための SDK の設計)。
    そのため `from_account_url` で作った `BlobServiceClient` への参照を保持しておかないと、
    `aclose()` だけでは aiohttp のセッションが一切閉じられずリークする。
    """

    def __init__(
        self,
        container_client: ContainerClient,
        *,
        prefix: str,
        owned_service: BlobServiceClient | None = None,
    ) -> None:
        self._container = container_client
        self._prefix = prefix
        # `from_account_url` でのみ設定される。設定されていれば `aclose()` で閉じる
        # (このクラスが生成したクライアントなので所有権を持つ)。
        self._owned_service = owned_service

    @classmethod
    def from_account_url(
        cls, account_url: str, *, container: str, prefix: str, credential: BlobCredential
    ) -> Self:
        service = BlobServiceClient(account_url=account_url, credential=credential)
        return cls(service.get_container_client(container), prefix=prefix, owned_service=service)

    @classmethod
    def from_connection_string(cls, connection_string: str, *, container: str, prefix: str) -> Self:
        """接続文字列(共有キー認証)から生成する。ローカル開発の Azurite 用。

        `from_account_url` + `DefaultAzureCredential` は Azurite(HTTP・共有キー認証)
        に認証できないため、ローカルで Blob 依存の経路(publish / versions / delete)を
        動かすにはこちらが必須になる(`final-fix-brief.md` 修正1 / I-1)。
        `from_account_url` と同じくこのメソッドが `BlobServiceClient` を新しく作るため、
        `owned_service` を渡して所有権を持たせる。`from_client` を流用すると
        (所有権を持たない設計のため)`aclose()` で閉じ漏れる。
        """
        service = BlobServiceClient.from_connection_string(connection_string)
        return cls(service.get_container_client(container), prefix=prefix, owned_service=service)

    @classmethod
    def from_client(cls, service: BlobServiceClient, *, container: str, prefix: str) -> Self:
        return cls(service.get_container_client(container), prefix=prefix)

    async def put_version(self, namespace: str, version: str, turtle: str) -> str:
        """TTL を置いて Blob パスを返す。"""
        path = blob_path_for(self._prefix, namespace, version)
        blob = self._container.get_blob_client(path)
        async with _wrap_errors("Blob の書き込み"):
            await blob.upload_blob(turtle.encode("utf-8"), overwrite=True)
        return path

    async def put_manifest(self, namespace: str, manifest: dict[str, Any]) -> str:
        """名前空間の承認状態マニフェスト(`_state.json`)を書いて Blob パスを返す。

        `put_version` と同じ `_wrap_errors` を通すため、失敗は `put_version` と
        同様に必ず `BlobStoreError` になる(不変条件4)。マニフェストは
        PostgreSQL の状態の射影であり正本ではない(ADR-0010 決定7)ので、
        呼び出し側(`ProjectionService`)はこの失敗を握り潰して `reconcile` に
        委ねる想定であり、この契約が壊れると意図しない例外伝播が起きる。
        """
        path = manifest_path_for(self._prefix, namespace)
        blob = self._container.get_blob_client(path)
        body = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        async with _wrap_errors("マニフェストの書き込み"):
            await blob.upload_blob(body, overwrite=True)
        return path

    async def get_version(self, blob_path: str) -> str:
        blob = self._container.get_blob_client(blob_path)
        async with _wrap_errors("Blob の読み取り"):
            stream = await blob.download_blob()
            return (await stream.readall()).decode("utf-8")

    async def list_versions(self, namespace: str | None = None) -> list[str]:
        """TTL の Blob パスを列挙する。"""
        if namespace is not None:
            validate_namespace_name(namespace)
            name_prefix = f"{self._prefix.rstrip('/')}/{namespace}/"
        else:
            name_prefix = self._prefix
        names: list[str] = []
        async with _wrap_errors("Blob 一覧の取得"):
            async for blob in self._container.list_blobs(name_starts_with=name_prefix):
                if blob.name.endswith(".ttl"):
                    names.append(blob.name)
        return sorted(names)

    async def aclose(self) -> None:
        """クライアントを閉じる。所有権のルールはクラス docstring を参照。"""
        await self._container.close()
        if self._owned_service is not None:
            await self._owned_service.close()
