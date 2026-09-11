"""結果件数の上限の API 経路(ADR-0025、`P2A-08`)。

**この経路が唯一の強制点である。** Fuseki 6.2.0 に行数の上限が無いことを
実測した(`fuseki:queryLimit` は語彙にあるが実装に読み手がおらず、設定しても
効かない)。ここを外すと `SPARQL_MAX_RESULTS` は再び「保持しているだけ」に
戻る。

ここで固定するのは 4 つである。

1. **上限を超えたら切り詰める**
2. **切り詰めたことをヘッダで必ず知らせる**(決定4)
3. **アクセスログには切り詰める前の行数を記録する**(決定6)
4. **`CONSTRUCT` / `DESCRIBE` はこの上限を通らない**(ADR-0034 決定4 で
   トリプル数の上限に分かれた。決定8 の 400 は解除された)
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.access import AccessRepository
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.roles import RoleRepository
from ontology_api.routers.sparql import (
    RESULT_LIMIT_HEADER,
    RESULT_TOTAL_ROWS_HEADER,
    RESULT_TRUNCATED_HEADER,
    SparqlQueryRequest,
    run_query,
)
from ontology_core.auth.entra import Principal
from ontology_core.config import AuthMode, Settings
from ontology_core.models import NamespaceRole, PlatformRole
from ontology_core.sparql.client import SparqlStore

_NS = "limits-ns"
_BASE = "https://e.example/#"

_ADMIN = Principal(
    subject="admin", object_id="admin-oid", platform_roles=(PlatformRole.PLATFORM_ADMIN.value,)
)
_ANALYST = Principal(subject="analyst", object_id="analyst-oid")


class _RowStore(SparqlStore):
    """指定した行数の `SELECT` 結果を返すストア。

    **廃止済み用語の問い合わせもここを通る**(`deprecated_terms_in_dataset`)。
    そちらには 0 行を返し、上限の検査に影響させない。
    """

    def __init__(self, rows: int) -> None:
        self._rows = rows
        self.queries: list[str] = []

    async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
        self.queries.append(sparql)
        if "owl:deprecated" in sparql or "deprecated" in sparql.lower():
            return {"head": {"vars": ["term"]}, "results": {"bindings": []}}
        bindings = [{"s": {"type": "uri", "value": f"{_BASE}T{i}"}} for i in range(self._rows)]
        return {"head": {"vars": ["s"]}, "results": {"bindings": bindings}}

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


def _settings(limit: int) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        AUTH_MODE=AuthMode.DISABLED,
        SPARQL_MAX_RESULTS=limit,
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
    session: AsyncSession, *, rows: int, limit: int, query: str = "SELECT ?s WHERE { ?s ?p ?o }"
) -> tuple[dict[str, Any], Response]:
    response = Response()
    result = await run_query(
        namespace=_NS,
        payload=SparqlQueryRequest(query=query),
        principal=_ANALYST,
        session=session,
        settings=_settings(limit),
        store=_RowStore(rows),
        response=response,
    )
    # **`run_query` の戻り値は `dict | Response` である**(ADR-0034 決定3)。
    # ここは `SELECT` / `ASK` の経路なので `dict` に絞る。**絞ったことを
    # 明示する** — `Response` が来ていたら経路の取り違えである。
    assert isinstance(result, dict), "SELECT の経路が Response を返している"
    return result, response


# ---------------------------------------------------------------- 切り詰め


@pytest.mark.integration
async def test_上限以下ならヘッダは付かない(session: AsyncSession) -> None:
    await _setup(session)
    result, response = await _run(session, rows=5, limit=10)
    assert len(result["results"]["bindings"]) == 5
    assert RESULT_TRUNCATED_HEADER not in response.headers


@pytest.mark.integration
async def test_上限を超えたら切り詰めてヘッダで知らせる(session: AsyncSession) -> None:
    """**黙って切らない**(ADR-0025 決定3)。

    切り詰めた結果を「全部です」として返すと、エージェントは
    「該当は N 件で全部見た」と信じて回答を作る。
    """
    await _setup(session)
    result, response = await _run(session, rows=25, limit=10)
    assert len(result["results"]["bindings"]) == 10
    assert response.headers[RESULT_TRUNCATED_HEADER] == "true"
    assert response.headers[RESULT_LIMIT_HEADER] == "10"
    # **「本当は何行あったか」も返す。** これが無いと「ちょうど上限だった」のか
    # 「もっとあった」のか区別できない。
    assert response.headers[RESULT_TOTAL_ROWS_HEADER] == "25"


@pytest.mark.integration
async def test_上限ちょうどは切り詰めない(session: AsyncSession) -> None:
    await _setup(session)
    result, response = await _run(session, rows=10, limit=10)
    assert len(result["results"]["bindings"]) == 10
    assert RESULT_TRUNCATED_HEADER not in response.headers


@pytest.mark.integration
async def test_上限を超えてもエラーにしない(session: AsyncSession) -> None:
    """**エージェントは「全部は取れなかった」ことを知って続けられるべき**(決定5)。

    413 / 422 で拒否すると回答が作れない。
    """
    await _setup(session)
    result, _ = await _run(session, rows=1000, limit=1)
    assert result["results"]["bindings"]  # 結果は返る
    assert len(result["results"]["bindings"]) == 1


@pytest.mark.integration
async def test_ASK_は切り詰めの対象外(session: AsyncSession) -> None:
    await _setup(session)

    class _AskStore(_RowStore):
        async def query(self, sparql: str, *, dataset: str) -> dict[str, Any]:
            if "deprecated" in sparql.lower():
                return {"head": {"vars": ["term"]}, "results": {"bindings": []}}
            return {"head": {}, "boolean": True}

    response = Response()
    result = await run_query(
        namespace=_NS,
        payload=SparqlQueryRequest(query="ASK { ?s ?p ?o }"),
        principal=_ANALYST,
        session=session,
        settings=_settings(1),
        store=_AskStore(0),
        response=response,
    )
    assert isinstance(result, dict), "ASK の経路が Response を返している"
    assert result["boolean"] is True
    assert RESULT_TRUNCATED_HEADER not in response.headers


# ------------------------------------------------------------ アクセスログ


@pytest.mark.integration
async def test_アクセスログには切り詰める前の行数を記録する(session: AsyncSession) -> None:
    """**「何行返ろうとしたか」を記録する**(ADR-0025 決定6)。

    「エージェントに何行渡したか」を記録すると、**上限に張り付いているクエリが
    上限ちょうどの行数として並ぶだけ**になり、見つけられない。
    """
    await _setup(session)
    await _run(session, rows=25, limit=10)
    await session.commit()

    events = await AccessRepository(session).query_events(namespace=_NS)
    assert len(events.events) == 1
    assert events.events[0].returned_row_count == 25, (
        "切り詰めた後の行数(10)を記録すると、上限に張り付いているクエリを見つけられない"
    )


# --------------------------------------------------- CONSTRUCT / DESCRIBE


@pytest.mark.integration
@pytest.mark.parametrize(
    "query",
    [
        "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
        "DESCRIBE <https://e.example/#T0>",
    ],
)
async def test_RDF_を返す形は行数の上限を通らない(session: AsyncSession, query: str) -> None:
    """**ADR-0025 決定8 の 400 は ADR-0034(`P2A-14`)で解除された。**

    以前は「この経路では扱えません」と 400 で断っていた。いまは
    `text/turtle` が返り、**行数ではなくトリプル数の上限**
    (`SPARQL_MAX_TRIPLES`)が効く(ADR-0034 決定4)。

    ここで固定するのは「**行数の上限の経路を通らない**」ことである。
    行数の上限をトリプル数に流用すると、1 行が何トリプルにもなる
    `CONSTRUCT` で実質の上限が変わってしまう。
    """
    await _setup(session)
    response = Response()
    result = await run_query(
        namespace=_NS,
        payload=SparqlQueryRequest(query=query),
        principal=_ANALYST,
        session=session,
        settings=_settings(1),  # 行数の上限は 1 だが、RDF には効かない
        store=_RowStore(25),
        response=response,
    )
    assert not isinstance(result, dict), "RDF を返す形が JSON を返している"
    assert result.media_type is not None
    assert result.media_type.startswith("text/turtle")
    # 行数の上限のヘッダは付かない。
    assert RESULT_TRUNCATED_HEADER not in response.headers


# ---------------------------------------------------------------- 設定の検証


def test_上限は_1_以上に限る() -> None:
    """**「0 なら無制限」という解釈を作らない**(ADR-0025 決定7)。

    この設定の目的は上限をかけることなので、0 を無制限と読む余地を残すのは
    この ADR が直している問題(効いていない設定)の別の形である。
    """
    for bad in (0, -1):
        with pytest.raises(ValueError):
            _settings(bad)
    assert _settings(1).sparql_max_results == 1
