"""クエリガードのテスト。

ガードは多層防御の外側にすぎないが、明らかな更新操作と `SERVICE` 句を
確実に弾けることは保証する。
"""

from __future__ import annotations

import pytest

from ontology_core.sparql.guards import QueryRejectedError, ensure_agent_safe_query


def test_select_is_allowed() -> None:
    ensure_agent_safe_query("SELECT ?s WHERE { ?s ?p ?o } LIMIT 10")


def test_ask_is_allowed() -> None:
    ensure_agent_safe_query("ASK { ?s a <http://example.org/Thing> }")


@pytest.mark.parametrize(
    "query",
    [
        "INSERT DATA { <http://example.org/a> <http://example.org/b> 1 }",
        "DELETE WHERE { ?s ?p ?o }",
        "LOAD <http://example.org/data.ttl>",
        "DROP GRAPH <http://example.org/g>",
        "CLEAR DEFAULT",
    ],
)
def test_update_operations_are_rejected(query: str) -> None:
    with pytest.raises(QueryRejectedError):
        ensure_agent_safe_query(query)


def test_service_clause_is_rejected_by_default() -> None:
    query = "SELECT ?s WHERE { SERVICE <http://169.254.169.254/metadata> { ?s ?p ?o } }"
    with pytest.raises(QueryRejectedError, match="SERVICE"):
        ensure_agent_safe_query(query)


def test_service_clause_can_be_allowed_explicitly() -> None:
    query = "SELECT ?s WHERE { SERVICE <http://trusted.example/sparql> { ?s ?p ?o } }"
    ensure_agent_safe_query(query, allow_service=True)


def test_keyword_inside_string_literal_is_not_a_false_positive() -> None:
    # リテラル内の "DELETE" で誤検知しないこと。
    ensure_agent_safe_query('SELECT ?s WHERE { ?s <http://example.org/note> "DELETE me" }')


def test_keyword_inside_comment_is_not_a_false_positive() -> None:
    ensure_agent_safe_query("# INSERT DATA is mentioned here\nSELECT ?s WHERE { ?s ?p ?o }")


def test_empty_query_is_rejected() -> None:
    with pytest.raises(QueryRejectedError):
        ensure_agent_safe_query("   ")
