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

from rdflib import Graph, URIRef

__all__ = [
    "TURTLE_MEDIA_TYPE",
    "TurtleSyntaxError",
    "iri_subjects",
    "term_iris_with_prefix",
    "validate_turtle",
]

#: Turtle を返すときの `Content-Type`。
#:
#: **`charset` を明示する。** 理由や表示名に日本語が入るのに、
#: `text/*` の既定の文字集合の扱いは実装によって揺れる。
#: PROV-O の書き出し(ADR-0026)とマッピングの書き出し(ADR-0031)で
#: 値が割れないよう 1 か所に置く。
TURTLE_MEDIA_TYPE = "text/turtle; charset=utf-8"


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


def iri_subjects(graph: Graph) -> set[str]:
    """主語として現れる IRI を返す。

    **「用語とは IRI の主語である」という判断をここ 1 箇所に置く。**
    空白ノードは含めない — 構造であって用語ではなく、IRI を持たないので
    参照もできない。`ontology_core.diff` と `ontology_core.health` の両方が
    この定義に依存しているので、二重に持たない。
    """
    return {str(s) for s in graph.subjects() if isinstance(s, URIRef)}


def term_iris_with_prefix(turtle: str, prefix: str) -> set[str]:
    """TTL を解析し、`prefix` を接頭辞に持つ用語 IRI を返す。

    健全性指標(ADR-0020)が「その名前空間が発行した用語」を数えるために使う。
    **`prefix` が空なら空集合を返す** — 全 IRI を数えてしまわないため
    (`base_iri` が設定されていない名前空間で `rdf:type` まで用語に数えると
    指標が意味を失う)。

    Raises:
        TurtleSyntaxError: TTL として解析できないとき。**空集合を返さない**
            (「用語が無い」と「解析できなかった」を混同しない)。
    """
    if not prefix:
        return set()
    graph = Graph()
    if turtle.strip():
        try:
            graph.parse(data=turtle, format="turtle")
        except Exception as exc:
            # rdflib は多様な例外を投げるためここで一本化する。
            raise TurtleSyntaxError(f"TTL を解析できません: {exc}") from exc
    return {iri for iri in iri_subjects(graph) if iri.startswith(prefix)}
