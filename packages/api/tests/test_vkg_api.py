"""R2RML マッピングの管理と仮想グラフの照会(ADR-0046、`P3-01`)。

**このファイルは実物の PostgreSQL をスキャンしてから検査する。**
`test_scan_api.py` が「実物のカタログに対してスキーマが読める」ことを
確かめたのと同じ理由である — **マッピングの検査はスキャンの観測に
依存する**ので、フェイクのカタログに対して通しても意味がない。
題材は**このシステム自身の `public.namespaces` 表**にした。

実物の Ontop に対する検証は `containers/ontop/ontop-check.test.sh` が
行う(docker が必要)。

ここで固定するのは 6 つである。

1. **観測していない関係を読むマッピングを拒否する**(決定5)。
   **Ontop は不正なマッピングでは起動しない**(実測)ので、通すと
   仮想グラフが丸ごと落ちる
2. **スキャンが一度も成功していないソースでは拒否する。**
   「検査できなかった」を「検査した」と書かない
3. **名前空間の外に出る主語を拒否する**(決定5、不変条件5)
4. **承認済み版に無い用語を拒否する**(決定6)。廃止済みも拒否する
5. **改訂は不変で `reason` が必須。監査に照合した版が残る**(決定2・12)
6. **マッピングが無い / エンドポイントが未設定なら、空を返さず断る**
   (決定11)
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.routers.scan import ScanSourceRegister, register_scan_source, run_scan
from ontology_api.routers.vkg import (
    VkgMappingRevise,
    VkgQueryRequest,
    get_vkg_mapping,
    get_vkg_mapping_turtle,
    list_vkg_mapping_revisions,
    query_virtual_graph,
    revise_vkg_mapping,
)
from ontology_api.services.projection import ProjectionService
from ontology_core.auth.entra import Principal
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.config import AuthMode, Settings
from ontology_core.models import ActorType, NamespaceRole, PlatformRole
from ontology_core.sparql.client import SparqlStore

_NS = "vkg-ns"
_BASE_IRI = "https://e.example/vkg#"
_DATA_PREFIX = "https://e.example/vkg/data/"
_OTHER_NS = "vkg-other"
_OTHER_IRI = "https://e.example/other#"
_SOURCE = "self"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_OWNER = Principal(subject="owner", object_id="owner-oid", actor_type=ActorType.USER)
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")

#: ローカルの PostgreSQL。**自分自身をスキャンする**(`test_scan_api.py` と同じ)。
_HOST = os.environ.get("POSTGRES_HOST", "localhost")
_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
_DATABASE = os.environ.get("POSTGRES_DATABASE", "ontology")
_USER = os.environ.get("POSTGRES_USER", "ontology")
_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "localdev")

#: 承認する版。`public.namespaces` 表を写す語彙だけを持つ。
_ONTOLOGY = (
    f"@prefix v: <{_BASE_IRI}> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "@prefix dcterms: <http://purl.org/dc/terms/> .\n"
    'v:Namespace a owl:Class ; rdfs:label "名前空間" .\n'
    'v:displayName a owl:DatatypeProperty ; rdfs:label "表示名" .\n'
    'v:baseIri a owl:DatatypeProperty ; rdfs:label "基底 IRI" .\n'
    'v:Legacy a owl:Class ; rdfs:label "旧" ;\n'
    "    owl:deprecated true ; dcterms:isReplacedBy v:Namespace .\n"
)

_PREFIXES = (
    "@prefix rr: <http://www.w3.org/ns/r2rml#> .\n"
    f"@prefix v: <{_BASE_IRI}> .\n"
    f"@prefix o: <{_OTHER_IRI}> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
)


def _mapping(
    *,
    table: str = '"public"."namespaces"',
    subject: str = _DATA_PREFIX + "namespace/{name}",
    cls: str = "v:Namespace",
    predicate: str = "v:displayName",
) -> str:
    return (
        _PREFIXES
        + "<#NamespaceMap> a rr:TriplesMap ;\n"
        + f"  rr:logicalTable [ rr:tableName {_quote(table)} ] ;\n"
        + "  rr:subjectMap [\n"
        + f'    rr:template "{subject}" ;\n'
        + f"    rr:class {cls}\n"
        + "  ] ;\n"
        + "  rr:predicateObjectMap [\n"
        + f"    rr:predicate {predicate} ;\n"
        + '    rr:objectMap [ rr:column "display_name" ]\n'
        + "  ] .\n"
    )


def _quote(table: str) -> str:
    """Turtle のリテラルとして表名を書く(内側の `"` をエスケープする)。"""
    return '"' + table.replace('"', '\\"') + '"'


def _settings(**kwargs: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        AUTH_MODE=AuthMode.DISABLED,
        SCAN_ALLOWED_HOSTS=_HOST,
        **kwargs,
    )


async def _local_password(source: Any, settings: Settings) -> str | None:
    """ローカルの PostgreSQL のパスワードを返す差し替え(`test_scan_api.py` と同じ)。"""
    return _PASSWORD


class _NullStore(SparqlStore):
    """射影を行わない代役。**用語の実在は正本の TTL で判定する。**"""

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
    """`get_version` だけを失敗させる代役(変異テストで見つかった穴の検証用)。

    **`OntologyBlobStore` を継承して 1 メソッドだけ差し替える。**
    全メソッドを自分で書くと、本物に増えたメソッドが黙って抜ける。
    """

    def __init__(self, real: OntologyBlobStore) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    async def get_version(self, blob_path: str) -> str:
        raise BlobStoreError("Blob を読めませんでした(検証用)")


class _ReturnsBroken(_ReadFails):
    """Turtle として解析できない本文を返す代役。"""

    async def get_version(self, blob_path: str) -> str:
        return "@prefix v: <https://e.example/vkg#> . v:Broken a"


async def _setup(session: AsyncSession) -> None:
    repo = NamespaceRepository(session)
    roles = RoleRepository(session)
    for name, base_iri in ((_NS, _BASE_IRI), (_OTHER_NS, _OTHER_IRI)):
        await repo.create(
            name=name,
            display_name=name,
            description="",
            base_iri=base_iri,
            created_by=_ADMIN.object_id,
            require_two_person_approval=False,
        )
    for principal, role in (
        (_OWNER, NamespaceRole.OWNER),
        (_ANALYST, NamespaceRole.DATA_ANALYST),
    ):
        await roles.grant(
            namespace=_NS,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await session.commit()


async def _register_and_scan(session: AsyncSession, *, scan: bool = True) -> None:
    """ソースを登録し、**実物の PostgreSQL をスキャンする。**"""
    await register_scan_source(
        namespace=_NS,
        payload=ScanSourceRegister(
            name=_SOURCE,
            driver="postgresql",
            host=_HOST,
            port=_PORT,
            database=_DATABASE,
            username=_USER,
            auth_mode="entra",
        ),
        principal=_OWNER,
        session=session,
        settings=_settings(),
    )
    if scan:
        await run_scan(
            namespace=_NS,
            name=_SOURCE,
            principal=_OWNER,
            session=session,
            settings=_settings(),
            secret_resolver=_local_password,
        )


async def _approve(
    session: AsyncSession, blob: OntologyBlobStore, *, version: str = "1.0.0"
) -> None:
    svc = ProjectionService(
        session=session, blob=blob, store=_NullStore(), graph_iri_base="urn:ontology:graph"
    )
    await svc.publish(namespace=_NS, turtle=_ONTOLOGY, actor=_OWNER.object_id, version=version)
    await svc.submit(namespace=_NS, version=version, actor=_OWNER.object_id)
    await svc.approve(namespace=_NS, version=version, actor=_OWNER.object_id)
    await session.commit()


async def _revise(
    session: AsyncSession,
    blob: OntologyBlobStore,
    *,
    content: str | None = None,
    principal: Principal = _OWNER,
    reason: str = "初回の登録",
) -> Any:
    return await revise_vkg_mapping(
        namespace=_NS,
        source=_SOURCE,
        payload=VkgMappingRevise(
            content=content if content is not None else _mapping(), reason=reason
        ),
        principal=principal,
        session=session,
        blob=blob,
    )


# ------------------------------------- 観測していない関係(決定5)


@pytest.mark.integration
async def test_観測していない関係を読むマッピングを拒否する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**Ontop は不正なマッピングでは起動しない**(実測)。

    通すと仮想グラフが丸ごと落ちる。しかも Ontop の起動失敗の本文は
    **接続ユーザから見える全関係を列挙する**ので、そのエラーを見せる
    わけにもいかない。**入口で捕まえるのが唯一の出口である。**
    """
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await _revise(session, blob_store, content=_mapping(table='"public"."nope"'))
    assert caught.value.status_code == 422
    assert "観測していない関係" in str(caught.value.detail)
    assert "public.nope" in str(caught.value.detail)


