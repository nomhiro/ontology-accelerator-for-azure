"""ソース DB のスキャン(ADR-0041、`P2A-01`)。

**このファイルでいちばん重要なのは `test_実データを読む_SQL_を発行しない` である。**

カタログは `P2A-02`(LLM によるオントロジー候補生成)の入力になる。
サンプリングした実データが一度そこへ入れば、後から消しても**モデルの提供者へ
渡った事実は消えない**。だから方針をコメントで書くのではなく、
**発行する SQL の集合に対して機械的に検査する**(決定3)。

ここで固定するのはもう 3 つある。

1. **「無い」と「0」を区別する**(決定2)。`reltuples = -1` は「未分析」、
   `n_distinct < 0` は**比率**である
2. **allowlist に無いホストを拒否する**(決定5)。**既定は空**
3. **対応外の driver を拒否する**(決定9)。空のカタログを作らない
"""

from __future__ import annotations

from typing import Any

import pytest

from ontology_core.scan import (
    CATALOG_QUERIES,
    SUPPORTED_DRIVERS,
    HostNotAllowedError,
    ScanAuthMode,
    ScanRunStatus,
    UnsupportedDriverError,
    build_observations,
    referenced_relations,
    validate_driver,
    validate_host,
)

#: 読んでよいスキーマ。**ここに無いものを参照する SQL は書けない。**
_ALLOWED_SCHEMAS = {"pg_catalog", "information_schema"}

#: `pg_stats` の列のうち**実データが入るもの**。
#:
#: `most_common_vals` / `histogram_bounds` / `most_common_elems` には
#: 値がそのまま入る。**列を挙げて取らないと、方針を書いただけで実データを
#: 取り込むことになる。**
_DATA_BEARING = ("most_common", "histogram")


# ------------------- 実データを読まない(決定1・3)


@pytest.mark.parametrize("name", sorted(CATALOG_QUERIES))
def test_実データを読む_SQL_を発行しない(name: str) -> None:
    """**このファイルの中心である**(ADR-0041 決定3)。

    カタログは LLM のプロンプトへ流れる。**ユーザーテーブルを参照する SQL が
    1 つでも混ざれば、顧客の実データが渡る。**

    方針を検査できる形に置くために、発行する SQL を `CATALOG_QUERIES` に
    固定してある。
    """
    sql = CATALOG_QUERIES[name]
    for relation in referenced_relations(sql):
        schema = relation.split(".")[0]
        assert schema in _ALLOWED_SCHEMAS, (
            f"{name} がシステムカタログ以外を参照している: {relation}"
        )


@pytest.mark.parametrize("name", sorted(CATALOG_QUERIES))
def test_値が入る統計の列を取らない(name: str) -> None:
    """**`pg_stats` を丸ごと取らない**(ADR-0041 決定1)。

    `most_common_vals` には**実データの値がそのまま入る**。
    """
    sql = CATALOG_QUERIES[name].lower()
    for banned in _DATA_BEARING:
        assert banned not in sql, f"{name} が {banned} を取っている(実データである)"


@pytest.mark.parametrize("name", sorted(CATALOG_QUERIES))
def test_読み取りだけを発行する(name: str) -> None:
    """**書き込みの SQL を混ぜない。** スキャンは読み取りである。"""
    sql = CATALOG_QUERIES[name].upper()
    for banned in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "CREATE ", "ALTER ", "GRANT "):
        assert banned not in sql, f"{name} が {banned.strip()} を含んでいる"


@pytest.mark.parametrize("name", sorted(CATALOG_QUERIES))
def test_システムのスキーマを除いている(name: str) -> None:
    """**顧客の語彙だけを拾う。** `pg_catalog` のテーブルを「顧客のテーブル」
    として LLM に渡さない。
    """
    assert "NOT IN ('pg_catalog', 'information_schema')" in CATALOG_QUERIES[name], name


def test_関係名の抽出はキーワードを落とす() -> None:
    """`JOIN LATERAL` の `LATERAL` は関係名ではない。

    `LATERAL` の次に来る関数呼び出し(`unnest(...)`)は `FROM` / `JOIN` の
    直後ではないので拾わない。**関係ではないので拾う必要も無い。**
    """
    assert referenced_relations("SELECT 1 FROM a JOIN LATERAL unnest(x) AS t ON TRUE") == {"a"}


def test_入れ子の_FROM_も拾う() -> None:
    """**これが検査の効き目を決める。**

    `JOIN LATERAL (SELECT ... FROM customers)` のように副問い合わせで
    ユーザーテーブルを読む書き方を見逃すと、検査が意味を失う。
    """
    sql = "SELECT 1 FROM pg_catalog.pg_class JOIN LATERAL (SELECT x FROM customers) q ON TRUE"
    assert referenced_relations(sql) == {"pg_catalog.pg_class", "customers"}


# ------------------- 「無い」と「0」を区別する(決定2)


