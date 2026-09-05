"""TTL(Turtle)の構文検証(P1-C2)。

`rdflib` は `packages/core` の依存に入っているが、修正前は使用 0 ファイルだった。
`ProjectionService.publish` は TTL を解析せずに Blob(正本)へ書いており、
壊れた TTL を投入すると次のように壊れる。

1. Blob(正本)に壊れた TTL が入る
2. その後の `put_graph` が Fuseki に拒否されて失敗するが、射影の失敗は
   握り潰す設計(不変条件3)なので呼び出し元には成功が返る
3. `reconcile` が拾って射影を試みるが、TTL が壊れているので永久に失敗し続ける
4. `P1-C1` の 409 ガード(Blob に版が残っている名前空間は削除できない)により、
   名前空間を削除して逃げることもできない

`ProjectionService.publish` はこのモジュールを**最初の Blob 書き込みより前**に
呼ぶ。位置が本質であり、`put_version` の後に置いても意味がない。
"""

from __future__ import annotations

from rdflib import Graph

__all__ = ["TurtleSyntaxError", "validate_turtle"]


class TurtleSyntaxError(ValueError):
    """TTL の構文が不正で解析できないことを表す。

    ルーター(`packages/api/src/ontology_api/routers/versions.py`)がこれを
    捕まえて 422 にマップする(`AutoVersionError` → 422 と同じ形)。
    """


def validate_turtle(text: str) -> None:
    """TTL として解析できることを確認する。

    Raises:
        TurtleSyntaxError: 解析できないとき。

    rdflib の Turtle パーサ(内部実装は notation3)は構文エラーの型が
    一貫していない。典型的な `rdflib.plugins.parsers.notation3.BadSyntax`
    に加えて、入力によっては `IndexError` や `AssertionError` を素のまま
    投げる(実測で確認済み。例: 述語だけで終わる `@prefix ex: <http://e/> .
    ex:A a` は `BadSyntax` ではなく `IndexError` になる)。特定の例外型に
    絞ると検証をすり抜けてしまうため、ここでは `Exception` を広く捕まえて
    `TurtleSyntaxError` に包み直す。
    """
    try:
        Graph().parse(data=text, format="turtle")
    except Exception as exc:
        raise TurtleSyntaxError(f"TTL の構文が不正です: {exc}") from exc
