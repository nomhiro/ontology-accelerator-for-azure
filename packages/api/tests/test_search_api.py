"""用語検索の口と、埋め込みの作り直し(ADR-0050、`P3-02`)。

ここで固定するのは 8 つである。

1. **承認済み版からしか埋め込みを作らない。** 提案中の版の用語を検索に
   出すと、**四眼原則を通っていない語彙が事実上流通する**
2. **「ベクトルが使えない」を「該当なし」にしない**(決定8)。3-gram だけで
   結果を返し、理由を必ず添える
3. **モデルが落ちても検索は成立する。** ベクトルの経路の失敗を例外に
   しない(不変条件3 と同じ向き)
4. **作り直しは名前空間の行を丸ごと入れ替える**(同じ用語が二重にならない)
5. **権限**: 検索は `data-analyst`、作り直しは `maintainer`
6. **退役した名前空間では作り直さない**(費用だけが発生する)
7. **監査に残る。** 誰が・いつ・なぜ作り直したか
8. **空の問いで全件を返さない**
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.embeddings import TermEmbeddingRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.routers.search import (
    EmbeddingRebuildRequest,
    rebuild_namespace_embeddings,
    search_namespace_terms,
)
from ontology_api.services.projection import ProjectionService
from ontology_api.services.search import SearchUnavailableError, rebuild_embeddings, search_terms
from ontology_core.auth.entra import Principal
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.config import AuthMode, Settings
from ontology_core.embedding import EMBEDDING_DIMENSIONS, EmbeddingClient, EmbeddingError
from ontology_core.models import ActorType, NamespaceRole, PlatformRole
from ontology_core.search import MAX_LIMIT, SearchRoute
from ontology_core.sparql.client import SparqlStore

_NS = "srch-ns"
_BASE_IRI = "https://e.example/srch#"
_DEPLOYMENT = "ontology-embedder"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_OWNER = Principal(subject="owner", object_id="owner-oid", actor_type=ActorType.USER)
_MAINTAINER = Principal(subject="maint", object_id="maint-oid", actor_type=ActorType.USER)
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")

#: 承認する版。**4 つの用語**を持つ(1 つは廃止済み)。
_ONTOLOGY = (
    f"@prefix s: <{_BASE_IRI}> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "@prefix dcterms: <http://purl.org/dc/terms/> .\n"
    's:Customer a owl:Class ; rdfs:label "顧客" ; rdfs:comment "サービスを利用する主体" .\n'
    's:customerId a owl:DatatypeProperty ; rdfs:label "顧客ID" .\n'
    's:Order a owl:Class ; rdfs:label "注文" .\n'
    's:Legacy a owl:Class ; rdfs:label "旧顧客" ;\n'
    "    owl:deprecated true ; dcterms:isReplacedBy s:Customer .\n"
)

#: 用語を 1 つも持たない版(`owl:Ontology` だけ)。
_NO_TERMS = (
    f"@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<{_BASE_IRI.rstrip('#')}> a owl:Ontology .\n"
)


def _settings(**kwargs: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        AUTH_MODE=AuthMode.DISABLED,
        MODEL_ENDPOINT="https://example.openai.azure.com/",
        **kwargs,
    )


def _configured(*, model: str = "text-embedding-3-small", **kwargs: Any) -> Settings:
    """埋め込みモデルが設定された状態。"""
    return _settings(EMBEDDING_DEPLOYMENT=_DEPLOYMENT, EMBEDDING_MODEL_NAME=model, **kwargs)


class _NullStore(SparqlStore):
    """射影を行わない代役。**検索は正本から作った埋め込みだけを見る。**"""

    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
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


class _ReadFails(OntologyBlobStore):
    """`get_version` だけを失敗させる代役。

    **`OntologyBlobStore` を継承して 1 メソッドだけ差し替える。**
    全メソッドを自分で書くと、本物に増えたメソッドが黙って抜ける。
    """

    def __init__(self, inner: OntologyBlobStore) -> None:
        self._inner = inner

    async def get_version(self, path: str) -> str:
        raise BlobStoreError("読めませんでした(試験)")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _vector_of(text: str) -> tuple[float, ...]:
    """「顧客」系と「注文」系で別の軸を立てる決定的な埋め込み。

    **ゼロベクトルを作らない**(cosine 距離が未定義になる)ので、
    3 番目の軸に小さな値を常に入れる。
    """
    values = [0.0] * EMBEDDING_DIMENSIONS
    if "顧客" in text or "お客" in text or "Customer" in text:
        values[0] = 1.0
    if "注文" in text or "Order" in text:
        values[1] = 1.0
    values[2] = 0.01
    return tuple(values)


class _FakeEmbeddings:
    """埋め込みを決定的に作る差し替え。

    **実物の Azure OpenAI を呼ばない。** テストが実テナントへ読み取りを
    飛ばす事故は既に 1 度起きている(`setup-app-role.py`)。

    「お客様」と `Customer / 顧客 / ...` を同じ軸に寄せてあるので、
    **3-gram では絶対に当たらない条件で言い換えの経路を検査できる。**
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def client(self) -> EmbeddingClient:
        outer = self

        class _Client(EmbeddingClient):
            async def embed(self, inputs: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
                outer.calls.append(inputs)
                return tuple(_vector_of(text) for text in inputs)

        return _Client(endpoint="https://x.invalid/", deployment=_DEPLOYMENT, api_version="v")


def _broken_client() -> EmbeddingClient:
    """`embed` が必ず失敗するクライアント。"""

    class _Client(EmbeddingClient):
        async def embed(self, inputs: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise EmbeddingError("埋め込みのデプロイの呼び出しに失敗しました(HTTP 503)")

    return _Client(endpoint="https://x.invalid/", deployment=_DEPLOYMENT, api_version="v")


async def _namespace(session: AsyncSession, name: str = _NS) -> None:
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=_BASE_IRI,
        created_by="tester",
    )
    await session.commit()


async def _grant(session: AsyncSession, principal: Principal, role: NamespaceRole) -> None:
    await RoleRepository(session).grant(
        namespace=_NS,
        principal_id=principal.object_id or principal.subject,
        role=role,
        granted_by=_ADMIN.object_id or "admin",
    )
    await session.commit()


async def _approve(
    session: AsyncSession, blob: OntologyBlobStore, *, turtle: str = _ONTOLOGY
) -> None:
    svc = ProjectionService(
        session=session, blob=blob, store=_NullStore(), graph_iri_base="urn:ontology:graph"
    )
    await svc.publish(namespace=_NS, turtle=turtle, actor=_OWNER.object_id, version="1.0.0")
    await svc.submit(namespace=_NS, version="1.0.0", actor=_OWNER.object_id)
    await svc.approve(namespace=_NS, version="1.0.0", actor=_MAINTAINER.object_id)
    await session.commit()


async def _build(
    session: AsyncSession, blob: OntologyBlobStore, fake: _FakeEmbeddings
) -> TermEmbeddingRepository:
    await rebuild_embeddings(
        session, blob=blob, settings=_configured(), namespace=_NS, client=fake.client()
    )
    await session.commit()
    return TermEmbeddingRepository(session)


# ---- 埋め込みの作り直し ----


async def test_承認済み版から埋め込みを作る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**4 つの用語すべてを作る**(廃止済みも含む)。"""
    await _namespace(session)
    await _approve(session, blob_store)

    outcome = await rebuild_embeddings(
        session,
        blob=blob_store,
        settings=_configured(),
        namespace=_NS,
        client=_FakeEmbeddings().client(),
    )
    await session.commit()

    assert outcome.version == "1.0.0"
    assert outcome.model == "text-embedding-3-small"
    assert outcome.term_count == 4
    assert outcome.deprecated_terms == (f"{_BASE_IRI}Legacy",)
    assert outcome.truncated_terms == ()
    assert await TermEmbeddingRepository(session).count(namespace=_NS) == 4


async def test_モデル名が空ならデプロイ名を記録する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**「どのモデルで作ったか」を空にしない。**

    `EMBEDDING_MODEL_NAME` は監査のために持つ設定で、未設定のデプロイは
    実際にありうる。空のまま保存すると、**モデルを変えたときに作り直しが
    必要かを判定できない**(比べる相手が無い)。
    """
    await _namespace(session)
    await _approve(session, blob_store)

    outcome = await rebuild_embeddings(
        session,
        blob=blob_store,
        settings=_configured(model=""),
        namespace=_NS,
        client=_FakeEmbeddings().client(),
    )
    await session.commit()

    assert outcome.model == _DEPLOYMENT
    assert await TermEmbeddingRepository(session).built_state(namespace=_NS) == (
        "1.0.0",
        _DEPLOYMENT,
    )


async def test_承認済み版が無ければ作らない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**提案中の版からは作らない**(四眼原則を通っていない語彙を出さない)。"""
    await _namespace(session)
    svc = ProjectionService(
        session=session, blob=blob_store, store=_NullStore(), graph_iri_base="urn:ontology:graph"
    )
    await svc.publish(namespace=_NS, turtle=_ONTOLOGY, actor=_OWNER.object_id, version="1.0.0")
    await session.commit()

    with pytest.raises(SearchUnavailableError, match="承認済みの版がありません"):
        await rebuild_embeddings(
            session,
            blob=blob_store,
            settings=_configured(),
            namespace=_NS,
            client=_FakeEmbeddings().client(),
        )
    assert await TermEmbeddingRepository(session).count(namespace=_NS) == 0


async def test_モデルが未設定なら作らない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**「0 件作った」とは言わない。**"""
    await _namespace(session)
    await _approve(session, blob_store)

    with pytest.raises(SearchUnavailableError, match="EMBEDDING_DEPLOYMENT"):
        await rebuild_embeddings(
            session,
            blob=blob_store,
            settings=_settings(),
            namespace=_NS,
            client=_FakeEmbeddings().client(),
        )


async def test_正本が読めなければ作らない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**「読めなかった」を「用語が無かった」にしない。**"""
    await _namespace(session)
    await _approve(session, blob_store)

    with pytest.raises(SearchUnavailableError, match="読めませんでした"):
        await rebuild_embeddings(
            session,
            blob=_ReadFails(blob_store),
            settings=_configured(),
            namespace=_NS,
            client=_FakeEmbeddings().client(),
        )


async def test_用語が_1_件も無い版では作らない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**空の索引を作って「検索できる」と見せない。**"""
    await _namespace(session)
    await _approve(session, blob_store, turtle=_NO_TERMS)

    with pytest.raises(SearchUnavailableError, match="用語がありません"):
        await rebuild_embeddings(
            session,
            blob=blob_store,
            settings=_configured(),
            namespace=_NS,
            client=_FakeEmbeddings().client(),
        )


async def test_埋め込みの呼び出しが失敗したら索引を残さない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**部分的な索引を残さない。**

    途中まで書いて成功を返すと、**検索が「その用語は無い」と言う**。
    """
    await _namespace(session)
    await _approve(session, blob_store)

    with pytest.raises(SearchUnavailableError, match="埋め込みの生成に失敗"):
        await rebuild_embeddings(
            session,
            blob=blob_store,
            settings=_configured(),
            namespace=_NS,
            client=_broken_client(),
        )
    assert await TermEmbeddingRepository(session).count(namespace=_NS) == 0


async def test_作り直しても同じ用語が二重にならない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**行を丸ごと入れ替える**(差分更新にしない)。

    モデルを変えて作り直したときに、**どのモデルで作ったかが更新される**
    ことも併せて固定する。
    """
    await _namespace(session)
    await _approve(session, blob_store)
    fake = _FakeEmbeddings()
    repository = await _build(session, blob_store, fake)
    assert await repository.count(namespace=_NS) == 4

    await rebuild_embeddings(
        session,
        blob=blob_store,
        settings=_configured(model="text-embedding-3-large"),
        namespace=_NS,
        client=fake.client(),
    )
    await session.commit()

    assert await repository.count(namespace=_NS) == 4
    assert await repository.built_state(namespace=_NS) == ("1.0.0", "text-embedding-3-large")


async def test_上限で分割して呼ぶ() -> None:
    """**1 回の要求が大きくなりすぎないようにする。**

    `EmbeddingClient.embed` は上限超えを断るので、分割はサービス側の責任
    である。**分割の順序が保たれる**ことも併せて固定する。
    """
    from ontology_api.services.search import _embed_all

    fake = _FakeEmbeddings()
    inputs = tuple(f"顧客{index}" if index % 2 == 0 else f"注文{index}" for index in range(150))

    vectors = await _embed_all(fake.client(), inputs)

    assert len(vectors) == 150
    assert [len(call) for call in fake.calls] == [64, 64, 22]
    # **順序が保たれている。** 偶数番は「顧客」の軸、奇数番は「注文」の軸。
    assert vectors[0][0] == 1.0 and vectors[0][1] == 0.0
    assert vectors[1][0] == 0.0 and vectors[1][1] == 1.0


# ---- 検索 ----


async def test_表記の部分一致で当たる(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    """3-gram の経路。「顧客ID」→ `customerId`。"""
    await _namespace(session)
    await _approve(session, blob_store)
    fake = _FakeEmbeddings()
    await _build(session, blob_store, fake)

    outcome = await search_terms(
        session,
        settings=_configured(),
        namespace=_NS,
        query="顧客ID",
        limit=10,
        client=fake.client(),
    )

    top = outcome.hits[0]
    assert top.term_iri == f"{_BASE_IRI}customerId"
    assert top.trigram_rank == 1
    assert outcome.vector_available is True
    assert outcome.conclusive is True


async def test_言い換えはベクトルの経路だけで当たる(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**これが 2 つの経路を持つ理由である。**

    「お客様」は `Customer / 顧客 / サービスを利用する主体` と 3-gram を
    1 つも共有しない(実測で類似度 0.000)。それでも `Customer` に当たる
    のはベクトルの経路があるからである。
    """
    await _namespace(session)
    await _approve(session, blob_store)
    fake = _FakeEmbeddings()
    await _build(session, blob_store, fake)

    outcome = await search_terms(
        session,
        settings=_configured(),
        namespace=_NS,
        query="お客様",
        limit=10,
        client=fake.client(),
    )

    routes = {hit.term_iri: hit.route for hit in outcome.hits}
    assert routes[f"{_BASE_IRI}Customer"] is SearchRoute.VECTOR
    # **3-gram では 1 件も当たっていない。**
    assert all(hit.trigram_rank is None for hit in outcome.hits)


async def test_モデルが未設定でも_3_gram_で検索できる(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**「使えない」を「該当なし」にしない**(決定8)。"""
    await _namespace(session)
    await _approve(session, blob_store)
    fake = _FakeEmbeddings()
    await _build(session, blob_store, fake)

    outcome = await search_terms(
        session,
        settings=_settings(),
        namespace=_NS,
        query="顧客ID",
        limit=10,
        client=fake.client(),
    )

    assert outcome.hits, "3-gram の経路だけでも結果は返る"
    assert outcome.vector_available is False
    assert "EMBEDDING_DEPLOYMENT" in outcome.vector_note
    assert outcome.conclusive is False


async def test_埋め込みを作っていない名前空間では理由を返す(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`0` は「作っていない」であって「該当なし」ではない。**

    ここで空を返して黙ると、**運用者は作成漏れに気づけない**。
    """
    await _namespace(session)
    await _approve(session, blob_store)

    outcome = await search_terms(
        session,
        settings=_configured(),
        namespace=_NS,
        query="顧客",
        limit=10,
        client=_FakeEmbeddings().client(),
    )

    assert outcome.hits == ()
    assert outcome.embedded_term_count == 0
    assert outcome.vector_available is False
    assert "まだ作られていません" in outcome.vector_note


async def test_モデルが落ちても検索は成立する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**ベクトルの経路の失敗を例外にしない**(不変条件3 と同じ向き)。

    3-gram の結果は正しいので、返せるものを返して**落ちたことを見せる**。
    """
    await _namespace(session)
    await _approve(session, blob_store)
    await _build(session, blob_store, _FakeEmbeddings())

    outcome = await search_terms(
        session,
        settings=_configured(),
        namespace=_NS,
        query="顧客ID",
        limit=10,
        client=_broken_client(),
    )

    assert outcome.hits, "3-gram の結果は返る"
    assert outcome.vector_available is False
    assert "埋め込みに失敗" in outcome.vector_note
    assert outcome.embedded_term_count == 4


async def test_失敗の理由に問いを載せない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**問いは利用者の入力である。** ログや応答に写して回さない。"""
    await _namespace(session)
    await _approve(session, blob_store)
    await _build(session, blob_store, _FakeEmbeddings())

    outcome = await search_terms(
        session,
        settings=_configured(),
        namespace=_NS,
        query="機密の問い合わせ文",
        limit=10,
        client=_broken_client(),
    )

    assert "機密の問い合わせ文" not in outcome.vector_note


async def test_廃止済みの用語も返して警告する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**除外しない**(ADR-0017 決定3)。見つけられないと理由を答えられない。"""
    await _namespace(session)
    await _approve(session, blob_store)
    fake = _FakeEmbeddings()
    await _build(session, blob_store, fake)

    outcome = await search_terms(
        session,
        settings=_configured(),
        namespace=_NS,
        query="旧顧客",
        limit=10,
        client=fake.client(),
    )

    assert f"{_BASE_IRI}Legacy" in {hit.term_iri for hit in outcome.hits}
    assert f"{_BASE_IRI}Legacy" in outcome.deprecated_hits


async def test_空の問いで全件を返さない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**「検索した結果これが全部である」と読めてしまう。**"""
    await _namespace(session)
    await _approve(session, blob_store)
    await _build(session, blob_store, _FakeEmbeddings())

    for query in ("", "   ", "\t\n"):
        with pytest.raises(SearchUnavailableError, match="問いが空です"):
            await search_terms(
                session,
                settings=_configured(),
                namespace=_NS,
                query=query,
                limit=10,
                client=_FakeEmbeddings().client(),
            )


# ---- ルータ(権限・監査・退役) ----


async def test_検索は_data_analyst_で通る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _namespace(session)
    await _approve(session, blob_store)
    fake = _FakeEmbeddings()
    await _build(session, blob_store, fake)
    await _grant(session, _ANALYST, NamespaceRole.DATA_ANALYST)

    view = await search_namespace_terms(
        namespace=_NS,
        principal=_ANALYST,
        session=session,
        settings=_configured(),
        client=fake.client(),
        q="顧客ID",
        limit=5,
    )

    assert view.query == "顧客ID"
    assert view.limit == 5
    assert view.hits[0].term_iri == f"{_BASE_IRI}customerId"
    # **経路ごとの件数を返す**(なぜその結果になったかを読むため)。
    assert set(view.routes) == {"vector", "trigram", "both"}


async def test_権限が無い主体は検索できない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**既定は拒否**(不変条件11)。"""
    await _namespace(session)
    await _approve(session, blob_store)
    await _build(session, blob_store, _FakeEmbeddings())

    with pytest.raises(HTTPException) as caught:
        await search_namespace_terms(
            namespace=_NS,
            principal=_STRANGER,
            session=session,
            settings=_configured(),
            client=_FakeEmbeddings().client(),
            q="顧客",
            limit=5,
        )
    assert caught.value.status_code == 403


async def test_存在しない名前空間は_404(session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as caught:
        await search_namespace_terms(
            namespace="missing-ns",
            principal=_ADMIN,
            session=session,
            settings=_configured(),
            client=_FakeEmbeddings().client(),
            q="顧客",
            limit=5,
        )
    assert caught.value.status_code == 404


async def test_名前空間名が不正なら_400(session: AsyncSession) -> None:
    """**不変条件5。** 名前空間名はセキュリティ境界である。"""
    with pytest.raises(HTTPException) as caught:
        await search_namespace_terms(
            namespace="../etc",
            principal=_ADMIN,
            session=session,
            settings=_configured(),
            client=_FakeEmbeddings().client(),
            q="顧客",
            limit=5,
        )
    assert caught.value.status_code == 400


async def test_件数の既定値が素の値である(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`Query(...)` を既定値の位置に書いていない**ことを固定する。

    書くと、ハンドラを直接呼ぶテストで `Query` オブジェクトが値として
    流れ込む(このリポジトリのルータのテストは直接呼ぶので必ず踏む)。
    """
    await _namespace(session)
    await _approve(session, blob_store)
    fake = _FakeEmbeddings()
    await _build(session, blob_store, fake)
    await _grant(session, _ANALYST, NamespaceRole.DATA_ANALYST)

    view = await search_namespace_terms(
        namespace=_NS,
        principal=_ANALYST,
        session=session,
        settings=_configured(),
        client=fake.client(),
        q="顧客ID",
    )
    assert view.limit == 10


async def test_上限を超える件数は丸めて見せる(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**丸めたことが応答に出る。** 要求値をそのまま返すと気づけない。"""
    await _namespace(session)
    await _approve(session, blob_store)
    fake = _FakeEmbeddings()
    await _build(session, blob_store, fake)
    await _grant(session, _ANALYST, NamespaceRole.DATA_ANALYST)

    view = await search_namespace_terms(
        namespace=_NS,
        principal=_ANALYST,
        session=session,
        settings=_configured(),
        client=fake.client(),
        q="顧客ID",
        limit=MAX_LIMIT + 100,
    )
    assert view.limit == MAX_LIMIT


async def test_作り直しは_maintainer_が必要(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**費用が発生する行為である。** `data-analyst` には開けない。"""
    await _namespace(session)
    await _approve(session, blob_store)
    await _grant(session, _ANALYST, NamespaceRole.DATA_ANALYST)

    with pytest.raises(HTTPException) as caught:
        await rebuild_namespace_embeddings(
            namespace=_NS,
            payload=EmbeddingRebuildRequest(reason="試す"),
            principal=_ANALYST,
            session=session,
            settings=_configured(),
            blob=blob_store,
            client=_FakeEmbeddings().client(),
        )
    assert caught.value.status_code == 403


async def test_作り直しが監査に残る(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    """**誰が・いつ・なぜ作り直したか。**

    モデルを変えたのか版を承認したのかが分からないと、作り直しの判断が
    再現しない。
    """
    await _namespace(session)
    await _approve(session, blob_store)
    await _grant(session, _MAINTAINER, NamespaceRole.MAINTAINER)

    view = await rebuild_namespace_embeddings(
        namespace=_NS,
        payload=EmbeddingRebuildRequest(reason="モデルを変えたため"),
        principal=_MAINTAINER,
        session=session,
        settings=_configured(),
        blob=blob_store,
        client=_FakeEmbeddings().client(),
    )

    assert view.term_count == 4
    page = await AuditRepository(session).query(namespace=_NS, action="embeddings-rebuilt")
    rebuilt = list(page.events)
    assert len(rebuilt) == 1
    assert rebuilt[0].actor == _MAINTAINER.object_id
    assert "モデルを変えたため" in (rebuilt[0].reason or "")
    # **何件・どのモデルで作ったかを残す。**
    assert "用語 4 件" in (rebuilt[0].reason or "")
    assert "text-embedding-3-small" in (rebuilt[0].reason or "")


async def test_作り直せないときは_404_で断る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**「0 件作った」と返さない。**"""
    await _namespace(session)
    await _grant(session, _MAINTAINER, NamespaceRole.MAINTAINER)

    with pytest.raises(HTTPException) as caught:
        await rebuild_namespace_embeddings(
            namespace=_NS,
            payload=EmbeddingRebuildRequest(reason="試す"),
            principal=_MAINTAINER,
            session=session,
            settings=_configured(),
            blob=blob_store,
            client=_FakeEmbeddings().client(),
        )
    assert caught.value.status_code == 404
    assert "承認済みの版がありません" in str(caught.value.detail)


async def test_退役した名前空間では作り直さない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**費用だけが発生する**(ADR-0032 決定5 と同じ形)。"""
    await _namespace(session)
    await _approve(session, blob_store)
    await _grant(session, _MAINTAINER, NamespaceRole.MAINTAINER)
    await NamespaceRepository(session).set_retired(
        _NS, actor=_ADMIN.object_id or "admin", reason="試験", at=datetime.now(UTC)
    )
    await session.commit()

    with pytest.raises(HTTPException) as caught:
        await rebuild_namespace_embeddings(
            namespace=_NS,
            payload=EmbeddingRebuildRequest(reason="試す"),
            principal=_MAINTAINER,
            session=session,
            settings=_configured(),
            blob=blob_store,
            client=_FakeEmbeddings().client(),
        )
    assert caught.value.status_code == 409
