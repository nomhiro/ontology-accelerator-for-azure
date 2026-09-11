"""孤児のマニフェストの検出と削除(ADR-0033、`P2B-20`)。

`reconcile` の `orphan_blobs` は **`.ttl` だけ**を列挙するので
(`list_versions` が `.endswith(".ttl")` で絞っている)、
`versions/<ns>/_state.json` は視界に入っていなかった。`P2B-12` の残りである。

ここで固定するのは 3 つである。

1. **マニフェストは正本ではないので消す**(決定1)。`orphan_blobs`(TTL =
   正本なので報告だけ)とは扱いが違う。**性質が違うものを、扱いが同じという
   理由だけで揃えない**
2. **削除の口はマニフェストしか指せない**(決定2)。`delete_manifest` は
   名前空間名を取り、パスを取らない。**署名の狭さが強制である**
3. **消しても報告から消さない**(決定3)。毎回出るなら削除経路に取りこぼしが
   ある(ADR-0013 決定5 と同じ判断)
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.services.projection import ProjectionService
from ontology_core.blob import OntologyBlobStore, manifest_path_for
from ontology_core.sparql.client import SparqlStore

_NS = "orphan-ns"
_GONE = "gone-ns"
_BASE = "https://e.example/orphan#"


class _NullStore(SparqlStore):
    """射影を行わない代役。孤児の検出は Blob と PostgreSQL だけを見る。"""

    def __init__(self) -> None:
        self.datasets: set[str] = {"ds"}

    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
        return {"head": {"vars": []}, "results": {"bindings": []}}

    async def construct(self, sparql: str, *, dataset: str) -> str:
        return ""

    async def update(self, sparql: str, *, dataset: str) -> None: ...
    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None: ...
    async def put_default_graph(self, turtle: str, *, dataset: str) -> None: ...
    async def delete_graph(self, graph_iri: str, *, dataset: str) -> None: ...
    async def list_graphs(self, dataset: str) -> list[str]:
        return []

    async def has_default_graph_content(self, dataset: str) -> bool:
        return True

    async def list_datasets(self) -> list[str]:
        return sorted(self.datasets)

    async def create_dataset(self, dataset: str) -> None:
        self.datasets.add(dataset)

    async def delete_dataset(self, dataset: str) -> None:
        self.datasets.discard(dataset)


async def _setup(session: AsyncSession) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri=_BASE,
        created_by="admin-oid",
        require_two_person_approval=False,
    )
    await session.commit()


def _service(session: AsyncSession, blob: OntologyBlobStore) -> ProjectionService:
    return ProjectionService(
        session=session, blob=blob, store=_NullStore(), graph_iri_base="urn:ontology:graph"
    )


async def _write_manifest(blob: OntologyBlobStore, namespace: str) -> str:
    return await blob.put_manifest(
        namespace,
        {
            "schema": 2,
            "namespace": namespace,
            "current": None,
            "retain_superseded": 0,
            "retired": False,
            "versions": [],
            "generated_at": "t",
        },
    )


# ------------------------------------------------------------ 検出と削除


@pytest.mark.integration
async def test_正本に無い名前空間のマニフェストを消す(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**マニフェストは正本ではない**(ADR-0010 決定7)ので消す(ADR-0033 決定1)。

    `orphan_blobs`(TTL)が報告だけなのは**正本だから**で、揃える理由が無い。
    """
    await _setup(session)
    path = await _write_manifest(blob_store, _GONE)
    assert path in await blob_store.list_manifests()

    report = await _service(session, blob_store).reconcile()
    assert report.orphan_manifests == [path]
    assert path not in await blob_store.list_manifests()


@pytest.mark.integration
async def test_正本にある名前空間のマニフェストは消さない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    path = await _write_manifest(blob_store, _NS)

    report = await _service(session, blob_store).reconcile()
    assert report.orphan_manifests == []
    assert path in await blob_store.list_manifests()


