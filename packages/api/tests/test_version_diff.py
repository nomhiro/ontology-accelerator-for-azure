"""承認時の差分の記録と、差分エンドポイントのテスト(P2B-09、ADR-0016)。

**中心にあるのは 2 つ。**

1. **基準は「この承認によって `superseded` になる版」である**(決定2)。
   publish 時点の「現行」ではない
2. **差分の計算に失敗しても承認は失敗しない**(決定「失敗させる」を却下)。
   差分は記述的なメタデータであって承認の前提条件ではない
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.routers.versions import (
    PublishRequest,
    diff_version,
    publish_version,
)
from ontology_api.services.projection import ProjectionService
from ontology_core.auth.entra import Principal
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.models import NamespaceRole, OntologyVersion, PlatformRole
from ontology_core.sparql.client import SparqlStore

_NS = "diff-ns"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_STEWARD = Principal(subject="steward", object_id="steward-oid")
_MAINTAINER = Principal(subject="maintainer", object_id="maintainer-oid")
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")

_HEAD = """
@prefix ex: <https://e.example/#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
"""
_V1 = _HEAD + 'ex:Product a owl:Class ; rdfs:label "商品" .\nex:Customer a owl:Class .\n'
_V2 = _HEAD + 'ex:Product a owl:Class ; rdfs:label "製品" .\nex:Customer a owl:Class .\n'
_V3 = _HEAD + 'ex:Product a owl:Class ; rdfs:label "製品" .\n'  # Customer を削除
# **正しい縮め方**(ADR-0009 決定3): 削除ではなく廃止して残す。
_V_DEPRECATED = (
    _HEAD
    + 'ex:Product a owl:Class ; rdfs:label "製品" .\n'
    + "ex:Customer a owl:Class ; owl:deprecated true ;\n"
    + "    dcterms:isReplacedBy ex:Product .\n"
)


class _NullStore(SparqlStore):
    """射影を行わない代役。"""

    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        return {"results": {"bindings": []}}

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
        return []

    async def create_dataset(self, dataset: str) -> None: ...
    async def delete_dataset(self, dataset: str) -> None: ...


async def _setup(session: AsyncSession) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri="https://e.example/#",
        created_by=_ADMIN.object_id,
        require_two_person_approval=False,
    )
    repo = RoleRepository(session)
    for principal, role in (
        (_STEWARD, NamespaceRole.DATA_STEWARD),
        (_MAINTAINER, NamespaceRole.MAINTAINER),
        (_ANALYST, NamespaceRole.DATA_ANALYST),
    ):
        await repo.grant(
            namespace=_NS,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await session.commit()


def _service(session: AsyncSession, blob: OntologyBlobStore) -> ProjectionService:
    return ProjectionService(
        session=session,
        blob=blob,
        store=_NullStore(),
        graph_iri_base="urn:ontology:graph",
    )


async def _publish(
    session: AsyncSession,
    blob: OntologyBlobStore,
    settings: Settings,
    *,
    version: str,
    turtle: str,
) -> OntologyVersion:
    return await publish_version(
        namespace=_NS,
        payload=PublishRequest(version=version, turtle=turtle),
        principal=_STEWARD,
        session=session,
        blob=blob,
        store=_NullStore(),
        settings=settings,
        response=Response(),
    )


async def _approve(
    session: AsyncSession, blob: OntologyBlobStore, *, version: str
) -> OntologyVersion:
    service = _service(session, blob)
    await service.submit(namespace=_NS, version=version, actor=_STEWARD.object_id)
    return await service.approve(
        namespace=_NS, version=version, actor=_MAINTAINER.object_id, reason="テスト"
    )


async def _diff_of_approval(session: AsyncSession, version: str) -> dict[str, Any] | None:
    """`approved` の監査記録に載った差分の要約を返す。"""
    events = await AuditRepository(session).list_for_subject(_NS, f"{_NS}@{version}")
    approved = [e for e in events if e.action == "approved"]
    assert approved, f"approved の記録が無い: {[e.action for e in events]}"
    raw = approved[-1].diff
    return None if raw is None else dict(json.loads(raw))


# ---------------------------------------------------------------- 承認時の記録


@pytest.mark.integration
async def test_最初の承認では差分を記録しない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**「何も無かったところに全部追加された」という差分は情報量が無い**
    (ADR-0016 決定2)。"""
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    assert await _diff_of_approval(session, "1.0.0") is None


@pytest.mark.integration
async def test_2_回目の承認で前の版との差分を記録する(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V2)
    await _approve(session, blob_store, version="2.0.0")

    diff = await _diff_of_approval(session, "2.0.0")
    assert diff is not None
    assert diff["base_version"] == "1.0.0", "基準は superseded になる版でなければならない"
    assert diff["modified_terms"] == ["https://e.example/#Product"]
    assert diff["added_terms"] == []
    assert diff["removed_terms"] == []
    assert diff["empty"] is False


