"""正本 TTL の Blob 読み書き。

Blob のレイアウトは `<prefix><namespace>/<version>.ttl`。
Fuseki のローダ(containers/fuseki/load-snapshot.sh)がこのレイアウトを前提に
名前空間ごとのデータセットを組み立てるため、**変更するときは両方を直す**。
"""

from __future__ import annotations

from typing import Self

from azure.core.credentials import AzureNamedKeyCredential, AzureSasCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.storage.blob.aio import BlobServiceClient, ContainerClient

from ontology_core.graphs import validate_namespace_name, validate_version

__all__ = ["OntologyBlobStore", "blob_path_for"]

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


class OntologyBlobStore:
    """承認済みオントロジー(正本)の置き場。

    クライアントの所有権(`aclose()` で何を閉じるか)は生成元によって異なる。

      - `from_account_url`: このメソッドが `BlobServiceClient` を新しく作るため、
        **`OntologyBlobStore` がその所有権を持つ**。`aclose()` はコンテナクライアントに
        加えてこの `BlobServiceClient` も閉じる。呼び出し側は何も後始末しなくてよい。
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
    def from_client(cls, service: BlobServiceClient, *, container: str, prefix: str) -> Self:
        return cls(service.get_container_client(container), prefix=prefix)

    async def put_version(self, namespace: str, version: str, turtle: str) -> str:
        """TTL を置いて Blob パスを返す。"""
        path = blob_path_for(self._prefix, namespace, version)
        blob = self._container.get_blob_client(path)
        await blob.upload_blob(turtle.encode("utf-8"), overwrite=True)
        return path

    async def get_version(self, blob_path: str) -> str:
        blob = self._container.get_blob_client(blob_path)
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
        async for blob in self._container.list_blobs(name_starts_with=name_prefix):
            if blob.name.endswith(".ttl"):
                names.append(blob.name)
        return sorted(names)

    async def aclose(self) -> None:
        """クライアントを閉じる。所有権のルールはクラス docstring を参照。"""
        await self._container.close()
        if self._owned_service is not None:
            await self._owned_service.close()
