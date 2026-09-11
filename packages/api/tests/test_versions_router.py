"""バージョン投入ルーターの例外マッピング。

M-1: `_next_version` が自動採番できない版に遭遇したときに `AutoVersionError` が
`ProjectionService.publish` から抜けて `routers/versions.py` まで伝播し、
未捕捉の 500 ではなく 422 に変換されることを確認する。サービス層の挙動は
`test_projection.py` で確認済みなので、ここではルーターの例外マッピングだけを見る。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_api.routers import versions as versions_module
from ontology_api.routers.versions import (
    PublishRequest,
    RejectRequest,
    TransitionRequest,
    approve_version,
    list_version_decisions,
    publish_version,
    reject_version,
    submit_version,
)
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.graphs import NamespaceNameError
from ontology_core.models import OntologyVersionStatus
from ontology_core.sparql.client import SparqlStore

pytestmark = pytest.mark.integration

TTL = "@prefix ex: <https://e.example/#> .\nex:A a ex:Class .\n"
# 述語だけで終わる。rdflib は BadSyntax ではなく IndexError を投げる経路
# (packages/core/tests/test_turtle.py で実測済み)。P1-C2 のブリーフの例そのもの。
BROKEN_TTL = "@prefix ex: <http://e/> . ex:A a"
_PRINCIPAL = Principal.local_dev()
# 四眼原則(ADR-0014 決定4)があるため、承認は publish と別の主体で行う。
# **既定を緩めるのではなく、正しく別人にする。** platform-admin でも
# 四眼原則は飛び越えられない(決定5)。
_APPROVER = Principal(
    subject="approver", object_id="approver-oid", platform_roles=("platform-admin",)
)


class _NullStore(SparqlStore):
    """射影は成功したことにするだけの最小フェイク。この経路には未到達のはず。"""

    async def query(self, sparql: str, *, dataset: str) -> dict:  # type: ignore[type-arg]
        return {"results": {"bindings": []}}

    async def construct(self, sparql: str, *, dataset: str) -> str:
        return ""

    async def update(self, sparql: str, *, dataset: str) -> None:
        return None

    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None:
        return None

    async def put_default_graph(self, turtle: str, *, dataset: str) -> None:
        return None

    async def delete_graph(self, graph_iri: str, *, dataset: str) -> None:
        return None

    async def list_graphs(self, dataset: str) -> list[str]:
        return []

    async def has_default_graph_content(self, dataset: str) -> bool:
        return True

    async def list_datasets(self) -> list[str]:
        return []

    async def create_dataset(self, dataset: str) -> None:
        return None

    async def delete_dataset(self, dataset: str) -> None:
        return None


async def test_publish_version_maps_auto_version_error_to_422(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    name = "ver-422"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()

    store = _NullStore()
    # 明示バージョン(英字を含む)での publish は成功する。
    await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=TTL, version="1.beta.0"),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=Response(),
    )

    # 以後 version 省略の publish は 500 ではなく 422 になる。
    with pytest.raises(HTTPException) as exc_info:
        await publish_version(
            namespace=name,
            payload=PublishRequest(turtle=TTL + "\nex:B a ex:Class .\n", version=None),
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
            response=Response(),
        )

    assert exc_info.value.status_code == 422
    assert "1.beta.0" in str(exc_info.value.detail)


async def test_publish_version_maps_turtle_syntax_error_to_422(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """P1-C2: 構文が壊れた TTL の publish は 422 になり、Blob にも PostgreSQL にも
    何も残らない。

    「422 を返す」だけでは、Blob に書いた後で検証している実装でも通ってしまう
    ため、サービス層(test_projection.py)と同じく Blob・PostgreSQL の状態も
    ここで明示的に確認する。
    """
    from ontology_api.repositories.versions import VersionRepository

    name = "ver-ttl-422"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()
    store = _NullStore()

    with pytest.raises(HTTPException) as exc_info:
        await publish_version(
            namespace=name,
            payload=PublishRequest(turtle=BROKEN_TTL),
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
            response=Response(),
        )

    assert exc_info.value.status_code == 422
    assert await blob_store.list_versions(name) == []
    assert await VersionRepository(session).list_for(name) == []


async def test_submit_approve_reject_router_status_codes(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """必須テスト4: 不正な遷移が 409、存在しない版が 404、reject の空 reason が 422。"""
    name = "ver-approval"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()
    store = _NullStore()

    # 存在しない版への submit は 404。
    with pytest.raises(HTTPException) as exc_info:
        await submit_version(
            namespace=name,
            version="9.9.9",
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 404

    published = await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=TTL, version="1.0.0"),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=Response(),
    )

    # draft を approve しようとすると 409。
    with pytest.raises(HTTPException) as exc_info:
        await approve_version(
            namespace=name,
            version=published.version,
            principal=_APPROVER,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 409

    submitted = await submit_version(
        namespace=name,
        version=published.version,
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    assert submitted.status.value == "in-review"

    # in-review を再度 submit しようとすると 409。
    with pytest.raises(HTTPException) as exc_info:
        await submit_version(
            namespace=name,
            version=published.version,
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 409

    approved = await approve_version(
        namespace=name,
        version=published.version,
        principal=_APPROVER,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    assert approved.status.value == "approved"
    # **承認者は publish した主体とは別人である**(四眼原則。ADR-0014 決定4)。
    # `approved_by` に実際に承認した主体が記録されることを確認する。
    assert approved.approved_by == (_APPROVER.object_id or _APPROVER.subject)
    assert approved.created_by != approved.approved_by

    # approved を submit しようとすると 409。
    with pytest.raises(HTTPException) as exc_info:
        await submit_version(
            namespace=name,
            version=published.version,
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 409


async def test_reject_empty_reason_is_422(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """RejectRequest.reason は空文字を拒否する(pydantic の入口検証で 422)。"""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        RejectRequest(reason="")


async def test_reject_unknown_version_is_404(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    name = "ver-reject-404"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()
    store = _NullStore()

    with pytest.raises(HTTPException) as exc_info:
        await reject_version(
            namespace=name,
            version="9.9.9",
            payload=RejectRequest(reason="無効な版"),
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 404


async def test_publish_version_maps_concurrent_update_error_to_409(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """P1-13: 基準バージョンが古いと 409 になり、正本には何も残らない。

    「409 を返す」だけでは Blob に書いた後で検査している実装でも通ってしまう
    ため、Blob と PostgreSQL の状態もここで確認する
    (test_publish_version_maps_turtle_syntax_error_to_422 と同じ方針)。
    """
    from ontology_api.repositories.versions import VersionRepository

    name = "ver-409-base"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()
    store = _NullStore()

    first = await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=TTL),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=Response(),
    )
    # bob が先に公開する。
    await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=TTL + "ex:B a ex:Class .\n", base_version=first.version),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=Response(),
    )

    blobs_before = sorted(await blob_store.list_versions(name))
    versions_before = len(await VersionRepository(session).list_for(name))

    with pytest.raises(HTTPException) as exc_info:
        await publish_version(
            namespace=name,
            payload=PublishRequest(turtle=TTL + "ex:C a ex:Class .\n", base_version=first.version),
            principal=_PRINCIPAL,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
            response=Response(),
        )

    assert exc_info.value.status_code == 409
    assert first.version in str(exc_info.value.detail)
    # 正本は増えていない(検査が Blob への書き込みより前にあること)。
    assert sorted(await blob_store.list_versions(name)) == blobs_before
    assert len(await VersionRepository(session).list_for(name)) == versions_before


async def test_publish_version_returns_200_when_content_is_unchanged(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """P1-26: 同一内容の再投入は 201 ではなく 200 を返す。

    `ProjectionService.publish` は同一内容(`content_hash` が一致)の再投入で
    **既存の版をそのまま返す**(冪等。意図した設計)。しかしルータの
    `status_code` が 201 に固定されていたため、**新規作成していないのに
    201 Created が返っていた**。クライアントが 201 を「新しい版ができた」と
    解釈すると版番号を取り違える余地がある。

    POSIX 経路の検証(P1-14)で `postdeploy.sh` を 2 回目として実行したとき、
    名前空間は 409、submit / approve は 409 なのに publish だけ 201 だったことで
    気づいた。
    """
    from ontology_api.repositories.versions import VersionRepository

    name = "ver-idempotent"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()
    store = _NullStore()

    first_response = Response()
    first = await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=TTL),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=first_response,
    )
    assert first_response.status_code == 201

    # 同じ本文をもう一度。新しい版は作られない。
    second_response = Response()
    second = await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=TTL),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=second_response,
    )

    assert second_response.status_code == 200
    assert second.version == first.version
    assert len(await VersionRepository(session).list_for(name)) == 1


async def test_publish_version_returns_201_for_a_new_version(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """内容が変われば新規作成なので 201 のまま。"""
    name = "ver-new-201"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()
    store = _NullStore()

    r1 = Response()
    await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=TTL),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=r1,
    )
    r2 = Response()
    await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=TTL + "ex:B a ex:Class .\n"),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=r2,
    )

    assert r1.status_code == 201
    assert r2.status_code == 201


# ---- P2B-08: 決定記録を参照時に返す ----


async def test_list_version_decisions_returns_the_why(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """版の決定記録が理由付きで、起きた順に返る。

    ADR-0009 決定7。「誰が承認した定義に基づく答えかを説明できること」は
    この製品の中核価値(ADR-0006)なので、理由が読み出せなければ意味がない。
    """
    name = "ver-decisions"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()
    store = _NullStore()

    published = await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=TTL, reason="初版の骨格"),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=Response(),
    )
    await submit_version(
        namespace=name,
        version=published.version,
        payload=TransitionRequest(reason="レビュー依頼"),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    await approve_version(
        namespace=name,
        version=published.version,
        payload=TransitionRequest(reason="想定質問を満たす"),
        principal=_APPROVER,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )

    decisions = await list_version_decisions(
        namespace=name,
        version=published.version,
        principal=_PRINCIPAL,
        session=session,
    )

    assert [d.action for d in decisions] == ["published", "submitted", "approved"]
    assert [d.reason for d in decisions] == ["初版の骨格", "レビュー依頼", "想定質問を満たす"]
    assert all(d.actor for d in decisions)


async def test_list_version_decisions_rejects_invalid_namespace(
    session: AsyncSession,
) -> None:
    """名前空間名はセキュリティ境界なので入口で検証する(不変条件5)。"""
    with pytest.raises(HTTPException) as exc_info:
        await list_version_decisions(
            namespace="../evil",
            version="1.0.0",
            principal=_PRINCIPAL,
            session=session,
        )
    assert exc_info.value.status_code == 400


async def test_list_version_decisions_is_404_for_an_unknown_version(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """存在しない版は空配列ではなく 404。

    空配列だと「決定記録が無い版」と「存在しない版」の区別がつかない。
    """
    del blob_store, settings
    name = "ver-decisions-404"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=f"https://e.example/{name}#",
        created_by="t",
    )
    await session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await list_version_decisions(
            namespace=name,
            version="9.9.9",
            principal=_PRINCIPAL,
            session=session,
        )
    assert exc_info.value.status_code == 404


# ---- P2A-05: SHACL 検証 ----


def test_graphs_validate_version_is_not_shadowed_by_a_handler() -> None:
    """**`validate_version` がハンドラ名で上書きされていないこと。**

    ルータのモジュールは `ontology_core.graphs.validate_version` を入口検証に
    使っている。ハンドラを同名 `validate_version` で定義すると、以後の
    `validate_version(version)` がハンドラを呼ぶようになり、入口検証が黙って
    効かなくなる(実際に一度踏んだ)。同じ間違いを機械的に防ぐ。
    """
    from ontology_core.graphs import validate_version as graphs_validate_version

    # モジュールの名前空間を直接引く(mypy の explicit-export を避けるため
    # getattr を使う。ここで見たいのは「この名前に何が束縛されているか」である)。
    bound = getattr(versions_module, "validate_version")  # noqa: B009
    assert bound is graphs_validate_version
    # 検証関数として振る舞うこと(不正な版を弾く)。
    with pytest.raises(NamespaceNameError):
        bound("../evil")


async def test_approve_is_blocked_when_shacl_is_violated(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """SHACL 違反があると承認が 422 で止まり、状態が変わらないこと。

    ADR-0009 決定1: 形式的に決定可能なものはブロッキング。
    ADR-0010 決定1 の分離により、**publish は止めない**(draft は編集途中で
    ありうる)。止めるのはエージェントが答えの根拠にする現行版にする瞬間。
    """
    name = "ver-shacl-block"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri="https://e.example/#",
        created_by="t",
    )
    await session.commit()
    store = _NullStore()

    violating = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix ex:  <https://e.example/#> .
ex:Product a owl:Class .
ex:ProductShape a sh:NodeShape ;
    sh:targetClass ex:Product ;
    sh:property [ sh:path ex:sku ; sh:datatype xsd:string ; sh:minCount 1 ] .
ex:p1 a ex:Product .
"""
    published = await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=violating),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=Response(),
    )
    # publish は通る(draft は編集途中でありうる)。
    assert published.status is OntologyVersionStatus.DRAFT

    await submit_version(
        namespace=name,
        version=published.version,
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )

    with pytest.raises(HTTPException) as exc_info:
        await approve_version(
            namespace=name,
            version=published.version,
            principal=_APPROVER,
            session=session,
            blob=blob_store,
            store=store,
            settings=settings,
        )
    assert exc_info.value.status_code == 422
    assert "SHACL" in str(exc_info.value.detail)

    # 状態は変わっていない(検証は遷移より前にある)。
    still = await VersionRepository(session).get(name, published.version)
    assert still is not None
    assert still.status is OntologyVersionStatus.IN_REVIEW


async def test_validate_endpoint_reports_without_changing_state(
    session: AsyncSession, blob_store: OntologyBlobStore, settings: Settings
) -> None:
    """検証エンドポイントは報告するだけで状態を変えないこと(ADR-0005)。"""
    name = "ver-shacl-report"
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri="https://e.example/#",
        created_by="t",
    )
    await session.commit()
    store = _NullStore()

    published = await publish_version(
        namespace=name,
        payload=PublishRequest(turtle=TTL),
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
        response=Response(),
    )
    report = await versions_module.validate_version_shacl(
        namespace=name,
        version=published.version,
        principal=_PRINCIPAL,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings,
    )
    assert report.conforms is True
    still = await VersionRepository(session).get(name, published.version)
    assert still is not None
    assert still.status is OntologyVersionStatus.DRAFT