@pytest.mark.integration
async def test_IRI_の削除が差分に現れる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**ADR-0009 決定3 の規律違反はここで見える。**

    **`P2B-03`(ADR-0017 決定2)で承認がブロックされるようになった。**
    `P2B-09` の時点では「報告するだけ」だったが、それは廃止の仕組みが
    無い状態で削除を禁じると縮める正当な手段が 1 つも無くなるからで、
    廃止の経路ができた時点で強制に変えると ADR-0016 決定6 に書いてある。

    差分そのものは `deprecations` の口で承認前に見える(下の
    `test_廃止して残せば承認できる` が正しい縮め方を示している)。
    """
    from ontology_api.services.projection import DeprecationViolationError

    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V3)

    with pytest.raises(DeprecationViolationError) as exc:
        await _approve(session, blob_store, version="2.0.0")
    assert any("Customer" in p.message for p in exc.value.problems)


@pytest.mark.integration
async def test_廃止して残せば承認できる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**これが「縮める正当な手段」である**(ADR-0009 決定3、ADR-0017)。

    削除は拒否されるが、`owl:deprecated` を立てて残せば通る。差分では
    `deprecated_terms` に入り、`removed_terms` には入らない。
    """
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V_DEPRECATED)
    approved = await _approve(session, blob_store, version="2.0.0")

    assert approved.version == "2.0.0"
    diff = await _diff_of_approval(session, "2.0.0")
    assert diff is not None
    assert diff["removed_terms"] == [], "廃止を削除として報告してはいけない"
    assert diff["deprecated_terms"] == ["https://e.example/#Customer"]
    assert diff["has_removed_terms"] is False


@pytest.mark.integration
async def test_差分の計算に失敗しても承認は成功する(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**不変条件3 と同じ向きの判断**(ADR-0016)。

    差分は説明のための記述的なメタデータであって承認の前提条件ではない。
    Blob へ到達できないだけで承認が止まってはいけない。
    """
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V2)

    service = _service(session, blob_store)
    calls = {"n": 0}
    original = blob_store.get_version

    # `approve` は Blob を 4 回読む: SHACL 検証(1)、廃止の検査(2: 対象と基準)、
    # 差分(2: 対象と基準)。**差分の取得だけを落とす**ため、4 回目以降で失敗させる。
    #
    # **回数に依存するのは脆いが、ここで検証したいのは「差分の失敗が承認を
    # 止めないこと」**であり、SHACL 検証や廃止の検査を落とすとそちらの
    # 経路(502 / 422)に入ってしまい、検証したいものが検証できない。
    # 呼び出し回数が変わったらこのコメントごと更新する。
    async def flaky(path: str) -> str:
        calls["n"] += 1
        if calls["n"] >= 4:
            raise BlobStoreError("到達できません")
        return await original(path)

    await service.submit(namespace=_NS, version="2.0.0", actor=_STEWARD.object_id)
    object.__setattr__(blob_store, "get_version", flaky)
    try:
        approved = await service.approve(
            namespace=_NS, version="2.0.0", actor=_MAINTAINER.object_id, reason="テスト"
        )
    finally:
        object.__setattr__(blob_store, "get_version", original)

    assert approved.version == "2.0.0", "差分の失敗で承認が止まってはいけない"
    assert await _diff_of_approval(session, "2.0.0") is None, "差分は null のまま残る"


# ---------------------------------------------------------------- 差分エンドポイント


@pytest.mark.integration
async def test_基準を省略すると現在の_approved_版と比べる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V2)

    result = await diff_version(
        namespace=_NS,
        version="2.0.0",
        principal=_ANALYST,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
    )
    assert result["base_version"] == "1.0.0"
    assert result["diff"]["modified_terms"] == ["https://e.example/#Product"]


@pytest.mark.integration
async def test_基準を明示できる(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V3)

    result = await diff_version(
        namespace=_NS,
        version="2.0.0",
        principal=_ANALYST,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
        base="1.0.0",
    )
    assert result["base_version"] == "1.0.0"
    assert result["diff"]["removed_terms"] == ["https://e.example/#Customer"]


@pytest.mark.integration
async def test_基準が無ければ_diff_は_null(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**404 にしない。** 「基準が無い」はエラーではなく答えるべき事実である
    (最初の版では必ずこうなる)。"""
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)

    result = await diff_version(
        namespace=_NS,
        version="1.0.0",
        principal=_ANALYST,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
    )
    assert result["base_version"] is None
    assert result["diff"] is None


