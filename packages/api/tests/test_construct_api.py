"""`CONSTRUCT` / `DESCRIBE` の API 経路(ADR-0034、`P2A-14`)。

[ADR-0025](../../../docs/adr/0025-result-limit-enforcement.md) 決定8 は
これらを **400 で断っていた**。断った理由は「実装していないから」ではなく
**通すと 502 になっていた**からである(`Accept: application/sparql-results+json`
を送るのに Fuseki が Turtle を返し、JSON の解析に失敗していた)。

ここで固定するのは 5 つである。

1. **同じエンドポイントで `text/turtle` が返る**(決定3)。URL は分けない —
   それが SPARQL 1.1 Protocol の振る舞いである
2. **クエリの形の判定は 1 か所**(決定1)。ガードとルータが食い違わない
3. **上限を超えたら切り詰めずに 413**(決定4)。行数(ADR-0025 決定5)とは
   意図的に違う判断である
4. **行とトリプルを混ぜない**(決定7)。`returned_row_count` は `NULL`
5. **退役と権限の検査は `SELECT` と同じ経路を通る**
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException, Response
from rdflib import Graph, URIRef
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.access import AccessRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.routers.sparql import SparqlQueryRequest, run_query
from ontology_core.auth.entra import Principal
from ontology_core.config import AuthMode, Settings
from ontology_core.models import NamespaceRole, PlatformRole
from ontology_core.sparql.client import SparqlStore, SparqlStoreError

_NS = "construct-ns"
_BASE = "https://e.example/c#"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")
_STRANGER = Principal(subject="stranger", object_id="stranger-oid")

_CONSTRUCT = "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"
_DESCRIBE = f"DESCRIBE <{_BASE}S0>"


def _turtle(count: int) -> str:
    lines = [f"@prefix e: <{_BASE}> ."]
    lines += [f"e:S{i} e:p e:O{i} ." for i in range(count)]
    return "\n".join(lines) + "\n"


class _GraphStore(SparqlStore):
    """`construct` が指定したトリプル数の Turtle を返すストア。

    **`query` は使われてはいけない。** `CONSTRUCT` が `query` を通ると
    `Accept` と実際の応答が食い違い、ADR-0025 決定8 が直した 502 に戻る。
    """

    def __init__(self, triples: int = 3, *, body: str | None = None) -> None:
        self._triples = triples
        self._body = body
        self.construct_calls: list[str] = []

    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
        if "deprecated" in sparql.lower():
            return {"head": {"vars": ["term"]}, "results": {"bindings": []}}
        raise AssertionError("CONSTRUCT / DESCRIBE が query を通っている(502 の原因)")

    async def construct(self, sparql: str, *, dataset: str) -> str:
        self.construct_calls.append(sparql)
        return _turtle(self._triples) if self._body is None else self._body

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


def _settings(*, triples: int = 100) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        AUTH_MODE=AuthMode.DISABLED,
        SPARQL_MAX_TRIPLES=triples,
    )


async def _setup(session: AsyncSession) -> None:
    await NamespaceRepository(session).create(
        name=_NS,
        display_name=_NS,
        description="",
        base_iri=_BASE,
        created_by=_ADMIN.object_id,
        require_two_person_approval=False,
    )
    await RoleRepository(session).grant(
        namespace=_NS,
        principal_id=_ANALYST.object_id,
        role=NamespaceRole.DATA_ANALYST,
        granted_by=_ADMIN.object_id,
    )
    await session.commit()


async def _run(
    session: AsyncSession,
    *,
    query: str = _CONSTRUCT,
    store: SparqlStore | None = None,
    settings: Settings | None = None,
    principal: Principal = _ANALYST,
) -> Response:
    result = await run_query(
        namespace=_NS,
        payload=SparqlQueryRequest(query=query),
        principal=principal,
        session=session,
        settings=settings or _settings(),
        store=store or _GraphStore(),
        response=Response(),
    )
    assert not isinstance(result, dict), "RDF を返す形が JSON を返している"
    return result


def _graph(response: Response) -> Graph:
    graph = Graph()
    graph.parse(data=bytes(response.body).decode("utf-8"), format="turtle")
    return graph


# --------------------------------------------------------- 通ること


@pytest.mark.integration
@pytest.mark.parametrize("query", [_CONSTRUCT, _DESCRIBE])
async def test_RDF_を返す形が_Turtle_で返る(session: AsyncSession, query: str) -> None:
    """**ADR-0025 決定8 の 400 が解除された**(ADR-0034)。

    ガードのメッセージが指していた `P2A-14` が実在するようになった。
    """
    await _setup(session)
    response = await _run(session, query=query)
    assert response.media_type is not None
    assert response.media_type.startswith("text/turtle")
    assert len(_graph(response)) == 3


@pytest.mark.integration
async def test_construct_を通る_query_は使わない(session: AsyncSession) -> None:
    """**`Accept` をクエリの形に合わせる**(ADR-0034 決定2)。

    `query` を通すと `Accept: application/sparql-results+json` を送るのに
    Fuseki が Turtle を返し、**JSON の解析に失敗して 502 になる**
    (ADR-0025 決定8 が `CONSTRUCT` を断っていた理由そのもの)。
    `_GraphStore.query` はこれを `AssertionError` で検出する。
    """
    await _setup(session)
    store = _GraphStore()
    await _run(session, store=store)
    assert store.construct_calls == [_CONSTRUCT]


@pytest.mark.integration
async def test_空のグラフも_200_で返る(session: AsyncSession) -> None:
    """**「0 件」はエラーではない。**"""
    await _setup(session)
    response = await _run(session, store=_GraphStore(0))
    assert len(_graph(response)) == 0


@pytest.mark.integration
async def test_応答は解析し直した_Turtle_である(session: AsyncSession) -> None:
    """**ストアの本文をそのまま転送しない**(ADR-0034 決定6)。"""
    await _setup(session)
    body = f"<{_BASE}S0> <{_BASE}p> <{_BASE}O0> .\n"
    response = await _run(session, store=_GraphStore(body=body))
    graph = _graph(response)
    assert (URIRef(_BASE + "S0"), URIRef(_BASE + "p"), URIRef(_BASE + "O0")) in graph


# --------------------------------------------------------- 上限


@pytest.mark.integration
async def test_上限を超えたら_413_で断る(session: AsyncSession) -> None:
    """**切り詰めない**(ADR-0034 決定4)。

    行数(ADR-0025 決定5)とは意図的に違う判断である — **RDF には
    「切り詰めた」と書く封筒が無く**、ヘッダに書いてもエージェントは
    見ない(ADR-0017 決定3)。
    """
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _run(session, store=_GraphStore(11), settings=_settings(triples=10))
    assert exc.value.status_code == 413
    # **実数を返す。** どれだけ絞ればよいか分かるようにする。
    assert "11" in exc.value.detail
    assert "切り詰めて返しません" in exc.value.detail


@pytest.mark.integration
async def test_上限ちょうどは通す(session: AsyncSession) -> None:
    await _setup(session)
    response = await _run(session, store=_GraphStore(10), settings=_settings(triples=10))
    assert len(_graph(response)) == 10


def test_トリプル数の上限は_1_以上に限る() -> None:
    """**「0 なら無制限」という解釈を作らない**(`SPARQL_MAX_RESULTS` と同じ)。"""
    for bad in (0, -1):
        with pytest.raises(ValueError):
            _settings(triples=bad)
    assert _settings(triples=1).sparql_max_triples == 1


# --------------------------------------------------------- 解析の失敗


@pytest.mark.integration
async def test_解析できない応答は_502(session: AsyncSession) -> None:
    """**空のグラフを返さない。** 「解析できなかった」を「該当なし」にしない。"""
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _run(session, store=_GraphStore(body="これは Turtle ではない <<<"))
    assert exc.value.status_code == 502


@pytest.mark.integration
async def test_ストアの失敗は_502(session: AsyncSession) -> None:
    await _setup(session)

    class _Failing(_GraphStore):
        async def construct(self, sparql: str, *, dataset: str) -> str:
            raise SparqlStoreError("到達できません")

    with pytest.raises(HTTPException) as exc:
        await _run(session, store=_Failing())
    assert exc.value.status_code == 502


# --------------------------------------------------------- アクセスログ


@pytest.mark.integration
async def test_行とトリプルを混ぜない(session: AsyncSession) -> None:
    """**`returned_row_count` は `NULL` である**(ADR-0034 決定7)。

    `0` を書くと、アクセスログを読んだ人が「何も返さなかったクエリ」として
    数える。「0 行返した」と「行という概念が無い」は違う。
    """
    await _setup(session)
    await _run(session, store=_GraphStore(4))
    await session.commit()

    events = await AccessRepository(session).query_events(namespace=_NS)
    assert len(events.events) == 1
    event = events.events[0]
    assert event.returned_row_count is None, "行の概念が無いのに 0 を書いている"
    assert event.returned_triple_count == 4


@pytest.mark.integration
async def test_グラフから用語を数える(session: AsyncSession) -> None:
    """**`SELECT` の束縛より正確である**(ADR-0034 決定7)。"""
    await _setup(session)
    await _run(session, store=_GraphStore(2))
    await session.commit()

    events = await AccessRepository(session).query_events(namespace=_NS)
    # S0 / S1 / O0 / O1 / p の 5 件。
    assert events.events[0].returned_term_count == 5


@pytest.mark.integration
async def test_SELECT_はトリプル数を記録しない(session: AsyncSession) -> None:
    """**逆向きも混ぜない。** `SELECT` に `returned_triple_count` は付かない。"""
    await _setup(session)

    class _Rows(_GraphStore):
        async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
            if "deprecated" in sparql.lower():
                return {"head": {"vars": ["term"]}, "results": {"bindings": []}}
            return {
                "head": {"vars": ["s"]},
                "results": {"bindings": [{"s": {"type": "uri", "value": _BASE + "T"}}]},
            }

    await run_query(
        namespace=_NS,
        payload=SparqlQueryRequest(query="SELECT ?s WHERE { ?s ?p ?o }"),
        principal=_ANALYST,
        session=session,
        settings=_settings(),
        store=_Rows(),
        response=Response(),
    )
    await session.commit()
    events = await AccessRepository(session).query_events(namespace=_NS)
    assert events.events[0].returned_row_count == 1
    assert events.events[0].returned_triple_count is None


# ------------------------------------------------- 権限・退役・ガード


@pytest.mark.integration
async def test_権限が無ければ_403(session: AsyncSession) -> None:
    """**`SELECT` と同じ経路を通る。** RDF だけ緩くならない。"""
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _run(session, principal=_STRANGER)
    assert exc.value.status_code == 403


@pytest.mark.integration
async def test_退役中は_409(session: AsyncSession) -> None:
    """退役した名前空間では RDF も返さない(ADR-0032 決定5)。"""
    from datetime import UTC, datetime

    await _setup(session)
    await NamespaceRepository(session).set_retired(_NS, actor="a", reason="r", at=datetime.now(UTC))
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await _run(session)
    assert exc.value.status_code == 409


@pytest.mark.integration
async def test_更新操作を含む_CONSTRUCT_は_400(session: AsyncSession) -> None:
    """**ガードは RDF を返す形にも効く。** 読み取り専用の条件は変わらない。"""
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await _run(session, query="CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o } ; DELETE {}")
    assert exc.value.status_code == 400


@pytest.mark.integration
async def test_形を判定できないクエリは_400(session: AsyncSession) -> None:
    """**知らない形を推測して扱わない**(ADR-0034 決定1)。

    判定できない形を通すと「`SELECT` のつもりで扱われる」ことになる。
    """
    await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await run_query(
            namespace=_NS,
            payload=SparqlQueryRequest(query="PREFIX e: <https://e.example/c#>"),
            principal=_ANALYST,
            session=session,
            settings=_settings(),
            store=_GraphStore(),
            response=Response(),
        )
    assert exc.value.status_code == 400
    assert "判定できません" in exc.value.detail
