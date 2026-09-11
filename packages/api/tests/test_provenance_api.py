"""監査証跡の PROV-O 書き出しの API 経路(ADR-0026、`P2A-07`)。

ADR-0006 決定3 が約束していた「PROV-O で表現する」の口である。

ここで固定するのは 4 つである。

1. **権限は `/audit` と同じ `data-analyst`**(決定1)。同じ情報を別の語彙で
   出すだけなので、ここだけ厳しくすると見せかけの制限になる
2. **`text/turtle` を返す**
3. **切り詰めは `next_cursor` の有無で判断する**(件数と `limit` の比較では
   判断できない)
4. **2 つの表現が同じ絞り込みを取る**(`cursor` 以外)。片方だけ絞り込みが
   増えると、表現によって見える範囲が変わる
5. **主体の種別が正本から書き出しまで通る**([ADR-0035](../../../docs/adr/0035-actor-type.md)、
   `P2A-16`)。**列に入れた値が PROV-O のクラスとして出る**ところまでを
   1 本で繋ぐ。`prov.py` 側の単体テストはフェイクの `AuditEvent` を使うので、
   **列 → モデル → 書き出しの写し間違いはここでしか捕まらない**
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import PROV, RDF
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.repositories.versions import AuditRepository
from ontology_api.routers.audit import export_provenance, query_audit
from ontology_core.auth.entra import Principal
from ontology_core.db import AuditEventRow
from ontology_core.models import ActorType, NamespaceRole, PlatformRole
from ontology_core.prov import AGENT_BASE, BUNDLE_BASE, ONT, REVISION_BASE

_NS = "prov-ns"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")

_BUNDLE = URIRef(f"{BUNDLE_BASE}{_NS}")


async def _setup(session: AsyncSession) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri="https://e.example/#",
        created_by=_ADMIN.object_id,
    )
    await RoleRepository(session).grant(
        namespace=_NS,
        principal_id=_ANALYST.object_id,
        role=NamespaceRole.DATA_ANALYST,
        granted_by=_ADMIN.object_id,
    )
    await session.commit()


async def _record(
    session: AsyncSession,
    *,
    action: str,
    subject: str,
    actor: str = "alice-oid",
    reason: str = "",
    actor_type: ActorType = ActorType.UNKNOWN,
) -> None:
    await AuditRepository(session).record(
        namespace=_NS,
        action=action,
        actor=actor,
        actor_type=actor_type,
        subject=subject,
        reason=reason,
    )
    await session.commit()


async def _export(session: AsyncSession, **kwargs: object) -> Graph:
    """書き出してパースし直す。**壊れた Turtle はここで例外になる。**"""
    response = await export_provenance(
        namespace=_NS,
        principal=_ANALYST,
        session=session,
        **kwargs,  # type: ignore[arg-type]
    )
    assert response.media_type is not None
    assert response.media_type.startswith("text/turtle")
    graph = Graph()
    graph.parse(data=bytes(response.body).decode("utf-8"), format="turtle")
    return graph


# ------------------------------------------------------------------ 中身


@pytest.mark.integration
async def test_記録した決定が_PROV_O_として出る(session: AsyncSession) -> None:
    await _setup(session)
    await _record(session, action="published", subject=f"{_NS}@1.0.0", reason="初版")
    await _record(session, action="approved", subject=f"{_NS}@1.0.0", actor="bob-oid")

    graph = await _export(session)
    revision = URIRef(f"{REVISION_BASE}{_NS}/1.0.0")
    assert (revision, RDF.type, PROV.Entity) in graph
    # 公開が版を生み、承認は既にある版を使う。
    assert list(graph.subjects(PROV.generated, revision))
    assert list(graph.subjects(PROV.used, revision))
    assert set(graph.objects(_BUNDLE, ONT.eventCount)) == {Literal(2)}


@pytest.mark.integration
async def test_イベントが無くても束は返る(session: AsyncSession) -> None:
    """**404 にしない。** 「まだ何も起きていない」はエラーではない。

    空の Turtle でもない — それだと名前空間を間違えたのかどうかも
    受け取った側には分からない。
    """
    await _setup(session)
    graph = await _export(session)
    assert (_BUNDLE, RDF.type, PROV.Bundle) in graph
    assert set(graph.objects(_BUNDLE, ONT.eventCount)) == {Literal(0)}
    assert set(graph.objects(_BUNDLE, ONT.truncated)) == {Literal(False)}


@pytest.mark.integration
async def test_絞り込みが効く(session: AsyncSession) -> None:
    await _setup(session)
    await _record(session, action="published", subject=f"{_NS}@1.0.0")
    await _record(session, action="rejected", subject=f"{_NS}@2.0.0")

    graph = await _export(session, action="rejected")
    assert set(graph.objects(_BUNDLE, ONT.eventCount)) == {Literal(1)}
    assert set(graph.objects(None, ONT.action)) == {Literal("rejected")}


# ------------------------------------------------------------------ 切り詰め


@pytest.mark.integration
async def test_上限を超えたら切り詰めたことを書く(session: AsyncSession) -> None:
    """**RDF は「無い」と「返していない」を区別できない**(ADR-0026 決定5)。"""
    await _setup(session)
    for i in range(3):
        await _record(session, action="published", subject=f"{_NS}@1.0.{i}")

    graph = await _export(session, limit=2)
    assert set(graph.objects(_BUNDLE, ONT.eventCount)) == {Literal(2)}
    assert set(graph.objects(_BUNDLE, ONT.truncated)) == {Literal(True)}


@pytest.mark.integration
async def test_件数が上限ちょうどでも切り詰めていない(session: AsyncSession) -> None:
    """**`len(events) == limit` で判定してはいけない。**

    リポジトリが `limit + 1` 件取って続きの有無を確かめているので、
    `next_cursor` を見れば正しく分かる。件数で判定すると
    **ちょうど全件だったときに「まだある」と嘘をつく。**
    """
    await _setup(session)
    for i in range(2):
        await _record(session, action="published", subject=f"{_NS}@1.0.{i}")

    graph = await _export(session, limit=2)
    assert set(graph.objects(_BUNDLE, ONT.eventCount)) == {Literal(2)}
    assert set(graph.objects(_BUNDLE, ONT.truncated)) == {Literal(False)}


# ------------------------------------------------------------------ 権限


@pytest.mark.integration
async def test_権限が無ければ_403(session: AsyncSession) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await export_provenance(namespace=_NS, principal=_STRANGER, session=session)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_無い名前空間は_404(session: AsyncSession) -> None:
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await export_provenance(namespace="missing-ns", principal=_ANALYST, session=session)
    assert exc.value.status_code == 404


@pytest.mark.integration
async def test_使えない名前空間名は_400(session: AsyncSession) -> None:
    """名前空間名はセキュリティ境界である(不変条件5)。**404 の前に弾く。**"""
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await export_provenance(namespace="../etc", principal=_ANALYST, session=session)
    assert exc.value.status_code == 400


@pytest.mark.integration
async def test_照会の指定の誤りは_422(session: AsyncSession) -> None:
    """**空の書き出しを返さない。** 空だと「本当に何も無い」と誤解させる。"""
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await export_provenance(
            namespace=_NS, principal=_ANALYST, session=session, limit=AuditRepository.MAX_LIMIT + 1
        )
    assert exc.value.status_code == 422


# --------------------------------------------------------- 2 つの表現の一致


def test_絞り込みは_JSON_と_Turtle_で揃っている() -> None:
    """**片方だけ絞り込みが増えると、表現によって見える範囲が変わる。**

    `cursor` だけは意図的に無い(ADR-0026 決定1)。RDF は順序を持たないので、
    カーソルで切り出した断片を RDF として渡す意味が薄い。
    """
    json_params = set(inspect.signature(query_audit).parameters)
    turtle_params = set(inspect.signature(export_provenance).parameters)
    assert json_params - turtle_params == {"cursor"}, (
        "JSON だけが取る絞り込みがある。両方に足すか、ADR に理由を書くこと"
    )
    assert turtle_params - json_params == set()


# ----------------------------------------- 主体の種別(ADR-0035、`P2A-16`)


@pytest.mark.integration
async def test_列に入れた種別が_PROV_のクラスとして出る(session: AsyncSession) -> None:
    """**正本 → モデル → 書き出しを 1 本で繋ぐ。**

    `prov.py` の単体テストはフェイクの `AuditEvent` を組み立てるので、
    `audit_events.actor_type`(文字列)から `ActorType` への写しが壊れても
    気づけない。ここが唯一その繋ぎを見るテストである。
    """
    await _setup(session)
    await _record(
        session,
        action="published",
        subject=f"{_NS}@1.0.0",
        actor="alice-oid",
        actor_type=ActorType.USER,
    )
    await _record(
        session,
        action="approved",
        subject=f"{_NS}@1.0.0",
        actor="robot-oid",
        actor_type=ActorType.SERVICE_PRINCIPAL,
    )

    graph = await _export(session)
    alice = URIRef(f"{AGENT_BASE}alice-oid")
    robot = URIRef(f"{AGENT_BASE}robot-oid")
    assert (alice, RDF.type, PROV.Person) in graph
    assert (robot, RDF.type, PROV.SoftwareAgent) in graph
    # 行為ごとの記録も出る(クラスは主体、種別は行為。ADR-0035 決定4)。
    assert set(graph.objects(None, ONT.actorType)) == {
        Literal("user"),
        Literal("service-principal"),
    }


@pytest.mark.integration
async def test_この機能より前の行は種別を主張しない(session: AsyncSession) -> None:
    """**`NULL` は「問うていない」**(ADR-0035 決定3・5)。

    マイグレーションが既存の行を `'unknown'` で埋めないので、この状態は
    実運用で必ず現れる。行を直接入れているのは、**リポジトリ経由では
    `NULL` を書けない**ためである(`record` は `actor_type` を必須に
    している。決定6)。
    """
    await _setup(session)
    session.add(
        AuditEventRow(
            namespace=_NS,
            action="published",
            actor="old-oid",
            subject=f"{_NS}@1.0.0",
            reason="",
        )
    )
    await session.commit()

    graph = await _export(session)
    agent = URIRef(f"{AGENT_BASE}old-oid")
    assert (agent, RDF.type, PROV.Agent) in graph
    assert not list(graph.triples((agent, RDF.type, PROV.Person)))
    assert not list(graph.triples((agent, RDF.type, PROV.SoftwareAgent)))
    assert not list(graph.triples((None, ONT.actorType, None))), (
        "問うていない行に種別を出してはならない(unknown とは意味が違う)"
    )


@pytest.mark.integration
async def test_分からなかったことは書き出しに出る(session: AsyncSession) -> None:
    """`idtyp` を設定していないテナントの見た目である。

    **無言の欠落にしない** — 設定漏れが読み取れることがこの機能の価値の
    半分である。
    """
    await _setup(session)
    await _record(
        session,
        action="published",
        subject=f"{_NS}@1.0.0",
        actor="who-oid",
        actor_type=ActorType.UNKNOWN,
    )
    graph = await _export(session)
    assert set(graph.objects(None, ONT.actorType)) == {Literal("unknown")}
    assert not list(graph.triples((URIRef(f"{AGENT_BASE}who-oid"), RDF.type, PROV.Person)))


@pytest.mark.integration
async def test_監査の照会でも種別が読める(session: AsyncSession) -> None:
    """**PROV-O を経由しなくても読める。**

    `GET /audit` の JSON にも出る — 書き出しの語彙を解さないクライアントが
    種別を読む手段を残しておく(ADR-0027 決定3 と同じ考え方)。
    """
    await _setup(session)
    await _record(
        session,
        action="published",
        subject=f"{_NS}@1.0.0",
        actor_type=ActorType.SERVICE_PRINCIPAL,
    )
    page = await query_audit(namespace=_NS, principal=_ANALYST, session=session)
    assert [e.actor_type for e in page.events] == [ActorType.SERVICE_PRINCIPAL]
