"""候補は `draft` にしか入らない(ADR-0043 決定5、`P2A-02`)。

**このファイルでいちばん重要なのは `test_候補は_draft_にしか入らない` である。**

この製品の前提は「**LLM の出力が人間の承認を経ずに正本へ入ることはない**」で
ある。これまでその前提は**LLM を呼ぶ経路が 1 つも無かったから**成り立って
いた。ここで初めてコードで強制する。

だから確かめるのは 2 つある。

1. 候補の状態が `draft` である
2. **Fuseki に 1 つもグラフが増えていない**(= エージェントから見えない)

2 のほうが本質である。状態の文字列は変えられるが、**射影されていないことは
射影先を見ないと分からない**。

ここで固定するのはほかに 3 つ。

3. **`owner` が必要**(決定7)。外向きのデータの流れと課金を伴う
4. **多すぎたら 413 で断る。切り詰めない**(決定6)
5. **モデルの出自が監査に残る**(決定10)
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.scan import ScanRepository
from ontology_api.repositories.versions import AuditRepository, VersionRepository
from ontology_api.routers.proposals import ProposalRequest, propose_ontology
from ontology_api.routers.scan import run_scan
from ontology_api.services import proposal as proposal_service
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import AuthMode, Settings
from ontology_core.db import ScanSourceRow
from ontology_core.models import ActorType, NamespaceRole, OntologyVersionStatus, PlatformRole
from ontology_core.sparql.client import SparqlStore

_NS = "proposal-ns"
_BASE = "https://e.example/proposal#"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_OWNER = Principal(subject="owner", object_id="owner-oid", actor_type=ActorType.USER)
_STEWARD = Principal(subject="steward", object_id="steward-oid", actor_type=ActorType.USER)

_HOST = os.environ.get("POSTGRES_HOST", "localhost")
_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
_DATABASE = os.environ.get("POSTGRES_DATABASE", "ontology")
_USER = os.environ.get("POSTGRES_USER", "ontology")
_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "localdev")

_FAKE_BEARER = "fake-bearer-value"

_TURTLE = f"""\
@prefix ex: <{_BASE}> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Namespace a owl:Class ;
    rdfs:label "名前空間" ;
    rdfs:comment "オントロジーをまとめる単位" .
