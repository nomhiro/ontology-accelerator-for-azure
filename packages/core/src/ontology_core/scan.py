"""ソース DB のスキーマを読む(ADR-0041、`P2A-01`)。

Phase 2 柱 A の入口である。`scan-job` がソース DB のスキーマと統計を抽出し、
PostgreSQL のカタログへ蓄積する([architecture.md](../../../../docs/architecture.md))。

## 実データを 1 行も読まない

**このカタログは `P2A-02`(LLM によるオントロジー候補生成)の入力になる。**
そこが設計の中心である。

> **サンプリングした実データは、このカタログを経て LLM のプロンプトへ流れる。**

一度渡れば、後から消しても**モデルの提供者へ渡った事実は消えない**。だから
`information_schema` と `pg_catalog` だけを読み、**ユーザーテーブルに
`SELECT` を発行しない**(ADR-0041 決定1)。

**`pg_stats` を丸ごと取らない。** `most_common_vals` / `histogram_bounds` /
`most_common_elems` には**実データの値がそのまま入る**。列を挙げて
`n_distinct` と `null_frac` だけを取る。

## 方針を検査できる形に置く

発行する SQL は `CATALOG_QUERIES` に固定してある(決定3)。テストが

- どの SQL も `information_schema` / `pg_catalog` 以外を参照していないこと
- どの SQL にも `most_common` / `histogram` が現れないこと

を機械的に検査する。**「読まないと決めた」を「読めない」に近づける。**

## 「無い」と「0」を区別する

実測して分かった 2 つの区別がある(決定2)。どちらも「測っていないものを数として
書かない」に落ちる。

| 観測 | 記録 |
|---|---|
| `reltuples = -1`、または `ANALYZE` の記録が無い | `estimated_rows = None`(**分析されていない**) |
| `reltuples = 0` かつ `ANALYZE` の記録あり | `estimated_rows = 0`(**測った 0**) |
| `n_distinct >= 0` | `distinct_estimate` に絶対数 |
| `n_distinct < 0` | **`distinct_ratio` に比率**(`-1` なら全行が異なる) |

**`n_distinct` の負の値を件数に換算しない。** 換算には行数の推定が必要で、
それが `None`(未分析)のときに**存在しない数を作る**ことになる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "CATALOG_QUERIES",
    "SUPPORTED_DRIVERS",
    "ColumnObservation",
    "ScanAuthMode",
    "ScanDriver",
    "ScanRunStatus",
    "TableObservation",
    "UnsupportedDriverError",
    "build_observations",
    "validate_host",
]


class ScanDriver(StrEnum):
    """対応しているソース DB の driver(ADR-0041 決定9)。"""

    POSTGRESQL = "postgresql"


#: 対応している driver。**ここに無いものは明示的に拒否する。**
#:
#: 黙って空のカタログを作ると「テーブルが 1 件も無い DB」として読まれる。
SUPPORTED_DRIVERS = frozenset({ScanDriver.POSTGRESQL.value})


class ScanAuthMode(StrEnum):
    """ソース DB への認証方式(ADR-0041 決定4)。

    **どちらもパスワードをカタログに保存しない。** `KEY_VAULT` は
    Key Vault の秘密名だけを持つ。
    """

    ENTRA = "entra"
    # **メンバ名に `SECRET` を入れない。** 静的解析ツールが S105
    # (ハードコードされた資格情報)として誤検知する(CLAUDE.md に記録した罠)。
    # 値は運用者が見る文字列なので `key-vault-secret` のままにする。
    KEY_VAULT = "key-vault-secret"


class ScanRunStatus(StrEnum):
    """1 回のスキャンの状態(ADR-0041 決定7)。

    **`RUNNING` のまま残った run は「完了していない観測」である。**
    読み手は `SUCCEEDED` で絞る — 半端なカタログを「テーブルが少ない DB」と
    して読ませない。
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class UnsupportedDriverError(ValueError):
    """対応していない driver を指定した(ADR-0041 決定9)。"""


class HostNotAllowedError(PermissionError):
    """`SCAN_ALLOWED_HOSTS` に無いホストへ接続しようとした(ADR-0041 決定5)。"""


