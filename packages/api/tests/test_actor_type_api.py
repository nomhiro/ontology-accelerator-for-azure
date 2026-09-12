"""主体の種別が**記録の経路すべてで**監査に届く(ADR-0035 決定6、`P2A-16`)。

## なぜこのファイルが要るのか

`ProjectionService` の各メソッドとルータには
`actor_type: ActorType = ActorType.UNKNOWN` の既定値がある(決定6)。既定値は
**安全な向き**である — 渡し忘れは情報を落とすだけで偽の主張はしない —
代わりに**渡し忘れが黙って通る**。

`AuditRepository.record` は必須引数にしてあるので「記録するのに種別を
考えなかった」は型検査で止まる。しかし「サービス層までは考えたが、**ルータが
`principal.actor_type` を渡し忘れた**」は止まらない。そこを塞ぐのがここである。

**列挙は網羅である。** `audit_events` に書く経路をすべて数え上げ、どれも
種別を運ぶことを固定する。新しい行為を足したときにここに 1 行足すのを忘れる
と、`test_監査に書く行為をすべて数え上げている` が落ちる。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.routers.access import AccessLogPurge, purge_access_log
from ontology_api.routers.mappings import MappingDeclare, declare_mapping, revoke_mapping
from ontology_api.routers.namespaces import (
    NamespaceRetire,
    retire_namespace,
    unretire_namespace,
)
from ontology_api.routers.questions import QuestionSetRevise, revise_question_set
from ontology_api.routers.scan import (
    ScanSourceRegister,
    ScanSourceRemove,
    register_scan_source,
    remove_scan_source,
)
from ontology_api.routers.versions import (
    PublishRequest,
    RejectRequest,
    approve_version,
    publish_version,
    reject_version,
    submit_version,
)
from ontology_core.auth.entra import Principal
from ontology_core.blob import OntologyBlobStore
from ontology_core.config import AuthMode, Settings
from ontology_core.db import AuditEventRow
from ontology_core.models import ActorType, NamespaceRole, PlatformRole
from ontology_core.prov import ACTIVITY_TYPES
from ontology_core.sparql.client import SparqlStore

_NS = "actor-type-ns"
_BASE = "https://e.example/at#"

_ADMIN = Principal(
    subject="admin",
    object_id="admin-oid",
    platform_roles=(PlatformRole.PLATFORM_ADMIN.value,),
    actor_type=ActorType.USER,
)
#: 人間の `owner`。`idtyp=user` が乗ったトークンに相当する。
_OWNER = Principal(subject="owner", object_id="owner-oid", actor_type=ActorType.USER)
#: サービスプリンシパルの `owner`。`idtyp=app` が乗ったトークンに相当する。
#:
#: **四眼原則の記録で意味を持つのがこの区別である** — 機械が承認したことを
#: 人間の承認として見せてはいけない(ADR-0026 決定3)。
#:
#: `owner` を与えているのは**権限の検証がここの関心ではない**ためである
#: (退役の解除とマッピングの取り消しは `owner` を要求する)。検証したいのは
#: 種別が届くことだけなので、権限で落ちない主体を 2 つ用意して種別だけを
#: 変える。
_ROBOT = Principal(subject="robot", object_id="robot-oid", actor_type=ActorType.SERVICE_PRINCIPAL)

_TTL = f"@prefix e: <{_BASE}> .\ne:Thing a <http://www.w3.org/2002/07/owl#Class> .\n"
_TTL2 = (
    f"@prefix e: <{_BASE}> .\n"
    "e:Thing a <http://www.w3.org/2002/07/owl#Class> .\n"
    "e:Other a <http://www.w3.org/2002/07/owl#Class> .\n"
)
_QUESTIONS = """
questions:
  - id: cq-1
    question: 何かあるか
    expect: ask_true
    sparql: |
      ASK { ?s ?p ?o }