"""


class _RecordingStore(SparqlStore):
    """射影を記録するだけの代役。

    **`draft` が射影されないことを確かめるために使う。** 状態の文字列は
    変えられるが、**射影されていないことは射影先を見ないと分からない**。
    """

    def __init__(self) -> None:
        self.graphs: dict[str, str] = {}
        self.default_graph: str | None = None
        self.datasets: set[str] = {_NS}

    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
        return {"head": {"vars": []}, "results": {"bindings": []}}

    async def construct(self, sparql: str, *, dataset: str) -> str:
        return ""

    async def update(self, sparql: str, *, dataset: str) -> None: ...

    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None:
        self.graphs[graph_iri] = turtle

    async def put_default_graph(self, turtle: str, *, dataset: str) -> None:
        self.default_graph = turtle

    async def delete_graph(self, graph_iri: str, *, dataset: str) -> None:
        self.graphs.pop(graph_iri, None)

    async def list_graphs(self, dataset: str) -> list[str]:
        return sorted(self.graphs)

    async def has_default_graph_content(self, dataset: str) -> bool:
        return self.default_graph is not None

    async def list_datasets(self) -> list[str]:
        return sorted(self.datasets)

    async def create_dataset(self, dataset: str) -> None:
        self.datasets.add(dataset)

    async def delete_dataset(self, dataset: str) -> None:
        self.datasets.discard(dataset)
        self.graphs.clear()


@pytest.fixture
def store() -> _RecordingStore:
    return _RecordingStore()


class _FakeAccess:
    def __init__(self) -> None:
        self.token = _FAKE_BEARER


class _FakeCredential:
    def get_token(self, *scopes: str, **kwargs: Any) -> _FakeAccess:
        return _FakeAccess()


def _settings(**kwargs: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "AUTH_MODE": AuthMode.DISABLED,
        "SCAN_ALLOWED_HOSTS": _HOST,
        "MODEL_ENDPOINT": "https://oai-test.openai.azure.com",
        "MODEL_DEPLOYMENT": "ontology-proposer",
        "MODEL_NAME": "gpt-4.1",
    }
    return Settings(**{**base, **kwargs})


async def _local_password(source: ScanSourceRow, settings: Settings) -> str | None:
    return _PASSWORD


async def _setup(session: AsyncSession) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri=_BASE,
        created_by=_ADMIN.object_id,
    )
    repo = RoleRepository(session)
    for principal, role in (
        (_OWNER, NamespaceRole.OWNER),
        (_STEWARD, NamespaceRole.DATA_STEWARD),
    ):
        await repo.grant(
            namespace=_NS,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await ScanRepository(session).create_source(
        namespace=_NS,
        name="self",
        driver="postgresql",
        host=_HOST,
        port=_PORT,
        database=_DATABASE,
        username=_USER,
        auth_mode="entra",
        vault_secret_name=None,
        created_by=_OWNER.object_id,
    )
    await session.commit()


async def _scan(session: AsyncSession) -> None:
    """実物の PostgreSQL をスキャンしてカタログを作る。"""
    await run_scan(
        namespace=_NS,
        name="self",
        principal=_OWNER,
        session=session,
        settings=_settings(),
        secret_resolver=_local_password,
    )


def _install_model(monkeypatch: pytest.MonkeyPatch, content: str = _TURTLE) -> list[str]:
    """モデルの代わりに `content` を返す。**送ったプロンプトを返す。**"""
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", _FakeCredential)
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        prompts.append(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    transport = httpx.MockTransport(handler)
    original = proposal_service.ProposalService.__init__

    def patched(self: Any, *, settings: Settings, client: Any = None) -> None:
        original(self, settings=settings, client=httpx.AsyncClient(transport=transport))

    monkeypatch.setattr(proposal_service.ProposalService, "__init__", patched)
    return prompts


async def _propose(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    store: _RecordingStore,
    *,
    principal: Principal = _OWNER,
    settings: Settings | None = None,
    **kwargs: Any,
) -> Any:
    return await propose_ontology(
        namespace=_NS,
        name="self",
        payload=ProposalRequest(**kwargs),
        principal=principal,
        session=session,
        blob=blob_store,
        store=store,
        settings=settings or _settings(),
    )


# ---------------------------- draft にしか入らない(決定5)


@pytest.mark.integration
async def test_候補は_draft_にしか入らない(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    store: _RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**このファイルの中心である**(ADR-0043 決定5)。

    「LLM の出力が人間の承認を経ずに正本へ入ることはない」を、初めて
    コードで強制した。確かめるのは 2 つ。

    1. 状態が `draft` である
    2. **Fuseki に 1 つもグラフが増えていない**

    **2 のほうが本質である。** 状態の文字列は変えられるが、射影されて
    いないことは**射影先を見ないと分からない**。`draft` が射影されない
    ことが、エージェントから見えないことの実体である(ADR-0010 決定1)。
    """
    await _setup(session)
    await _scan(session)
    _install_model(monkeypatch)
    before = set(await store.list_graphs(_NS))

    result = await _propose(session, blob_store, store, tables=["namespaces"], version="0.1.0")

    assert result["status"] == OntologyVersionStatus.DRAFT.value
    assert result["version"] == "0.1.0"
    assert result["attempts"] == 1

    version = await VersionRepository(session).get(namespace=_NS, version="0.1.0")
    assert version is not None
    assert version.status is OntologyVersionStatus.DRAFT
    # **射影していない**(ADR-0010 決定1)。
    assert version.projected_at is None
    assert set(await store.list_graphs(_NS)) == before, (
        "候補が Fuseki に射影されている(エージェントから見えてしまう)"
    )


