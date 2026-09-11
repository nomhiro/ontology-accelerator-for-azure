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
from ontology_core.models import NamespaceRole, PlatformRole
from ontology_core.prov import BUNDLE_BASE, ONT, REVISION_BASE

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
    session: AsyncSession, *, action: str, subject: str, actor: str = "alice-oid", reason: str = ""
) -> None:
    await AuditRepository(session).record(
        namespace=_NS, action=action, actor=actor, subject=subject, reason=reason
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