"""


async def _setup(session: AsyncSession) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri=_BASE,
        created_by=_ADMIN.object_id,
        require_two_person_approval=False,
    )
    repo = RoleRepository(session)
    for principal, role in (
        (_OWNER, NamespaceRole.OWNER),
        (_ROBOT, NamespaceRole.OWNER),
    ):
        await repo.grant(
            namespace=_NS,
            principal_id=principal.object_id,
            role=role,
            granted_by=_ADMIN.object_id,
        )
    await session.commit()


async def _types_by_action(session: AsyncSession) -> dict[str, str | None]:
    """記録された `action` から `actor_type` への対応を返す。"""
    rows = (await session.execute(select(AuditEventRow).order_by(AuditEventRow.id))).scalars()
    return {row.action: row.actor_type for row in rows}


class _NullStore(SparqlStore):
    """射影を受け取るだけの代役。**射影の検証はここの関心ではない。**"""

    def __init__(self) -> None:
        self.graphs: dict[str, str] = {}
        self.datasets: set[str] = {"ds"}

    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
        return {"head": {"vars": []}, "results": {"bindings": []}}

    async def construct(self, sparql: str, *, dataset: str) -> str:
        return ""

    async def update(self, sparql: str, *, dataset: str) -> None: ...

    async def put_graph(self, graph_iri: str, turtle: str, *, dataset: str) -> None:
        self.graphs[graph_iri] = turtle

    async def put_default_graph(self, turtle: str, *, dataset: str) -> None: ...

    async def delete_graph(self, graph_iri: str, *, dataset: str) -> None:
        self.graphs.pop(graph_iri, None)

    async def list_graphs(self, dataset: str) -> list[str]:
        return sorted(self.graphs)

    async def has_default_graph_content(self, dataset: str) -> bool:
        return True

    async def list_datasets(self) -> list[str]:
        return sorted(self.datasets)

    async def create_dataset(self, dataset: str) -> None:
        self.datasets.add(dataset)

    async def delete_dataset(self, dataset: str) -> None:
        self.datasets.discard(dataset)
        self.graphs.clear()


@pytest.fixture
def store() -> _NullStore:
    return _NullStore()


async def _publish(
    session: AsyncSession,
    blob: OntologyBlobStore,
    store: SparqlStore,
    settings: Settings,
    *,
    principal: Principal = _OWNER,
    turtle: str = _TTL,
    version: str = "1.0.0",
) -> Any:
    class _Resp:
        status_code = 0

    return await publish_version(
        namespace=_NS,
        payload=PublishRequest(turtle=turtle, version=version),
        principal=principal,
        session=session,
        blob=blob,
        store=store,
        settings=settings,
        response=_Resp(),  # type: ignore[arg-type]
    )


# ------------------------------------------------ 記録の経路すべてを数え上げる


async def test_版のライフサイクルが種別を運ぶ(
    session: AsyncSession, blob_store: OntologyBlobStore, store: _NullStore, settings: Settings
) -> None:
    """`published` / `submitted` / `approved` / `superseded` / `rejected`。

    **`approved` と `superseded` は別の行為だが同じ承認から生まれる。**
    `superseded` は承認した主体の種別で記録する(ADR-0035 決定4 の
    「行為ごとに記録する」に従う)。
    """
    await _setup(session)
    kwargs: dict[str, Any] = {
        "session": session,
        "blob": blob_store,
        "store": store,
        "settings": settings,
    }

    await _publish(session, blob_store, store, settings, principal=_OWNER, version="1.0.0")
    await submit_version(namespace=_NS, version="1.0.0", principal=_OWNER, payload=None, **kwargs)
    await approve_version(namespace=_NS, version="1.0.0", principal=_ROBOT, payload=None, **kwargs)

    # 2 版目を承認すると 1 版目が superseded になる。
    await _publish(
        session, blob_store, store, settings, principal=_OWNER, turtle=_TTL2, version="2.0.0"
    )
    await submit_version(namespace=_NS, version="2.0.0", principal=_OWNER, payload=None, **kwargs)
    await approve_version(namespace=_NS, version="2.0.0", principal=_ROBOT, payload=None, **kwargs)

    types = await _types_by_action(session)
    assert types["published"] == "user"
    assert types["submitted"] == "user"
    # **機械が承認したことが記録に残る。** これが四眼原則の記録で意味を持つ。
    assert types["approved"] == "service-principal"
    assert types["superseded"] == "service-principal"


async def test_却下が種別を運ぶ(
    session: AsyncSession, blob_store: OntologyBlobStore, store: _NullStore, settings: Settings
) -> None:
    await _setup(session)
    kwargs: dict[str, Any] = {
        "session": session,
        "blob": blob_store,
        "store": store,
        "settings": settings,
    }
    await _publish(session, blob_store, store, settings)
    await submit_version(namespace=_NS, version="1.0.0", principal=_OWNER, payload=None, **kwargs)
    await reject_version(
        namespace=_NS,
        version="1.0.0",
        payload=RejectRequest(reason="まだ足りない"),
        principal=_ROBOT,
        **kwargs,
    )
    assert (await _types_by_action(session))["rejected"] == "service-principal"


async def test_退役と解除が種別を運ぶ(
    session: AsyncSession, blob_store: OntologyBlobStore, store: _NullStore, settings: Settings
) -> None:
    await _setup(session)
    kwargs: dict[str, Any] = {
        "session": session,
        "blob": blob_store,
        "store": store,
        "settings": settings,
    }
    await retire_namespace(
        namespace=_NS, payload=NamespaceRetire(reason="統合された"), principal=_OWNER, **kwargs
    )
    await unretire_namespace(
        namespace=_NS, payload=NamespaceRetire(reason="間違いだった"), principal=_ROBOT, **kwargs
    )
    types = await _types_by_action(session)
    assert types["retired"] == "user"
    assert types["unretired"] == "service-principal"


async def test_マッピングの宣言と取り消しが種別を運ぶ(session: AsyncSession) -> None:
    await _setup(session)
    await declare_mapping(
        namespace=_NS,
        payload=MappingDeclare(
            source_term=f"{_BASE}Thing",
            target_term="http://www.w3.org/2004/02/skos/core#Concept",
            predicate="closeMatch",
            reason="近い",
        ),
        principal=_OWNER,
        session=session,
    )
    await revoke_mapping(
        namespace=_NS,
        principal=_ROBOT,
        session=session,
        source_term=f"{_BASE}Thing",
        target_term="http://www.w3.org/2004/02/skos/core#Concept",
    )
    types = await _types_by_action(session)
    assert types["mapping-declared"] == "user"
    assert types["mapping-revoked"] == "service-principal"


async def test_想定質問の改訂が種別を運ぶ(session: AsyncSession) -> None:
    await _setup(session)
    await revise_question_set(
        namespace=_NS,
        payload=QuestionSetRevise(content=_QUESTIONS, reason="最初の版"),
        principal=_OWNER,
        session=session,
    )
    assert (await _types_by_action(session))["questions-revised"] == "user"


async def test_アクセスログの削除が種別を運ぶ(session: AsyncSession) -> None:
    await _setup(session)
    await purge_access_log(
        namespace=_NS,
        payload=AccessLogPurge(before=datetime.now(UTC), reason="保持期間を過ぎた"),
        principal=_OWNER,
        session=session,
    )
    assert (await _types_by_action(session))["access-log-purged"] == "user"


#: スキャンのソースの登録で使う設定。
#:
#: **`SCAN_ALLOWED_HOSTS` を明示する。** 既定は空で、空なら登録が 403 になる
#: (ADR-0041 決定5)。ここでは接続しないので**到達できないホストでよい**。
_SCAN_HOST = "db.example.internal"


def _scan_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        AUTH_MODE=AuthMode.DISABLED,
        SCAN_ALLOWED_HOSTS=_SCAN_HOST,
    )


def _scan_payload() -> ScanSourceRegister:
    return ScanSourceRegister(
        name="sales-db",
        driver="postgresql",
        host=_SCAN_HOST,
        port=5432,
        database="sales",
        username="ontology_scanner",
        auth_mode="entra",
    )


async def test_スキャンのソースの登録が種別を運ぶ(session: AsyncSession) -> None:
    """**このシステムに顧客 DB への到達手段を与える行為である**(ADR-0041)。

    だから誰が登録したかが監査に残る。**人間か機械かも残る** — 自動化が
    ソースを足したのと運用者が足したのは、事後の説明が違う。
    """
    await _setup(session)
    await register_scan_source(
        namespace=_NS,
        payload=_scan_payload(),
        principal=_OWNER,
        session=session,
        settings=_scan_settings(),
    )
    assert (await _types_by_action(session))["scan-source-registered"] == "user"


async def test_サービスプリンシパルの登録も種別を運ぶ(session: AsyncSession) -> None:
    """**機械が登録したことを人間の登録として見せない**(ADR-0035 決定1)。"""
    await _setup(session)
    await register_scan_source(
        namespace=_NS,
        payload=_scan_payload(),
        principal=_ROBOT,
        session=session,
        settings=_scan_settings(),
    )
    assert (await _types_by_action(session))["scan-source-registered"] == "service-principal"


async def test_スキャンのソースの削除が種別を運ぶ(session: AsyncSession) -> None:
    """**`scan_sources` の行は消えるが、消したという事実は消えない**(ADR-0041)。"""
    await _setup(session)
    await register_scan_source(
        namespace=_NS,
        payload=_scan_payload(),
        principal=_OWNER,
        session=session,
        settings=_scan_settings(),
    )
    await remove_scan_source(
        namespace=_NS,
        name="sales-db",
        payload=ScanSourceRemove(reason="このソースは使わなくなった"),
        principal=_OWNER,
        session=session,
    )
    assert (await _types_by_action(session))["scan-source-removed"] == "user"


def test_監査に書く行為をすべて数え上げている() -> None:
    """**このファイルが網羅であることを固定する。**

    `ontology_core.prov.ACTIVITY_TYPES` は監査の `action` から PROV-O の
    下位クラスへの対応表である。そこに載っている行為はすべて、上のどれかの
    テストで種別が届くことを確かめてある。

    **新しい行為を足したらここが落ちる。** 落ちたら、その行為の記録経路に
    `actor_type` が届くことを確かめるテストを足してから、この集合に加える。
    `retired` / `unretired` が `ACTIVITY_TYPES` に無いのは ADR-0032 の
    決定(独自の下位クラスを持たない)であり、種別は運ぶ。
    """
    covered = {
        "access-log-purged",
        "approved",
        "mapping-declared",
        "mapping-revoked",
        "published",
        "questions-revised",
        "rejected",
        "retired",
        "scan-source-registered",
        "scan-source-removed",
        "submitted",
        "superseded",
        "unretired",
    }
    assert set(ACTIVITY_TYPES) <= covered, (
        f"種別が届くことを確かめていない行為がある: {set(ACTIVITY_TYPES) - covered}"
    )


# ------------------------------------------------------- 分からないときの記録


async def test_種別が分からなければ_unknown_を書く(session: AsyncSession) -> None:
    """**`NULL` にしない**(ADR-0035 決定3)。

    `idtyp` を設定していないテナントでは種別が分からない。そのとき列を
    `NULL` にすると「この機能より前に書かれた行」と区別できなくなる。
    **問うたことは記録に残す。**
    """
    await _setup(session)
    unknown = Principal(subject="u", object_id=_OWNER.object_id)
    assert unknown.actor_type is ActorType.UNKNOWN
    await revise_question_set(
        namespace=_NS,
        payload=QuestionSetRevise(content=_QUESTIONS, reason="最初の版"),
        principal=unknown,
        session=session,
    )
    assert (await _types_by_action(session))["questions-revised"] == "unknown"