@pytest.mark.integration
async def test_実物のスキャンで観測した関係なら通る(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**実物のカタログに対して通ることを確かめる。**

    フェイクのカタログでは、引用の外し方や `schema.table` の組み立てが
    実物と合っているかを確かめられない。
    """
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)

    created = await _revise(session, blob_store)
    assert created.revision == 1
    assert created.tables == ("public.namespaces",)
    assert created.triples_map_count == 1
    assert created.validated_against_version == "1.0.0"


@pytest.mark.integration
async def test_スキャンが一度も成功していないソースでは拒否する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**「検査できなかった」を「検査した」と書かない。**

    観測が無い状態で通すと、「観測済みの表に限る」という約束が嘘になる。
    """
    await _setup(session)
    await _register_and_scan(session, scan=False)
    await _approve(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await _revise(session, blob_store)
    assert caught.value.status_code == 422
    assert "成功したスキャンがありません" in str(caught.value.detail)


# ------------------------------------- 名前空間の境界(決定5、不変条件5)


@pytest.mark.integration
async def test_名前空間の外に出る主語を拒否する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**不変条件5。** 他の名前空間の IRI を主語にできると、相手が宣言して
    いない事実を相手の名前で作れる。
    """
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await _revise(
            session,
            blob_store,
            content=_mapping(subject="https://e.example/other/data/namespace/{name}"),
        )
    assert caught.value.status_code == 422
    assert _DATA_PREFIX in str(caught.value.detail)


@pytest.mark.integration
async def test_用語の空間を主語にするマッピングも拒否する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """`base_iri` の直下(用語の空間)はデータの接頭辞ではない。

    **インスタンスと用語を同じ空間に置くと、用語の一覧に実データが混ざる。**
    """
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await _revise(session, blob_store, content=_mapping(subject=_BASE_IRI + "{name}"))
    assert caught.value.status_code == 422


@pytest.mark.integration
async def test_他の名前空間の用語をクラスに使うマッピングを拒否する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await _revise(session, blob_store, content=_mapping(cls="o:Thing"))
    assert caught.value.status_code == 422
    assert "他の名前空間の用語" in str(caught.value.detail)


@pytest.mark.integration
async def test_外部語彙の述語は検査しない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """`rdfs:label` への写像が正当な主用途である(ADR-0023 決定6 と同じ判断)。

    **外部語彙まで実在を要求すると、実用的なマッピングが 1 つも書けない。**
    """
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)

    created = await _revise(session, blob_store, content=_mapping(predicate="rdfs:label"))
    assert created.revision == 1


# ------------------------------------- 承認済み版との突き合わせ(決定6・12)


@pytest.mark.integration
async def test_承認済み版に無い用語を拒否する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**実データを流しても、その用語で問い合わせるエージェントには見えない。**"""
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await _revise(session, blob_store, content=_mapping(predicate="v:neverDefined"))
    assert caught.value.status_code == 422
    assert "存在しない用語" in str(caught.value.detail)


@pytest.mark.integration
async def test_廃止された用語を拒否し後継を示す(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**廃止した用語に新しく実データを流すと、廃止の宣言が意味を失う**(決定6)。

    後継を示すのは、直す先が分からない拒否は直せないからである
    (ADR-0030 の `successor` と同じ理由)。
    """
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await _revise(session, blob_store, content=_mapping(cls="v:Legacy"))
    assert caught.value.status_code == 422
    detail = str(caught.value.detail)
    assert "廃止された用語" in detail
    assert _BASE_IRI + "Namespace" in detail


@pytest.mark.integration
async def test_承認済み版が無ければ照合せずに通し_None_を記録する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**Scan が Model より先に来る製品である**(決定12)。

    版がまだ無い段階のマッピングは異常ではない。**`None` は「照合先が
    無かった」であって「検査に通った」ではない。**
    """
    await _setup(session)
    await _register_and_scan(session)

    created = await _revise(session, blob_store)
    assert created.validated_against_version is None


@pytest.mark.integration
async def test_正本が読めなければ拒否する_実在するとは言わない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**「読めなかった」を「実在する」にしない。**

    **このテストは変異テストで見つかった穴である。** 例外の分岐に
    「通す」を入れても、他のどのテストも落ちなかった。落ちなければ、
    **Blob が読めない名前空間では用語の検査が黙って無効になる**。

    `_ReadFails` は `get_version` だけを失敗させる代役である。
    """
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await _revise(session, _ReadFails(blob_store))
    assert caught.value.status_code == 422
    detail = str(caught.value.detail)
    assert "確かめられませんでした" in detail
    # **版の名前を出す。** どの版と突き合わせようとしたのかが分からないと直せない。
    assert "1.0.0" in detail


@pytest.mark.integration
async def test_正本が壊れていても拒否する(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """Turtle として解析できないときも「実在する」とは言わない。

    **`BlobStoreError` と `DeprecationCheckError` は別の例外である。**
    片方だけ捕まえる実装でも上のテストは通るので、両方を固定する。
    """
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await _revise(session, _ReturnsBroken(blob_store))
    assert caught.value.status_code == 422
    assert "確かめられませんでした" in str(caught.value.detail)


@pytest.mark.integration
async def test_退役した名前空間ではマッピングを改訂できない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**版を作れない名前空間の実データへの入口を書き換える意味が無い**
    (ADR-0032 決定5 と同じ形)。

    **このテストも変異テストで見つかった穴である。** 退役の検査を
    丸ごと外しても、他のどのテストも落ちなかった。
    """
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)
    await _revise(session, blob_store)

    await NamespaceRepository(session).set_retired(
        _NS, actor=_ADMIN.object_id, reason="検証のため", at=datetime.now(UTC)
    )
    await session.commit()

    with pytest.raises(HTTPException) as caught:
        await _revise(session, blob_store, reason="退役後の改訂")
    assert caught.value.status_code == 409


# ------------------------------------- 改訂と監査(決定2・12)


@pytest.mark.integration
async def test_改訂は積み上がり書き換わらない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)

    first = await _revise(session, blob_store, reason="初回")
    second = await _revise(
        session, blob_store, content=_mapping(predicate="v:baseIri"), reason="基底 IRI も写す"
    )
    assert (first.revision, second.revision) == (1, 2)

    revisions = await list_vkg_mapping_revisions(_NS, _SOURCE, _ANALYST, session)
    assert [r.revision for r in revisions] == [2, 1]
    assert [r.reason for r in revisions] == ["基底 IRI も写す", "初回"]

    active = await get_vkg_mapping(_NS, _SOURCE, _ANALYST, session)
    assert active.revision == 2
    assert "baseIri" in active.content


@pytest.mark.integration
async def test_監査に照合した版が残る(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    """**後から「そのとき何と突き合わせたのか」を追えるようにする**(決定12)。"""
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)
    await _revise(session, blob_store, reason="初回")

    recorded = await AuditRepository(session).list_for_subject(_NS, f"{_NS}#vkg/{_SOURCE}@1")
    assert len(recorded) == 1
    assert recorded[0].action == "vkg-mapping-revised"
    assert "照合した版: 1.0.0" in recorded[0].reason
    assert "public.namespaces" in recorded[0].reason
    assert recorded[0].actor_type is ActorType.USER


@pytest.mark.integration
async def test_承認済み版が無いときの監査は照合先が無かったと書く(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**`NULL` を「検査した」と読ませない。** 監査の本文でもそう書く。"""
    await _setup(session)
    await _register_and_scan(session)
    await _revise(session, blob_store)

    recorded = await AuditRepository(session).list_for_subject(_NS, f"{_NS}#vkg/{_SOURCE}@1")
    assert "照合した版: (承認済み版なし)" in recorded[0].reason


def test_reason_は必須である() -> None:
    """**マッピングは実データへの入口の定義である。**

    入口が変わった理由が残らないのは監査として成立しない。
    """
    with pytest.raises(ValueError):
        VkgMappingRevise(content=_mapping(), reason="")


def test_rr_sqlQuery_は本文の説明に明記されている() -> None:
    """**受け付けないことを API の説明に書く。**

    書かないと、利用者は 422 を「バグ」として報告することになる。
    """
    description = VkgMappingRevise.model_fields["content"].description or ""
    assert "rr:sqlQuery" in description


# ------------------------------------- 権限


@pytest.mark.integration
async def test_maintainer_では登録できず_owner_が必要(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**ソースの登録と同じ重さの行為である。**

    `maintainer` に緩めると、ソースを登録できない主体が、登録済みの
    ソースから何を読むかを決められてしまう。
    """
    await _setup(session)
    await _register_and_scan(session)
    await _approve(session, blob_store)

    await RoleRepository(session).grant(
        namespace=_NS,
        principal_id=_STRANGER.object_id,
        role=NamespaceRole.MAINTAINER,
        granted_by=_ADMIN.object_id,
    )
    await session.commit()

    with pytest.raises(HTTPException) as caught:
        await _revise(session, blob_store, principal=_STRANGER)
    assert caught.value.status_code == 403


@pytest.mark.integration
async def test_権限の無い主体は読めない(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    await _register_and_scan(session)
    await _revise(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await get_vkg_mapping(_NS, _SOURCE, _STRANGER, session)
    assert caught.value.status_code == 403


# ------------------------------------- 返すものが無いときに空を返さない(決定11)


@pytest.mark.integration
async def test_マッピングが無ければ_404(session: AsyncSession) -> None:
    """**空の本文を返さない。** 空の R2RML は「何も読まない有効なマッピング」と
    区別できない。
    """
    await _setup(session)
    await _register_and_scan(session, scan=False)

    with pytest.raises(HTTPException) as caught:
        await get_vkg_mapping(_NS, _SOURCE, _ANALYST, session)
    assert caught.value.status_code == 404


@pytest.mark.integration
async def test_存在しないソースは_404(session: AsyncSession) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as caught:
        await get_vkg_mapping(_NS, "no-such-source", _ANALYST, session)
    assert caught.value.status_code == 404


@pytest.mark.integration
async def test_マッピングが無いソースへの照会は_404(session: AsyncSession) -> None:
    """**空の結果を返さない**(決定11)。

    マッピングを登録していないソースは「読める実データが無い」のではなく
    「入口を定義していない」である。
    """
    await _setup(session)
    await _register_and_scan(session, scan=False)

    with pytest.raises(HTTPException) as caught:
        await query_virtual_graph(
            namespace=_NS,
            source=_SOURCE,
            payload=VkgQueryRequest(query="SELECT * WHERE { ?s ?p ?o }"),
            principal=_ANALYST,
            session=session,
            settings=_settings(VKG_ENDPOINT_TEMPLATE="http://ontop:8080/sparql"),
            response=Response(),
        )
    assert caught.value.status_code == 404


@pytest.mark.integration
async def test_エンドポイントが未設定なら_503(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    """**空の結果を返さない**(決定11)。

    空を返すと「該当する行が無い」と区別できず、**設定漏れがデータの
    不在として通る**。
    """
    await _setup(session)
    await _register_and_scan(session)
    await _revise(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await query_virtual_graph(
            namespace=_NS,
            source=_SOURCE,
            payload=VkgQueryRequest(query="SELECT * WHERE { ?s ?p ?o }"),
            principal=_ANALYST,
            session=session,
            settings=_settings(),
            response=Response(),
        )
    assert caught.value.status_code == 503
    assert "VKG_ENDPOINT_TEMPLATE" in str(caught.value.detail)


@pytest.mark.integration
async def test_更新のクエリは_400(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    """**多層防御である。** 実測で Ontop 自身も 415 で拒否するが、
    こちらでも弾く。
    """
    await _setup(session)
    await _register_and_scan(session)
    await _revise(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await query_virtual_graph(
            namespace=_NS,
            source=_SOURCE,
            payload=VkgQueryRequest(query="INSERT DATA { <urn:a> <urn:b> <urn:c> }"),
            principal=_ANALYST,
            session=session,
            settings=_settings(VKG_ENDPOINT_TEMPLATE="http://ontop:8080/sparql"),
            response=Response(),
        )
    assert caught.value.status_code == 400


@pytest.mark.integration
async def test_SERVICE_句のクエリは_400(
    session: AsyncSession, blob_store: OntologyBlobStore
) -> None:
    await _setup(session)
    await _register_and_scan(session)
    await _revise(session, blob_store)

    with pytest.raises(HTTPException) as caught:
        await query_virtual_graph(
            namespace=_NS,
            source=_SOURCE,
            payload=VkgQueryRequest(
                query="SELECT * WHERE { SERVICE <http://169.254.169.254/> { ?s ?p ?o } }"
            ),
            principal=_ANALYST,
            session=session,
            settings=_settings(VKG_ENDPOINT_TEMPLATE="http://ontop:8080/sparql"),
            response=Response(),
        )
    assert caught.value.status_code == 400


# ------------------------------------- Turtle で取り出す口(`P3-07` が使う)


@pytest.mark.integration
async def test_Turtle_で取り出せる(session: AsyncSession, blob_store: OntologyBlobStore) -> None:
    """**Ontop は `--mapping <ファイル>` しか受け付けない。**

    JSON に包むと取り出す側が毎回ほどくことになり、`jq -r` を挟んだ経路で
    **改行やエスケープが壊れる**。
    """
    await _setup(session)
    await _register_and_scan(session)
    await _revise(session, blob_store)

    response = await get_vkg_mapping_turtle(_NS, _SOURCE, _ANALYST, session)
    assert response.media_type is not None
    assert response.media_type.startswith("text/turtle")
    assert bytes(response.body).decode("utf-8") == _mapping()
    # **ヘッダは返す `Response` に載せる**(注入側に載せると消える)。
    assert response.headers["X-Ontology-Vkg-Revision"] == "1"
