"""エージェントに公開するクエリのガード。

外部から受け取ったクエリをストアへ渡す前に、ここで明らかに危険なものを弾く。

**この層は多層防御の外側にすぎない。** 正規表現による判定は難読化に弱く、これ単体を
信頼してはならない。権威ある制御は次の 2 つであり、そちらを必ず有効にしておくこと。

1. ストア側で `SERVICE` の実行自体を無効化する(`containers/fuseki/config.ttl`)
2. エージェント向けの経路には更新用エンドポイントを与えない(Core API のみが書き込む)
"""

from __future__ import annotations

import re

__all__ = ["QueryRejectedError", "ensure_agent_safe_query"]


class QueryRejectedError(ValueError):
    """クエリがガードに引っかかったことを表す。"""


# SPARQL の更新操作。読み取り専用の経路では拒否する。
_UPDATE_KEYWORDS = re.compile(
    r"\b(INSERT|DELETE|LOAD|CLEAR|CREATE|DROP|COPY|MOVE|ADD)\b",
    re.IGNORECASE,
)

# 連邦クエリ。任意の URL へリクエストを飛ばせるため SSRF の踏み台になる。
_SERVICE_KEYWORD = re.compile(r"\bSERVICE\b", re.IGNORECASE)

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
        QueryRejectedError: 更新操作、または許可されていない `SERVICE` 句を含むとき。
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