#: 発行する SQL の全体(ADR-0041 決定3)。**これ以外を発行しない。**
#:
#: 検査できる形に置くための定数である。テスト
#: (`packages/core/tests/test_scan.py`)が、どの SQL も
#: `information_schema` / `pg_catalog` 以外を参照していないことと、
#: `most_common` / `histogram` が現れないことを機械的に確かめる。
#:
#: **システムのスキーマは除く。** `pg_catalog` / `information_schema` と
#: `pg_` で始まるスキーマは顧客の語彙ではない。
CATALOG_QUERIES: dict[str, str] = {
    # テーブル・ビューの一覧。行数の推定と `ANALYZE` の有無を併せて取る。
    "tables": """
        SELECT
            n.nspname AS schema_name,
            c.relname AS table_name,
            c.relkind::text AS kind,
            c.reltuples AS reltuples,
            (s.last_analyze IS NOT NULL OR s.last_autoanalyze IS NOT NULL) AS analyzed,
            obj_description(c.oid, 'pg_class') AS table_comment
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_catalog.pg_stat_all_tables s ON s.relid = c.oid
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg\\_%'
        ORDER BY n.nspname, c.relname
    """,
    # 列。**SQL 標準の `information_schema` を使う**(移植しやすい)。
    "columns": """
        SELECT
            table_schema AS schema_name,
            table_name,
            column_name,
            ordinal_position,
            data_type,
            is_nullable,
            column_default,
            character_maximum_length,
            numeric_precision,
            numeric_scale
        FROM information_schema.columns
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
          AND table_schema NOT LIKE 'pg\\_%'
        ORDER BY table_schema, table_name, ordinal_position
    """,
    # 列のコメント。**LLM がいちばん使う材料である**(意味が書いてある)。
    "column_comments": """
        SELECT
            n.nspname AS schema_name,
            c.relname AS table_name,
            a.attname AS column_name,
            col_description(a.attrelid, a.attnum) AS column_comment
        FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE a.attnum > 0
          AND NOT a.attisdropped
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg\\_%'
          AND col_description(a.attrelid, a.attnum) IS NOT NULL
        ORDER BY n.nspname, c.relname, a.attnum
    """,
    # 列の統計。**`n_distinct` と `null_frac` だけ**を挙げて取る(決定1)。
    "column_stats": """
        SELECT
            schemaname AS schema_name,
            tablename AS table_name,
            attname AS column_name,
            n_distinct,
            null_frac
        FROM pg_catalog.pg_stats
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
          AND schemaname NOT LIKE 'pg\\_%'
        ORDER BY schemaname, tablename, attname
    """,
    # 主キーと外部キー。**配列を順序付きで展開するので複合キーでも正確**である。
    "constraints": """
        SELECT
            ns.nspname AS schema_name,
            cl.relname AS table_name,
            con.conname AS constraint_name,
            con.contype::text AS constraint_type,
            k.ord AS key_position,
            att.attname AS column_name,
            fns.nspname AS referenced_schema,
            fcl.relname AS referenced_table,
            fatt.attname AS referenced_column
        FROM pg_catalog.pg_constraint con
        JOIN pg_catalog.pg_class cl ON cl.oid = con.conrelid
        JOIN pg_catalog.pg_namespace ns ON ns.oid = cl.relnamespace
        JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
        JOIN pg_catalog.pg_attribute att
          ON att.attrelid = con.conrelid AND att.attnum = k.attnum
        LEFT JOIN pg_catalog.pg_class fcl ON fcl.oid = con.confrelid
        LEFT JOIN pg_catalog.pg_namespace fns ON fns.oid = fcl.relnamespace
        LEFT JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS fk(attnum, ord)
          ON fk.ord = k.ord
        LEFT JOIN pg_catalog.pg_attribute fatt
          ON fatt.attrelid = con.confrelid AND fatt.attnum = fk.attnum
        WHERE con.contype IN ('p', 'f')
          AND ns.nspname NOT IN ('pg_catalog', 'information_schema')
          AND ns.nspname NOT LIKE 'pg\\_%'
        ORDER BY ns.nspname, cl.relname, con.conname, k.ord
    """,
}

#: `pg_class.relkind` から読める名前への対応。
#:
#: **知らない種別は生の 1 文字を残す。** 既知のどれかに丸めるより、
#: 種類を名乗らないほうが正しい(ADR-0026 決定4 と同じ判断)。
TABLE_KINDS: dict[str, str] = {
    "r": "table",
    "p": "partitioned-table",
    "v": "view",
    "m": "materialized-view",
    "f": "foreign-table",
}


@dataclass(frozen=True)
class ColumnObservation:
    """1 回のスキャンで観測した 1 列(ADR-0041 決定6)。

    Attributes:
        estimated_distinct: 異なり数の推定値。**`n_distinct >= 0` のときだけ**
            入る(決定2)。
        distinct_ratio: 行数に対する異なり数の比率。**`n_distinct < 0` の
            ときだけ**入る。`1.0` なら全行が異なる。
        null_fraction: NULL の割合。`pg_stats` に行が無ければ `None`。
        is_primary_key: 主キーの一部か。
        referenced_*: 外部キーの参照先。無ければ `None`。
    """

    schema_name: str
    table_name: str
    column_name: str
    ordinal_position: int
    data_type: str
    is_nullable: bool
    column_default: str | None = None
    character_maximum_length: int | None = None
    numeric_precision: int | None = None
    numeric_scale: int | None = None
    comment: str | None = None
    estimated_distinct: int | None = None
    distinct_ratio: float | None = None
    null_fraction: float | None = None
    is_primary_key: bool = False
    referenced_schema: str | None = None
    referenced_table: str | None = None
    referenced_column: str | None = None