def _table(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_name": "public",
        "table_name": "orders",
        "kind": "r",
        "reltuples": 10.0,
        "analyzed": True,
        "table_comment": None,
    }
    return {**base, **kwargs}


def _column(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_name": "public",
        "table_name": "orders",
        "column_name": "id",
        "ordinal_position": 1,
        "data_type": "integer",
        "is_nullable": "NO",
        "column_default": None,
        "character_maximum_length": None,
        "numeric_precision": 32,
        "numeric_scale": 0,
    }
    return {**base, **kwargs}


def _build(**kwargs: Any) -> tuple[Any, ...]:
    defaults: dict[str, Any] = {
        "tables": [_table()],
        "columns": [_column()],
        "column_comments": [],
        "column_stats": [],
        "constraints": [],
    }
    return build_observations(**{**defaults, **kwargs})


@pytest.mark.parametrize(
    ("reltuples", "analyzed", "expected"),
    [
        (-1.0, False, None),
        (-1.0, True, None),
        (0.0, False, None),
        (0.0, True, 0),
        (42.0, True, 42),
        (42.0, False, None),
    ],
)
def test_行数は分析されていなければ_None(
    reltuples: float, analyzed: bool, expected: int | None
) -> None:
    """**`0` を作らない**(ADR-0041 決定2)。

    `reltuples = 0` は「0 行」とも「まだ分析されていない」とも読める
    (PostgreSQL 14 より前は未分析でも `0` だった)。`ANALYZE` の記録と
    併せて読むことでこの曖昧さが消える。

    **実測でこの製品自身のテーブルのほとんどが `-1` だった。**
    """
    observed = _build(tables=[_table(reltuples=reltuples, analyzed=analyzed)])
    assert observed[0].estimated_rows == expected


@pytest.mark.parametrize(
    ("n_distinct", "estimated", "ratio"),
    [
        (5.0, 5, None),
        (0.0, 0, None),
        (-1.0, None, 1.0),
        (-0.5, None, 0.5),
        (None, None, None),
    ],
)
def test_n_distinct_の負の値は比率として持つ(
    n_distinct: float | None, estimated: int | None, ratio: float | None
) -> None:
    """**絶対数に換算しない**(ADR-0041 決定2)。

    PostgreSQL は `n_distinct` の負の値を「行数に対する比率」として使う。
    換算には行数の推定が必要で、それが `None`(未分析)のときに
    **存在しない数を作る**ことになる。

    **`-1` を件数として読めば「異なり数は -1 件」という無意味な値になる。**
    実測で `alembic_version.version_num` が `-1` だった。
    """
    stats = (
        []
        if n_distinct is None
        else [
            {
                "schema_name": "public",
                "table_name": "orders",
                "column_name": "id",
                "n_distinct": n_distinct,
                "null_frac": 0.0,
            }
        ]
    )
    column = _build(column_stats=stats)[0].columns[0]
    assert column.estimated_distinct == estimated
    assert column.distinct_ratio == ratio


def test_統計が無ければ_NULL_率も_None() -> None:
    """**`0.0` を作らない。** 「NULL が無い」と「測っていない」は違う。"""
    assert _build()[0].columns[0].null_fraction is None


# ------------------- 観測の組み立て


def test_主キーと外部キーを列に付ける() -> None:
    constraints = [
        {
            "schema_name": "public",
            "table_name": "orders",
            "constraint_name": "orders_pkey",
            "constraint_type": "p",
            "key_position": 1,
            "column_name": "id",
            "referenced_schema": None,
            "referenced_table": None,
            "referenced_column": None,
        },
        {
            "schema_name": "public",
            "table_name": "orders",
            "constraint_name": "orders_customer_fkey",
            "constraint_type": "f",
            "key_position": 1,
            "column_name": "customer_id",
            "referenced_schema": "public",
            "referenced_table": "customers",
            "referenced_column": "id",
        },
    ]
    observed = _build(
        columns=[_column(), _column(column_name="customer_id", ordinal_position=2)],
        constraints=constraints,
    )
    columns = {c.column_name: c for c in observed[0].columns}
    assert columns["id"].is_primary_key
    assert not columns["customer_id"].is_primary_key
    assert columns["customer_id"].referenced_table == "customers"
    assert columns["customer_id"].referenced_column == "id"


def test_列のコメントを拾う() -> None:
    """**LLM がいちばん使う材料である**(意味が書いてある)。"""
    comments = [
        {
            "schema_name": "public",
            "table_name": "orders",
            "column_name": "id",
            "column_comment": "注文の識別子",
        }
    ]
    assert _build(column_comments=comments)[0].columns[0].comment == "注文の識別子"


def test_知らない_relkind_は生の_1_文字を残す() -> None:
    """**既知のどれかに丸めない**(ADR-0026 決定4 と同じ判断)。"""
    assert _build(tables=[_table(kind="z")])[0].kind == "z"
    assert _build(tables=[_table(kind="v")])[0].kind == "view"


