"""クエリガードのテスト。

ガードは多層防御の外側にすぎないが、明らかな更新操作と `SERVICE` 句を
確実に弾けることは保証する。
"""

from __future__ import annotations

import pytest

from ontology_core.sparql.guards import (
    QueryForm,
    QueryRejectedError,
    ensure_agent_safe_query,
    query_form,
)


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


# ------------------------------------------------- クエリの形の判定(P2A-14)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("SELECT ?s WHERE { ?s ?p ?o }", QueryForm.SELECT),
        ("ASK { ?s ?p ?o }", QueryForm.ASK),
        ("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", QueryForm.CONSTRUCT),
        ("DESCRIBE <urn:x>", QueryForm.DESCRIBE),
        ("PREFIX x: <urn:x#> SELECT * WHERE { ?s ?p ?o }", QueryForm.SELECT),
        ("prefix x: <urn:x#> describe x:y", QueryForm.DESCRIBE),
    ],
)
def test_クエリの形を判定する(query: str, expected: QueryForm) -> None:
    """**判定はこのモジュールにしか置かない**(ADR-0034 決定1)。

    ガードが通した形とルータが扱う形が食い違うと、**ガードを通ったクエリが
    別の経路で落ちる** — ADR-0025 決定8 が直した 502 と同じ形の不具合になる。
    """
    assert query_form(query) is expected


def test_副問い合わせの_SELECT_に引っぱられない() -> None:
    """**最初に現れた語で決める。** `CONSTRUCT { } WHERE { SELECT ... }` は
    `CONSTRUCT` である。
    """
    query = "CONSTRUCT { ?s ?p ?o } WHERE { { SELECT ?s ?p ?o WHERE { ?s ?p ?o } } }"
    assert query_form(query) is QueryForm.CONSTRUCT


def test_リテラルとコメントの中の語で判定しない() -> None:
    """**`'SELECT'` という文字列リテラルを含む `CONSTRUCT` を誤判定しない。**

    誤判定すると `Accept` と実際の応答が食い違い、502 になる。
    """
    assert query_form("# SELECT in a comment\nASK { ?s ?p ?o }") is QueryForm.ASK
    assert query_form('CONSTRUCT { ?s ?p "SELECT" } WHERE { ?s ?p ?o }') is QueryForm.CONSTRUCT


def test_判定できない形は_UNKNOWN() -> None:
    """**推測しない**(`skip:unknown-status-*` と同じ方針)。"""
    assert query_form("PREFIX x: <urn:x#>") is QueryForm.UNKNOWN
    assert query_form("何でもない文字列") is QueryForm.UNKNOWN


@pytest.mark.parametrize(
    ("form", "expected"),
    [
        (QueryForm.SELECT, False),
        (QueryForm.ASK, False),
        (QueryForm.CONSTRUCT, True),
        (QueryForm.DESCRIBE, True),
        (QueryForm.UNKNOWN, False),
    ],
)
def test_RDF_を返す形を見分ける(form: QueryForm, expected: bool) -> None:
    """`DESCRIBE` も RDF を返す。**`CONSTRUCT` だけにしない。**"""
    assert form.returns_rdf is expected


def test_判定できないクエリはガードが断る() -> None:
    """**知らない形を推測して扱わない**(ADR-0034 決定1)。

    通すと「`SELECT` のつもりで扱われる」ことになる。
    """
    with pytest.raises(QueryRejectedError, match="判定できません"):
        ensure_agent_safe_query("PREFIX x: <urn:x#>")


@pytest.mark.parametrize(
    "query",
    [
        "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
        "DESCRIBE <urn:x>",
    ],
)
def test_RDF_を返す形はガードを通る(query: str) -> None:
    """**ADR-0025 決定8 の拒否が ADR-0034 で解除された**(`P2A-14`)。

    ガードのメッセージが指していた `P2A-14` が実在するようになった。
    """
    ensure_agent_safe_query(query)


def test_RDF_を返す形でも更新操作は断る() -> None:
    """**読み取り専用の条件は変わらない。**"""
    with pytest.raises(QueryRejectedError):
        ensure_agent_safe_query("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o } ; DELETE {}")