@dataclass(frozen=True)
class TableObservation:
    """1 回のスキャンで観測した 1 テーブル(ADR-0041 決定6)。

    Attributes:
        kind: `table` / `view` など。**知らない `relkind` は生の 1 文字**。
        estimated_rows: 行数の推定値。**`ANALYZE` が走っていなければ `None`**
            (決定2)。`0` は「測った 0」である。
    """

    schema_name: str
    table_name: str
    kind: str
    estimated_rows: int | None = None
    comment: str | None = None
    columns: tuple[ColumnObservation, ...] = field(default_factory=tuple)


def validate_host(host: str, allowed: list[str]) -> None:
    """接続先が allowlist にあるか確かめる(ADR-0041 決定5)。

    **`allowed` が空なら必ず拒否する。** 「設定が無ければどこへでも」は
    不変条件11 が禁じている形である。任意のホストへ接続できる口は、
    認証済みの主体に**内部ネットワークの到達性を調べる手段**を与える
    (接続の成否だけで十分な情報になる)。

    Raises:
        HostNotAllowedError: allowlist に無いとき。
    """
    if host not in allowed:
        raise HostNotAllowedError(
            f"ホスト '{host}' への接続は許可されていません。"
            "SCAN_ALLOWED_HOSTS に追加してください"
            "(既定は空で、そのときスキャンは使えません)"
        )


def validate_driver(driver: str) -> ScanDriver:
    """driver が対応しているか確かめる(ADR-0041 決定9)。

    Raises:
        UnsupportedDriverError: 対応していないとき。**空のカタログを作らない。**
    """
    if driver not in SUPPORTED_DRIVERS:
        raise UnsupportedDriverError(
            f"driver '{driver}' には対応していません。"
            f"対応しているのは {sorted(SUPPORTED_DRIVERS)} です"
        )
    return ScanDriver(driver)


def _row_estimate(reltuples: float | None, analyzed: bool) -> int | None:
    """行数の推定値を返す。分析されていなければ `None`(ADR-0041 決定2)。

    **`0` を作らない。** `reltuples = 0` は「0 行」とも「まだ分析されていない」
    とも読める(PostgreSQL 14 より前は未分析でも `0` だった)。`ANALYZE` の
    記録と併せて読むことでこの曖昧さが消える。
    """
    if reltuples is None or reltuples < 0 or not analyzed:
        return None
    return int(reltuples)


def _distinct(n_distinct: float | None) -> tuple[int | None, float | None]:
    """`(絶対数, 比率)` を返す(ADR-0041 決定2)。

    PostgreSQL は `n_distinct` の**負の値を「行数に対する比率」**として使う
    (`-1` なら全行が異なる)。**絶対数に換算しない** — 換算には行数の推定が
    必要で、それが `None`(未分析)のときに存在しない数を作ることになる。
    """
    if n_distinct is None:
        return None, None
    if n_distinct < 0:
        return None, -float(n_distinct)
    return int(n_distinct), None


def _char_code(value: Any) -> str:
    """PostgreSQL の `"char"` 型を 1 文字の文字列にする。

    **実測: asyncpg は `relkind` と `contype` を `bytes` で返す。** `"char"` は
    1 バイトの内部型であってテキストではない。`str(b"r")` は `"b'r'"` になり、

    - テーブルの種別が `TABLE_KINDS` の対応から**静かに外れて** `b'r'` になり
    - **主キーと外部キーが「1 件も無い」ことになる**

    (両方とも実物の PostgreSQL に対して回して初めて分かった。フェイクの
    `dict` では再現しない)。

    SQL 側で `::text` に寄せたうえで、ここでも `bytes` を受けられるように
    してある。**「読めなかった」を「無かった」に変えないため**である。
    """
    if isinstance(value, bytes):
        return value.decode("ascii", errors="replace")
    return str(value)


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row["schema_name"]), str(row["table_name"])