def test_bytes_で来た_char_型を読める() -> None:
    """**実測で踏んだ回帰**(`P2A-01`)。

    `pg_class.relkind` と `pg_constraint.contype` は PostgreSQL の `"char"` 型
    (1 バイトの内部型)で、**asyncpg はこれを `bytes` で返す**。`str(b"r")` は
    `"b'r'"` になるため、

    - テーブルの種別が `TABLE_KINDS` の対応から**静かに外れて** `b'r'` になり
    - **主キーと外部キーが「1 件も無い」ことになる**

    後者が悪い。**「読めなかった」が「無かった」として通る**からである。
    実物の PostgreSQL に対して回して初めて分かった(フェイクの `dict` では
    再現しない)。SQL 側で `::text` に寄せたうえで、ここでも `bytes` を
    受けられるようにしてある。
    """
    constraints = [
        {
            "schema_name": "public",
            "table_name": "orders",
            "constraint_name": "orders_pkey",
            "constraint_type": b"p",
            "key_position": 1,
            "column_name": "id",
            "referenced_schema": None,
            "referenced_table": None,
            "referenced_column": None,
        },
        {
            "schema_name": "public",
            "table_name": "orders",
            "constraint_name": "orders_customer_fkey",
            "constraint_type": b"f",
            "key_position": 1,
            "column_name": "customer_id",
            "referenced_schema": "public",
            "referenced_table": "customers",
            "referenced_column": "id",
        },
    ]
    observed = _build(
        tables=[_table(kind=b"r")],
        columns=[_column(), _column(column_name="customer_id", ordinal_position=2)],
        constraints=constraints,
    )
    assert observed[0].kind == "table"
    columns = {c.column_name: c for c in observed[0].columns}
    assert columns["id"].is_primary_key
    assert columns["customer_id"].referenced_table == "customers"


def test_is_nullable_は_YES_NO_を真偽に直す() -> None:
    """`information_schema` は `'YES'` / `'NO'` を返す。"""
    assert not _build(columns=[_column(is_nullable="NO")])[0].columns[0].is_nullable
    assert _build(columns=[_column(is_nullable="YES")])[0].columns[0].is_nullable


def test_列だけが見えたテーブルも拾う() -> None:
    """**観測の欠落を黙って作らない。**

    `information_schema.columns` に出て `pg_class` に出ないことは通常ないが、
    起きたら捨てずに拾う。
    """
    observed = _build(tables=[], columns=[_column(table_name="ghost")])
    assert [t.table_name for t in observed] == ["ghost"]
    assert observed[0].kind == "unknown"


def test_テーブルが無ければ空を返す() -> None:
    assert _build(tables=[], columns=[]) == ()


# ------------------- 接続先と driver(決定5・9)


def test_allowlist_が空なら必ず拒否する() -> None:
    """**「設定が無ければどこへでも」にしない**(ADR-0041 決定5)。

    任意のホストへ接続できる口は、認証済みの主体に**内部ネットワークの
    到達性を調べる手段**を与える(接続の成否だけで十分な情報になる)。
    SPARQL の `SERVICE` を既定で禁止したのと同じ判断である。
    """
    with pytest.raises(HostNotAllowedError):
        validate_host("db.example.internal", [])


def test_allowlist_にあれば通す() -> None:
    validate_host("db.example.internal", ["db.example.internal", "other.example"])


def test_部分一致では通さない() -> None:
    """**接頭辞や部分文字列で許してはいけない。** `evil-db.example.internal`
    が `db.example.internal` の allowlist で通ってはならない。
    """
    with pytest.raises(HostNotAllowedError):
        validate_host("evil-db.example.internal", ["db.example.internal"])
    with pytest.raises(HostNotAllowedError):
        validate_host("db.example.internal.evil.example", ["db.example.internal"])


def test_対応外の_driver_は拒否する() -> None:
    """**黙って空のカタログを作らない**(ADR-0041 決定9)。

    「テーブルが 1 件も無い DB」として読まれる。
    """
    for driver in ("mysql", "sqlserver", "oracle", "", "POSTGRESQL"):
        with pytest.raises(UnsupportedDriverError):
            validate_driver(driver)


def test_対応している_driver_は_1_つである() -> None:
    """広げるときは ADR-0041 決定9 を読むこと(統計は方言である)。"""
    assert SUPPORTED_DRIVERS == {"postgresql"}
    assert validate_driver("postgresql").value == "postgresql"


# ------------------- 語彙


def test_認証方式は_2_つである() -> None:
    """**どちらもパスワードをカタログに保存しない**(ADR-0041 決定4)。"""
    assert {mode.value for mode in ScanAuthMode} == {"entra", "key-vault-secret"}


def test_run_の状態は_3_つである() -> None:
    """**`running` のまま残った run は「完了していない観測」である**(決定7)。"""
    assert {s.value for s in ScanRunStatus} == {"running", "succeeded", "failed"}
