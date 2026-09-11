"""エージェントに公開するクエリのガード。

外部から受け取ったクエリをストアへ渡す前に、ここで明らかに危険なものを弾く。

**この層は多層防御の外側にすぎない。** 正規表現による判定は難読化に弱く、これ単体を
信頼してはならない。権威ある制御は次の 2 つであり、そちらを必ず有効にしておくこと。

1. ストア側で `SERVICE` の実行自体を無効化する(`containers/fuseki/config.ttl`)
2. エージェント向けの経路には更新用エンドポイントを与えない(Core API のみが書き込む)
"""

from __future__ import annotations

import re
from enum import StrEnum

__all__ = ["QueryForm", "QueryRejectedError", "ensure_agent_safe_query", "query_form"]


class QueryRejectedError(ValueError):
    """クエリがガードに引っかかったことを表す。"""


# SPARQL の更新操作。読み取り専用の経路では拒否する。
_UPDATE_KEYWORDS = re.compile(
    r"\b(INSERT|DELETE|LOAD|CLEAR|CREATE|DROP|COPY|MOVE|ADD)\b",
    re.IGNORECASE,
)

# 連邦クエリ。任意の URL へリクエストを飛ばせるため SSRF の踏み台になる。
_SERVICE_KEYWORD = re.compile(r"\bSERVICE\b", re.IGNORECASE)

# クエリの形を表す語。**判定はここにしか置かない**(ADR-0034 決定1)。
#
# ガードが「通す」と決めた形とルータが「こう扱う」と決めた形が食い違うと、
# **ガードを通ったクエリが別の経路で落ちる** — ADR-0025 決定8 が直したのと
# 同じ形の不具合である(`Accept: application/sparql-results+json` を送るのに
# Fuseki が Turtle を返し、JSON の解析に失敗して 502 になっていた)。
_FORM_KEYWORDS = re.compile(r"\b(SELECT|ASK|CONSTRUCT|DESCRIBE)\b", re.IGNORECASE)

# 文字列リテラルとコメントを取り除いてからキーワードを探すための正規表現。
# リテラル内の "DELETE" のような語で誤検知しないようにする。
_LITERALS_AND_COMMENTS = re.compile(
    r'"""(?:[^\\]|\\.)*?"""'  # 三重引用符(")
    r"|'''(?:[^\\]|\\.)*?'''"  # 三重引用符(')
    r'|"(?:[^"\\\n]|\\.)*"'  # 通常の文字列(")
    r"|'(?:[^'\\\n]|\\.)*'"  # 通常の文字列(')
    r"|<[^<>\"{}|^`\\\s]*>"  # IRI
    r"|#[^\n]*",  # 行コメント
    re.DOTALL,
)


class QueryForm(StrEnum):
    """クエリの形(ADR-0034 決定1)。

    **判定はこのモジュールにしか置かない。** ガードとルータが別々に判定すると、
    ガードが通した形とルータが扱う形が食い違う。
    """

    SELECT = "select"
    ASK = "ask"
    CONSTRUCT = "construct"
    DESCRIBE = "describe"
    #: 4 つのどれでもない。**推測して扱わない。**
    UNKNOWN = "unknown"

    @property
    def returns_rdf(self) -> bool:
        """結果が RDF グラフか(`SELECT` / `ASK` は違う)。"""
        return self in (QueryForm.CONSTRUCT, QueryForm.DESCRIBE)


def query_form(query: str) -> QueryForm:
    """クエリの形を判定する(ADR-0034 決定1)。

    **リテラル・IRI・コメントを取り除いてから探す。** `'SELECT'` という
    文字列リテラルを含む `CONSTRUCT` を `SELECT` と判定してはいけない。

    **最初に現れた語で決める。** `CONSTRUCT { } WHERE { SELECT ... }`
    (副問い合わせ)の形は `CONSTRUCT` である。

    4 つのどれも見つからなければ `UNKNOWN` を返す。**推測しない。**
    """
    match = _FORM_KEYWORDS.search(_strip_noise(query))
    if match is None:
        return QueryForm.UNKNOWN
    return QueryForm(match.group(0).lower())


def _strip_noise(query: str) -> str:
    """リテラル・IRI・コメントを空白に置き換える。"""
    return _LITERALS_AND_COMMENTS.sub(" ", query)


def ensure_agent_safe_query(query: str, *, allow_service: bool = False) -> None:
    """エージェント向けの読み取り専用クエリとして妥当か検査する。

    Args:
        query: 検査する SPARQL クエリ。
        allow_service: `SERVICE` 句を許可するか。既定は禁止。運用で連邦先を
            allowlist 化できている場合にのみ有効にする。

    Raises:
        QueryRejectedError: 空のクエリ、更新操作、許可されていない `SERVICE` 句、
            またはクエリの形が判定できないとき。

    **`CONSTRUCT` / `DESCRIBE` はここでは弾かない**(ADR-0034 決定1)。
    `P2A-14` で通るようになった。呼び出し側は `query_form` で分岐する。
    """
    if not query.strip():
        raise QueryRejectedError("クエリが空です")

    body = _strip_noise(query)

    if match := _UPDATE_KEYWORDS.search(body):
        raise QueryRejectedError(
            f"更新操作 ({match.group(0).upper()}) は許可されていません。この経路は読み取り専用です"
        )

    if not allow_service and _SERVICE_KEYWORD.search(body):
        raise QueryRejectedError(
            "SERVICE 句は許可されていません。任意の URL への到達を防ぐため既定で禁止しています"
        )

    # **知らない形は通さない**(ADR-0034 決定1)。
    #
    # `CONSTRUCT` / `DESCRIBE` は `P2A-14` で通るようになった(ADR-0034)。
    # 代わりに、**4 つのどれでもないクエリを推測して扱わない** — 呼び出し側は
    # `query_form` で分岐するので、判定できない形を通すと「SELECT のつもりで
    # 扱われる」ことになる(`skip:unknown-status-*` と同じ方針)。
    if query_form(query) is QueryForm.UNKNOWN:
        raise QueryRejectedError(
            "クエリの形(SELECT / ASK / CONSTRUCT / DESCRIBE)を判定できません。"
            "この経路はこの 4 つだけを受け付けます"
        )