@pytest.mark.integration
async def test_存在しない基準を指定すると_404(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**`diff: null` で返さない。** 指定の誤りを「基準が無い」と混同させない。"""
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)

    with pytest.raises(HTTPException) as exc:
        await diff_version(
            namespace=_NS,
            version="1.0.0",
            principal=_ANALYST,
            session=session,
            blob=blob_store,
            store=_NullStore(),
            settings=settings,
            base="9.9.9",
        )
    assert exc.value.status_code == 404


@pytest.mark.integration
async def test_存在しない版は_404(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await diff_version(
            namespace=_NS,
            version="9.9.9",
            principal=_ANALYST,
            session=session,
            blob=blob_store,
            store=_NullStore(),
            settings=settings,
        )
    assert exc.value.status_code == 404


@pytest.mark.integration
async def test_差分の参照には_data_analyst_が必要(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    with pytest.raises(HTTPException) as exc:
        await diff_version(
            namespace=_NS,
            version="1.0.0",
            principal=_STRANGER,
            session=session,
            blob=blob_store,
            store=_NullStore(),
            settings=settings,
        )
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_差分の参照は状態を変えない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**その場で決定するための道具**であって、決定そのものではない。"""
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V2)

    before = await AuditRepository(session).list_for_subject(_NS, f"{_NS}@2.0.0")
    await diff_version(
        namespace=_NS,
        version="2.0.0",
        principal=_ANALYST,
        session=session,
        blob=blob_store,
        store=_NullStore(),
        settings=settings,
    )
    after = await AuditRepository(session).list_for_subject(_NS, f"{_NS}@2.0.0")
    assert len(before) == len(after), "差分を見ただけで監査記録が増えてはいけない"


# --------------- 差分レビューのルーティング先(ADR-0040、`P2B-13`)


async def _diff(
    session: AsyncSession,
    blob: OntologyBlobStore,
    settings: Settings,
    *,
    version: str = "2.0.0",
    principal: Principal = _ANALYST,
) -> dict[str, Any]:
    return await diff_version(
        namespace=_NS,
        version=version,
        principal=principal,
        session=session,
        blob=blob,
        store=_NullStore(),
        settings=settings,
    )


@pytest.mark.integration
async def test_差分に問い合わせ先が載る(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**ADR-0015 が「得られるもの」に挙げていた約束の実装である**(ADR-0040 決定2)。

    > 差分レビューのルーティング先が決まる。`P2B-09`(意味的差分)が
    > 「この差分を誰に見せるか」を答えられるようになる
    """
    from ontology_api.repositories.term_owners import TermOwnerRepository

    await _setup(session)
    await TermOwnerRepository(session).assign(
        namespace=_NS,
        term_iri="https://e.example/#Product",
        principal_id="product-owner-oid",
        assigned_by=_MAINTAINER.object_id,
    )
    await session.commit()
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V2)

    result = await _diff(session, blob_store, settings)
    owners = {entry["term_iri"]: entry for entry in result["owners"]}
    assert owners["https://e.example/#Product"]["source"] == "term-owner"
    assert owners["https://e.example/#Product"]["principal_ids"] == ["product-owner-oid"]


@pytest.mark.integration
async def test_責任者がいなければ名前空間の_owner_へ回す(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**フォールバックを隠さない**(ADR-0015 決定2)。

    `namespace-owners` は「用語の責任者がいないので名前空間の `owner` へ
    回した」という意味である。**`source` を見なければ「責任者がいる」と
    誤解する。**
    """
    from ontology_api.repositories.roles import RoleRepository

    await _setup(session)
    await RoleRepository(session).grant(
        namespace=_NS,
        principal_id="ns-owner-oid",
        role=NamespaceRole.OWNER,
        granted_by=_ADMIN.object_id,
    )
    await session.commit()
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V2)

    result = await _diff(session, blob_store, settings)
    owners = {entry["term_iri"]: entry for entry in result["owners"]}
    entry = owners["https://e.example/#Product"]
    assert entry["source"] == "namespace-owners"
    assert entry["principal_ids"] == ["ns-owner-oid"]


