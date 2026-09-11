"""RDF を JSON-LD として返す(ADR-0036、`P2A-17`)。

[ADR-0026](../../../../docs/adr/0026-provenance-export.md) の代替案は JSON-LD を
挙げて却下していた。理由は「**`@context` をどこで公開するか**という新しい問題が
生まれる」ことだった(IRI が `urn:` で dereferenceable ではない)。

## その問題は「公開しない」ことで消える

**`@context` は文書に埋め込む**(ADR-0036 決定2)。JSON-LD 1.1 の正規の形であり、
文書が自己完結する。外に置く案はいずれも「解決できる URL を名乗っているのに
解決できない」形になる。

- **この API のコンテキスト URL は認証が要る。** JSON-LD プロセッサは
  Bearer トークンを持たないので取得できない
- **URL はデプロイごとに違う。** 書き出した文書を第三者に渡した時点で壊れる。
  内部 ingress に置いた環境では最初から解決できない
- **リポジトリの raw URL に置くと、文書の意味がリポジトリの可用性に依存する。**
  移転や改名で過去の書き出しが読めなくなる

## 用語の別名を作らない

キーは `ont:action` / `prov:generated` のままにする(決定3)。別名を作ると
**Turtle の述語名と JSON のキー名という 2 つの語彙**を同期させ続けることに
なる([ADR-0027](../../../../docs/adr/0027-revision-lineage.md) の
「名前が 2 つになる」と同じ形)。別名が買うのは見やすさだけである。

## 使っていない語彙を並べない

**rdflib の既定の接頭辞表を出さない**(決定4)。既定では Brick / GeoSPARQL /
SOSA など**その文書に一切現れない語彙が 30 件**並ぶ。「この文書はそれらに
ついて何か言っている」と読める余地を作らない。

## `Accept` の解釈は既定を壊さない

**既定は Turtle である**(決定5)。`application/ld+json` を明示したときだけ
切り替える。`application/json` では切り替えない(既存のクライアントの
振る舞いを変えないため)。**406 も返さない** — いまは `Accept` を無視して
いるので、406 にすると狭い `Accept` を送る既存クライアントが壊れる。

**`q=0` は「受け付けない」である。** 部分文字列一致で判定すると
`Accept: application/ld+json;q=0` に JSON-LD を返す。**解釈できない
`Accept` は既定に落とす** — 迷ったら既存の振る舞いを変えない側に寄せる。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from rdflib import Graph
from rdflib.namespace import PROV, RDFS, SKOS, XSD

from ontology_core.mapping import MAPPING_ONT_NAMESPACE
from ontology_core.prov import ONT

__all__ = [
    "JSONLD_MEDIA_TYPE",
    "MAPPING_CONTEXT",
    "PROVENANCE_CONTEXT",
    "prefers_jsonld",
    "render_jsonld",
]

#: JSON-LD を返すときの `Content-Type`。
#:
#: **`charset` を付けない。** `application/*` の既定は UTF-8 と定まっている
#: (`text/*` と違って揺れない)。
JSONLD_MEDIA_TYPE = "application/ld+json"

#: `Accept` の突き合わせに使う Turtle の型。
#:
#: `ontology_core.turtle.TURTLE_MEDIA_TYPE` は `charset` を含むので、
#: ヘッダの照合にはそのままでは使えない。
_TURTLE_TYPE = "text/turtle"

#: 監査証跡(PROV-O)の書き出しに埋め込むコンテキスト(ADR-0036 決定2)。
#:
#: **実際に出る語彙だけを並べる**(決定4)。`rdf` は `@type` として
#: JSON-LD が自前で扱うので要らない。
PROVENANCE_CONTEXT: Mapping[str, str] = {
    "ont": str(ONT),
    "prov": str(PROV),
    "rdfs": str(RDFS),
    "xsd": str(XSD),
}

#: マッピングの書き出しに埋め込むコンテキスト。
MAPPING_CONTEXT: Mapping[str, str] = {
    "ont": MAPPING_ONT_NAMESPACE,
    "rdfs": str(RDFS),
    "skos": str(SKOS),
    "xsd": str(XSD),
}


def render_jsonld(graph: Graph, *, context: Mapping[str, str]) -> str:
    """グラフを JSON-LD にする。**`@context` は埋め込む。**

    **形は常に `@graph` の配列である**(決定7)。rdflib は**主語が
    ちょうど 1 つのときだけ**平らなノードオブジェクトを返す(空のグラフには
    `"@graph": []` を出す。実測)。形が入力で変わる契約はクライアント側に
    分岐を強いるので、ここで揃える。

    Args:
        graph: 直列化するグラフ。
        context: 埋め込む `@context`(接頭辞だけ。決定3)。

    Returns:
        JSON-LD。
    """
    # **`ensure_ascii` はここでは指定しない。** 下の `json.dumps` が最終の
    # 出力を決めるので、ここで渡しても効かない(渡すと「効いている」と
    # 読める死んだ設定になる。変異テストで気づいた)。
    serialized = graph.serialize(
        format="json-ld",
        context=dict(context),
        auto_compact=True,
    )
    text = serialized if isinstance(serialized, str) else serialized.decode("utf-8")
    document: dict[str, Any] = json.loads(text)
    if "@graph" not in document:
        # **ここに来るのは主語がちょうど 1 つのときだけである。** 空のグラフには
        # rdflib 自身が `"@graph": []` を出す(実測)。したがってノードは
        # 必ず空でない — 「空なら空配列」という到達しない分岐は置かない。
        node = {key: value for key, value in document.items() if key != "@context"}
        document = {
            "@context": document.get("@context", dict(context)),
            "@graph": [node],
        }
    # **日本語をエスケープしない。** 理由や表示名に日本語が入る。
    # `\uXXXX` でも正しい JSON だが、運用者が目で読めなくなる。
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)


def _quality(accept: str, media_type: str) -> float:
    """`Accept` における `media_type` の品質値を返す。一致しなければ `0`。

    より具体的な範囲が勝つ(`text/turtle` > `text/*` > `*/*`)。RFC 9110 の
    完全な実装ではない — **見るのは 2 つの型だけ**である。

    Raises:
        ValueError: `q` の値が数として読めないとき。呼び出し側は
            **既定に落とす**(決定5)。
    """
    wanted_type, _, wanted_subtype = media_type.partition("/")
    best: tuple[int, float] | None = None
    for part in accept.split(","):
        tokens = part.split(";")
        media_range = tokens[0].strip().lower()
        if not media_range:
            continue
        quality = 1.0
        for token in tokens[1:]:
            name, _, value = token.partition("=")
            if name.strip().lower() != "q":
                continue
            try:
                quality = float(value.strip())
            except ValueError as exc:
                raise ValueError(f"q の値が読めません: {value!r}") from exc
            if not 0.0 <= quality <= 1.0:
                raise ValueError(f"q が範囲外です: {quality}")
            break
        range_type, _, range_subtype = media_range.partition("/")
        if (range_type, range_subtype) == (wanted_type, wanted_subtype):
            specificity = 2
        elif (range_type, range_subtype) == (wanted_type, "*"):
            specificity = 1
        elif (range_type, range_subtype) == ("*", "*"):
            specificity = 0
        else:
            continue
        if best is None or specificity > best[0]:
            best = (specificity, quality)
        elif specificity == best[0]:
            best = (specificity, max(best[1], quality))
    return 0.0 if best is None else best[1]


def prefers_jsonld(accept: str | None) -> bool:
    """`Accept` が JSON-LD を Turtle より望んでいるか(ADR-0036 決定5)。

    **既定は Turtle である。** 同じ品質値なら Turtle を返す — 内容交渉は
    **足すだけ**にして、既存のクライアントの振る舞いを変えない。

    - `application/ld+json` → `True`
    - `*/*` / 空 / ヘッダ無し → `False`(既定)
    - `application/json` → `False`(**既存の振る舞いを変えない**)
    - `application/ld+json;q=0` → `False`(**明示的な拒否を尊重する**)
    - 解釈できない `q` → `False`(既定に落とす)
    """
    if not accept:
        return False
    try:
        jsonld = _quality(accept, JSONLD_MEDIA_TYPE)
        turtle = _quality(accept, _TURTLE_TYPE)
    except ValueError:
        return False
    return jsonld > 0.0 and jsonld > turtle
