"""TTL(Turtle)の構文検証(P1-C2)。

`ProjectionService.publish` が Blob(正本)へ書く前に呼ぶ検証そのもの。
ここではストア・DB を介さない純粋なパース挙動だけを確認する
(`packages/api/tests/test_projection.py` / `test_versions_router.py` が
publish 経路への組み込みを確認する)。
"""

from __future__ import annotations

import pytest

from ontology_core.turtle import TurtleSyntaxError, validate_turtle

VALID_TTL = "@prefix ex: <https://e.example/#> .\nex:A a ex:Class .\n"


def test_valid_turtle_is_accepted() -> None:
    validate_turtle(VALID_TTL)  # 例外が起きなければ成功


@pytest.mark.parametrize(
    "broken",
    [
        # 述語だけで終わる。実測では rdflib は BadSyntax ではなく IndexError を
        # 投げる(ブリーフに挙げられた例そのもの)。
        "@prefix ex: <http://e/> . ex:A a",
        # 角括弧が閉じていない。rdflib.plugins.parsers.notation3.BadSyntax の経路。
        "@prefix ex: <http://e/> .\nex:A ex:p [ ex:q ex:r .",
        # TTL として全く体をなしていない。
        "this is not turtle at all !!! ###",
        # 文字列リテラルが閉じていない。実測では AssertionError の経路。
        '@prefix ex: <http://e/> .\nex:A ex:p "unterminated .',
    ],
)
def test_broken_turtle_is_rejected(broken: str) -> None:
    """rdflib は構文エラーの型が一貫していない(BadSyntax・IndexError・
    AssertionError のいずれも投げる、実測で確認済み)。型を絞ると検証を
    すり抜けるため、どの経路でも TurtleSyntaxError に正規化されることを確認する。
    """
    with pytest.raises(TurtleSyntaxError):
        validate_turtle(broken)
