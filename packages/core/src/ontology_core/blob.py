"""正本 TTL の Blob 読み書き。

Blob のレイアウトは `<prefix><namespace>/<version>.ttl`。
Fuseki のローダ(containers/fuseki/load-snapshot.sh)がこのレイアウトを前提に
名前空間ごとのデータセットを組み立てるため、**変更するときは両方を直す**。
"""

from __future__ import annotations

from typing import Self

from azure.storage.blob.aio import BlobServiceClient, ContainerClient

from ontology_core.graphs import validate_namespace_name, validate_version

__all__ = ["OntologyBlobStore", "blob_path_for"]


def blob_path_for(prefix: str, namespace: str, version: str) -> str:
    """正本 TTL の Blob 上のパスを返す。"""
    validate_namespace_name(namespace)
    validate_version(version)
    return f"{prefix.rstrip('/')}/{namespace}/{version}.ttl"


class OntologyBlobStore:
    """承認済みオントロジー(正本)の置き場。"""

    def __init__(self, container_client: ContainerClient, *, prefix: str) -> None:
        self._container = container_client
        self._prefix = prefix

    @classmethod
    def from_account_url(
        cls, account_url: str, *, container: str, prefix: str, credential: object
    ) -> Self:
        service = BlobServiceClient(account_url=account_url, credential=credential)  # type: ignore[arg-type]
        return cls(service.get_container_client(container), prefix=prefix)

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
        await self._container.close()
