"""正本 Blob クライアント。Azurite に対して実際に読み書きする。"""

from __future__ import annotations

import pytest

from ontology_core.blob import OntologyBlobStore, blob_path_for

pytestmark = pytest.mark.integration


def test_blob_path_layout() -> None:
    assert blob_path_for("versions/", "retail-core", "1.0.0") == "versions/retail-core/1.0.0.ttl"


def test_blob_path_rejects_traversal() -> None:
    from ontology_core.graphs import NamespaceNameError

    with pytest.raises(NamespaceNameError):
        blob_path_for("versions/", "../escape", "1.0.0")


async def test_put_then_get_roundtrip(blob_store: OntologyBlobStore) -> None:
    ttl = "@prefix ex: <https://e.example/#> .\nex:A a ex:B .\n"
    path = await blob_store.put_version("retail-core", "1.0.0", ttl)

    assert path == "versions/retail-core/1.0.0.ttl"
    assert await blob_store.get_version(path) == ttl


async def test_list_versions_filters_by_namespace(blob_store: OntologyBlobStore) -> None:
    await blob_store.put_version("alpha", "1.0.0", "# a\n")
    await blob_store.put_version("beta", "1.0.0", "# b\n")

    assert await blob_store.list_versions("alpha") == ["versions/alpha/1.0.0.ttl"]
    listed = await blob_store.list_versions()
    assert "versions/beta/1.0.0.ttl" in listed