@pytest.mark.integration
async def test_誰にも届かないことを明示する(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**`unresolved` は「誰にも届かない」である**(ADR-0015 決定2)。

    `_setup` は `owner` を付与していないので、この名前空間には回す先が無い。
    **空のリストを「問題なし」と読ませない** — `source` がそれを言う。
    """
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V2)

    result = await _diff(session, blob_store, settings)
    entry = next(e for e in result["owners"] if e["term_iri"] == "https://e.example/#Product")
    assert entry["source"] == "unresolved"
    assert entry["principal_ids"] == []


@pytest.mark.integration
async def test_差分が無い版でも応答の形は変わらない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """最初の版(基準が無い)では `diff` が `null` になる(ADR-0016)。

    **そのときも `owners` のキーは入る**(ADR-0040 決定2)。形が入力で変わる
    契約はクライアントに分岐を強いる。空の配列は「回す用語が無い」であり、
    `diff` が `null` であることが既にその理由を言っている。
    """
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)

    result = await _diff(session, blob_store, settings, version="1.0.0")
    assert result["diff"] is None
    assert result["owners"] == []


@pytest.mark.integration
async def test_廃止した用語の問い合わせ先も載る(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**廃止は縮めることの正しい形である**(不変条件8)。

    廃止した用語こそ「誰に確認すべきか」が要る。`deprecated_terms` も
    問い合わせ先の対象に含める(ADR-0040 決定4)。
    """
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V_DEPRECATED)

    result = await _diff(session, blob_store, settings)
    assert result["diff"]["deprecated_terms"] == ["https://e.example/#Customer"]
    listed = {entry["term_iri"] for entry in result["owners"]}
    assert "https://e.example/#Customer" in listed


def _with_blank_nodes(body: str, count: int) -> str:
    """空白ノードを `count` 個持つ TTL を作る。

    `MAX_BLANK_NODES` を超えると意味的差分は `modified_terms` を計算しない
    (ADR-0016 決定5)。**そのとき `deprecated_terms` だけが廃止を伝える。**
    """
    # **SHACL の形状にしない。** `approve` が SHACL 検証をするので、
    # 不正な shapes を混ぜると承認そのものが落ちる(実際に踏んだ)。
    # 独自述語の空白ノードなら pyshacl は形状として読まない。
    holders = "\n".join(f"ex:Holder{i} ex:detail [ ex:index {i} ] ." for i in range(count))
    return body + holders + "\n"


@pytest.mark.integration
async def test_modified_terms_が計算できなくても廃止は問い合わせ先に載る(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**`deprecated_terms` を対象に含める理由がここにある**(ADR-0040 決定4)。

    廃止された用語は普通 `modified_terms` にも現れる(`owl:deprecated true` を
    得たので変更されている)。**空白ノードが多い版では `modified_terms` が
    `null` になる**(ADR-0016 決定5)ので、そのとき廃止を伝えるのは
    `deprecated_terms` だけである。

    **変異テストで見つけた穴である** — `deprecated_terms` を対象から外す変異が、
    通常の版では `modified_terms` に救われて生き残った。
    """
    from ontology_core.diff import MAX_BLANK_NODES

    over = MAX_BLANK_NODES + 1
    await _setup(session)
    await _publish(
        session, blob_store, settings, version="1.0.0", turtle=_with_blank_nodes(_V1, over)
    )
    await _approve(session, blob_store, version="1.0.0")
    await _publish(
        session,
        blob_store,
        settings,
        version="2.0.0",
        turtle=_with_blank_nodes(_V_DEPRECATED, over),
    )

    result = await _diff(session, blob_store, settings)
    assert result["diff"]["modified_terms"] is None, "空白ノードの上限に達していない"
    assert result["diff"]["deprecated_terms"] == ["https://e.example/#Customer"]
    listed = {entry["term_iri"] for entry in result["owners"]}
    assert "https://e.example/#Customer" in listed, (
        "modified_terms が計算できないとき、廃止を伝えるのは deprecated_terms だけである"
    )


@pytest.mark.integration
async def test_問い合わせ先は重複しない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """同じ用語が複数の一覧に現れても 1 件にまとめる。

    廃止した用語は `deprecated_terms` と `modified_terms` の両方に現れうる。
    """
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V_DEPRECATED)

    result = await _diff(session, blob_store, settings)
    listed = [entry["term_iri"] for entry in result["owners"]]
    assert len(listed) == len(set(listed)), f"問い合わせ先が重複している: {listed}"


@pytest.mark.integration
async def test_責任者がいなくても承認は止まらない(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """**これが ADR-0040 決定1・3 の本題である。**

    責任者を承認の条件にすると、「責任者がいない用語を含む版を承認できない」
    (人質)か「責任者がいなければ誰でも承認できる」(不変条件11 が最も避ける
    形)のどちらかになる。**報告はする、ブロックはしない。**

    `_setup` は `term_owners` を 1 件も付けていない。それでも承認は通る。
    """
    await _setup(session)
    await _publish(session, blob_store, settings, version="1.0.0", turtle=_V1)
    await _approve(session, blob_store, version="1.0.0")
    await _publish(session, blob_store, settings, version="2.0.0", turtle=_V2)

    # 問い合わせ先は解決できない。
    result = await _diff(session, blob_store, settings)
    assert all(e["source"] == "unresolved" for e in result["owners"])

    # それでも承認は通る。
    approved = await _approve(session, blob_store, version="2.0.0")
    assert approved.status.value == "approved"