@pytest.mark.integration
async def test_退役した名前空間のマニフェストは孤児ではない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**退役は削除ではない**(ADR-0032)。行は残るので孤児にならない。

    ここを間違えると、退役した名前空間の `skip:retired` が消えて
    **次の再構築で射影が復活する**。
    """
    from datetime import UTC, datetime

    await _setup(session)
    path = await _write_manifest(blob_store, _NS)
    await NamespaceRepository(session).set_retired(_NS, actor="a", reason="r", at=datetime.now(UTC))
    await session.commit()

    report = await _service(session, blob_store).reconcile()
    assert report.orphan_manifests == []
    assert path in await blob_store.list_manifests()


@pytest.mark.integration
async def test_消しても報告から消さない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**修復した事実を隠すと上流の原因が見えなくなる**(ADR-0013 決定5)。"""
    await _setup(session)
    path = await _write_manifest(blob_store, _GONE)
    report = await _service(session, blob_store).reconcile()
    # 消したうえで報告に残る。
    assert path in report.orphan_manifests
    assert path not in await blob_store.list_manifests()


@pytest.mark.integration
async def test_TTL_の一覧には混ざらない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`orphan_blobs` の意味を変えない**(ADR-0033 の代替案)。

    混ぜると、報告を読んだ運用者が「消してはいけない TTL」と
    「消してよいマニフェスト」を手で選別することになる。
    """
    await _setup(session)
    await _write_manifest(blob_store, _GONE)
    assert await blob_store.list_versions() == []


# ------------------------------------------------------- 削除の口の形


@pytest.mark.integration
async def test_delete_manifest_は名前空間名からパスを組み立てる(
    blob_store: OntologyBlobStore,
) -> None:
    """**署名の狭さが強制である**(ADR-0033 決定2)。

    引数はパスではないので、**このメソッドで `.ttl` を消すことはできない**。
    """
    path = await _write_manifest(blob_store, _NS)
    assert path == manifest_path_for("versions/", _NS)
    assert await blob_store.delete_manifest(_NS) is True
    assert path not in await blob_store.list_manifests()


@pytest.mark.integration
async def test_無いマニフェストの削除は_False(blob_store: OntologyBlobStore) -> None:
    """**例外にしない。** 既に無いことは異常ではない(冪等)。"""
    assert await blob_store.delete_manifest("never-existed") is False


@pytest.mark.integration
async def test_使えない名前空間名は削除しない(blob_store: OntologyBlobStore) -> None:
    """名前空間名はセキュリティ境界である(不変条件5)。"""
    from ontology_core.graphs import NamespaceNameError

    with pytest.raises(NamespaceNameError):
        await blob_store.delete_manifest("../etc")


@pytest.mark.integration
async def test_使えない段を含むマニフェストは自動削除せず報告する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**検証を回避して消しに行かない**(不変条件5)。

    `delete_manifest` は名前空間名からパスを組み立てる契約なので、
    名前空間名として使えない段は渡せない。**報告に残して運用者に委ねる。**
    """
    await _setup(session)
    # `versions/BAD/_state.json`(大文字は名前空間名として使えない)を直接書く。
    await blob_store._container.get_blob_client("versions/BAD/_state.json").upload_blob(
        json.dumps({"schema": 2, "namespace": "BAD", "versions": []}).encode("utf-8"),
        overwrite=True,
    )

    report = await _service(session, blob_store).reconcile()
    assert "versions/BAD/_state.json" in report.orphan_manifests
    assert any("自動削除しません" in f for f in report.failures)
    # 消えていない。
    assert "versions/BAD/_state.json" in await blob_store.list_manifests()


@pytest.mark.integration
async def test_マニフェストが無ければ何も報告しない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    report = await _service(session, blob_store).reconcile()
    assert report.orphan_manifests == []


@pytest.mark.integration
async def test_孤児の_TTL_とマニフェストは別の欄に出る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**2 つの報告を混ぜない**(ADR-0033 決定1 と却下した代替案)。

    孤児の `.ttl` は**正本なので消さない**(不変条件7)。マニフェストは
    射影なので消す。**同じ欄に混ぜると、報告を読んだ運用者が手で選別する
    ことになる。**

    `list_manifests` が `.ttl` も返すと `orphan_manifests` に TTL のパスが
    並ぶ(変異テストで実際に生き残った)。
    """
    await _setup(session)
    manifest = await _write_manifest(blob_store, _GONE)
    ttl = await blob_store.put_version(_GONE, "1.0.0", "<urn:s> <urn:p> <urn:o> .")

    report = await _service(session, blob_store).reconcile()
    assert report.orphan_manifests == [manifest], "TTL が混ざっている"
    assert report.orphan_blobs == [ttl]
    # **TTL は消さない。** マニフェストだけが消える。
    assert ttl in await blob_store.list_versions()
    assert manifest not in await blob_store.list_manifests()