def build_observations(
    *,
    tables: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    column_comments: list[dict[str, Any]],
    column_stats: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
) -> tuple[TableObservation, ...]:
    """カタログのクエリの結果を観測に組み立てる。

    **純粋関数である。** 接続もクエリもここでは行わない — `CATALOG_QUERIES` を
    実行するのは `ontology_api.services.scan` である。

    **列だけがあるテーブルを捨てない。** `information_schema.columns` に出て
    `pg_class` に出ないことは通常ないが、起きたら**捨てずに拾う**
    (観測の欠落を黙って作らない)。
    """
    comments = {
        (str(r["schema_name"]), str(r["table_name"]), str(r["column_name"])): r["column_comment"]
        for r in column_comments
    }
    stats = {
        (str(r["schema_name"]), str(r["table_name"]), str(r["column_name"])): r
        for r in column_stats
    }

    primary_keys: set[tuple[str, str, str]] = set()
    foreign_keys: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in constraints:
        key = (str(row["schema_name"]), str(row["table_name"]), str(row["column_name"]))
        constraint_type = _char_code(row["constraint_type"])
        if constraint_type == "p":
            primary_keys.add(key)
        elif constraint_type == "f":
            # **最初の宣言を採る。** 同じ列に複数の外部キーが張られることは
            # ありうるが、1 列 1 参照先として持つ形にしている(受け入れるコスト)。
            foreign_keys.setdefault(key, row)

    by_table: dict[tuple[str, str], list[ColumnObservation]] = {}
    for row in columns:
        table_key = _key(row)
        column_key = (*table_key, str(row["column_name"]))
        stat = stats.get(column_key)
        estimated_distinct, distinct_ratio = _distinct(
            None if stat is None else stat.get("n_distinct")
        )
        fk = foreign_keys.get(column_key)
        by_table.setdefault(table_key, []).append(
            ColumnObservation(
                schema_name=table_key[0],
                table_name=table_key[1],
                column_name=str(row["column_name"]),
                ordinal_position=int(row["ordinal_position"]),
                data_type=str(row["data_type"]),
                # `information_schema` は `'YES'` / `'NO'` を返す。
                is_nullable=str(row["is_nullable"]).upper() == "YES",
                column_default=row.get("column_default"),
                character_maximum_length=row.get("character_maximum_length"),
                numeric_precision=row.get("numeric_precision"),
                numeric_scale=row.get("numeric_scale"),
                comment=comments.get(column_key),
                estimated_distinct=estimated_distinct,
                distinct_ratio=distinct_ratio,
                null_fraction=None if stat is None else stat.get("null_frac"),
                is_primary_key=column_key in primary_keys,
                referenced_schema=None if fk is None else fk.get("referenced_schema"),
                referenced_table=None if fk is None else fk.get("referenced_table"),
                referenced_column=None if fk is None else fk.get("referenced_column"),
            )
        )

    observed: list[TableObservation] = []
    seen: set[tuple[str, str]] = set()
    for row in tables:
        table_key = _key(row)
        seen.add(table_key)
        kind_code = _char_code(row["kind"])
        observed.append(
            TableObservation(
                schema_name=table_key[0],
                table_name=table_key[1],
                kind=TABLE_KINDS.get(kind_code, kind_code),
                estimated_rows=_row_estimate(row.get("reltuples"), bool(row.get("analyzed"))),
                comment=row.get("table_comment"),
                columns=tuple(by_table.get(table_key, ())),
            )
        )

    # **列だけが見えたテーブルも拾う。** 欠落を黙って作らない。
    for table_key in sorted(set(by_table) - seen):
        observed.append(
            TableObservation(
                schema_name=table_key[0],
                table_name=table_key[1],
                kind="unknown",
                columns=tuple(by_table[table_key]),
            )
        )
    return tuple(observed)


#: 許されるスキーマ以外を参照していないかを検査する正規表現。
#:
#: `test_scan.py` が `CATALOG_QUERIES` に対して使う。**方針を検査できる形に
#: 置くための道具である**(ADR-0041 決定3)。
_FROM_OR_JOIN = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", re.IGNORECASE)

#: `FROM` / `JOIN` の直後に来るが関係名ではない語。
_SQL_KEYWORDS = frozenset({"LATERAL"})


def referenced_relations(sql: str) -> set[str]:
    """SQL が `FROM` / `JOIN` で参照している関係名を返す。

    **完全な SQL パーサではない。** `CATALOG_QUERIES` という固定の集合に対して
    「システムカタログ以外を参照していないか」を確かめるためだけのものである。

    `JOIN LATERAL` の `LATERAL` はキーワードなので落とす。**それ以外は
    落とさない** — 知らない名前を黙って除くと、検査が「何も見つからなかった」と
    「見なかった」を混同する。
    """
    return {
        match.group(1)
        for match in _FROM_OR_JOIN.finditer(sql)
        if match.group(1).upper() not in _SQL_KEYWORDS
    }