@pytest.mark.integration
async def test_承認も提出もしない(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    store: _RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**`submit` は人の意思表示である**(ADR-0043 の却下案)。

    機械が代わりに言うと、レビュアの待ち行列が**誰も意図していない提案**で
    埋まる。`in-review` は名前付きグラフへ射影されるので、Fuseki に載って
    しまう点も違う。
    """
    await _setup(session)
    await _scan(session)
    _install_model(monkeypatch)
    await _propose(session, blob_store, store, tables=["namespaces"], version="0.1.0")

    page = await AuditRepository(session).query(namespace=_NS)
    actions = [event.action for event in page.events]
    assert "published" in actions
    assert "submitted" not in actions, "機械が submit している"
    assert "approved" not in actions, "機械が approve している"


# --------------------------------------------- 権限(決定7)


@pytest.mark.integration
async def test_生成には_owner_が必要(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    store: _RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**`publish` は `data-steward` で足りるが、生成は `owner` である**
    (ADR-0043 決定7)。

    組織のスキーマのメタデータをモデルの提供者へ送る外向きの操作であり、
    呼ぶたびに課金される。「この DB をスキャンしてよい」と決めた人が、
    「このカタログをモデルへ送ってよい」も決める。
    """
    from fastapi import HTTPException

    await _setup(session)
    await _scan(session)
    prompts = _install_model(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await _propose(session, blob_store, store, principal=_STEWARD, tables=["namespaces"])
    assert exc.value.status_code == 403
    assert prompts == [], "権限を確かめる前にモデルを呼んでいる(課金される)"


# ------------------------------- 切り詰めない(決定6)


@pytest.mark.integration
async def test_多すぎたら_413_で断る(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    store: _RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**候補の Turtle には「一部である」と書く場所が無い**(ADR-0043 決定6)。

    切り詰めると、スキーマの一部しか覆っていないオントロジーができ、
    **出力のどこにも「一部である」と書かれない**([ADR-0034](../../../docs/adr/0034-construct-describe.md)
    決定4 と同じ「封筒が無い」問題)。
    """
    from fastapi import HTTPException

    await _setup(session)
    await _scan(session)
    prompts = _install_model(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await _propose(session, blob_store, store, settings=_settings(PROPOSAL_MAX_TABLES=2))
    assert exc.value.status_code == 413
    assert "tables" in str(exc.value.detail)
    assert prompts == [], "上限を超えているのにモデルを呼んでいる"


@pytest.mark.integration
async def test_無いテーブルを指定したら_422(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    store: _RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**黙って無視しない。**

    綴りを間違えたテーブルを落として生成すると、**覆っていない範囲が
    応答のどこにも出ない**。
    """
    from fastapi import HTTPException

    await _setup(session)
    await _scan(session)
    _install_model(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await _propose(session, blob_store, store, tables=["namespaces", "no_such_table"])
    assert exc.value.status_code == 422
    assert "no_such_table" in str(exc.value.detail)


@pytest.mark.integration
async def test_スキャンしていなければ_409(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    store: _RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**「まだスキャンしていない」を「テーブルが 0 件」にしない**
    (ADR-0041 決定7)。
    """
    from fastapi import HTTPException

    await _setup(session)
    prompts = _install_model(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await _propose(session, blob_store, store)
    assert exc.value.status_code == 409
    assert prompts == []


# -------------------------------- 出自が監査に残る(決定10)


@pytest.mark.integration
async def test_モデルの出自が監査に残る(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    store: _RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**監査は追記専用なので消えない**(ADR-0043 決定10)。

    レビュアは「これは何が作ったのか」を版の記録から読める。
    """
    await _setup(session)
    await _scan(session)
    _install_model(monkeypatch)
    await _propose(
        session, blob_store, store, tables=["namespaces"], version="0.1.0", reason="小売の初期案"
    )

    page = await AuditRepository(session).query(namespace=_NS)
    published = [event for event in page.events if event.action == "published"]
    assert len(published) == 1
    reason = published[0].reason or ""
    assert "小売の初期案" in reason, "呼び出し側の理由を捨てている"
    assert "gpt-4.1" in reason
    assert "ontology-proposer" in reason
    assert "試行 1 回" in reason
    assert "レビューを経ていない" in reason
    # **主体は依頼した人間である。** モデルは道具であって主体ではない。
    assert published[0].actor == _OWNER.object_id
    assert published[0].actor_type is ActorType.USER


# ------------------------- カタログが実際にプロンプトへ入る


@pytest.mark.integration
async def test_実物のカタログがプロンプトに入る(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    store: _RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**フェイクのカタログではなく、実物をスキャンした結果を渡す。**

    自分自身の DB をスキャンしているので、`namespaces` テーブルの主キーと
    列がプロンプトに現れる。
    """
    await _setup(session)
    await _scan(session)
    prompts = _install_model(monkeypatch)
    await _propose(session, blob_store, store, tables=["namespaces"], version="0.1.0")

    assert len(prompts) == 1
    prompt = prompts[0]
    assert "public.namespaces" in prompt
    assert "base_iri" in prompt
    assert "PRIMARY KEY" in prompt
    # **区切りの内側にカタログがある**(ADR-0043 決定2)。
    assert prompt.index("=== CATALOG") < prompt.index("public.namespaces")
    assert prompt.index("public.namespaces") < prompt.index("=== END CATALOG")
    # **選んだテーブルだけが入る。**
    assert "public.audit_events" not in prompt


@pytest.mark.integration
async def test_base_iri_の外を作るモデルの出力を受け付けない(
    session: AsyncSession,
    blob_store: OntologyBlobStore,
    store: _RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**注入が成功しても `draft` すら作らせない**(ADR-0043 決定3)。

    ここでは「モデルが他の名前空間に用語を作った」状況を作る。検証で
    落ちるので**版は 1 つも増えない**。
    """
    from fastapi import HTTPException

    await _setup(session)
    await _scan(session)
    _install_model(
        monkeypatch,
        content=(
            "@prefix other: <https://elsewhere.example/x#> .\n"
            "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n\n"
            "other:Evil a owl:Class .\n"
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await _propose(session, blob_store, store, tables=["namespaces"])
    assert exc.value.status_code == 422
    assert "base_iri" in str(exc.value.detail)

    versions = await VersionRepository(session).list_for(_NS)
    assert versions == [], "検証に落ちた候補が版として残っている"
