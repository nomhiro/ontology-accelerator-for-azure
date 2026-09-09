"""用語 IRI の検証(ADR-0015 決定3、`P2B-04`)。

## 名前空間名とは扱いが違う

名前空間名は Fuseki のデータセット名・Blob のパス・グラフ IRI の組み立てに
使うため、**セキュリティ境界**である(不変条件5。`graphs.py` を通す)。
用語 IRI はそうではない — 保存して返すだけで、パスやデータセット名を
組み立てない。そのため許す文字はずっと広い。

## それでも何を弾くのか

**絶対 IRI でないもの**と、**空白・制御文字・SPARQL の区切り文字**である。

- 相対 IRI を許すと、何を基準に解決するのかが決まらない
  (`base_iri` 配下に限らないと決めた以上、基準が無い)
- 空白と制御文字は、`<...>` で囲んで SPARQL に埋め込んだときに
  構文を壊す。**将来の埋め込みの事故を入口で潰しておく**
  (埋め込む側のエスケープ責任を免除するものではない)

`base_iri` 配下に限定しないのは [ADR-0009](../../../../docs/adr/0009-ontology-operations.md)
決定8(領域をまたぐ矛盾は統合せずマッピングする)のためである。外部語彙への
`skos:closeMatch` を張ったとき、そのマッピングの妥当性について説明責任を
負うのは張った側であり、外部 IRI に責任者を置けなければ記録できない。
"""

from __future__ import annotations

import re

__all__ = ["MAX_TERM_IRI_LENGTH", "TermIriError", "validate_term_iri"]

# `term_owners.term_iri` の列長と揃える。
MAX_TERM_IRI_LENGTH = 1024

# スキームは RFC 3987 に従い、英字で始まり英数字と `+ - .` が続く。
_SCHEME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")

# SPARQL の `<...>` に埋め込めない文字。RFC 3987 が IRI から除外する
# 文字集合(`<>"{}|^`\` と空白)に、制御文字を加えたもの。
_FORBIDDEN = re.compile(r"[\x00-\x20\x7f<>\"{}|^`\\]")


class TermIriError(ValueError):
    """用語 IRI として使えないことを表す。"""


def validate_term_iri(iri: str) -> str:
    """用語 IRI を検証して返す。**正規化はしない。**

    前後の空白を黙って落とさないのは、「見た目が同じで別の行」を作らない
    ためである。一意制約は正規化された値に対して働くので、正規化を入口で
    分散させると、同じ用語に 2 行入りうる状態を「入らない」と誤認させる。

    Raises:
        TermIriError: 絶対 IRI でない、使えない文字を含む、または長すぎるとき。
    """
    if not iri:
        raise TermIriError("用語 IRI が空です")
    if len(iri) > MAX_TERM_IRI_LENGTH:
        raise TermIriError(
            f"用語 IRI が長すぎます({len(iri)} 文字。上限 {MAX_TERM_IRI_LENGTH} 文字)"
        )
    found = _FORBIDDEN.search(iri)
    if found is not None:
        # 制御文字をそのまま出すとログが壊れるので、コードポイントで示す。
        char = found.group()
        shown = repr(char) if char.isprintable() else f"U+{ord(char):04X}"
        raise TermIriError(f"用語 IRI '{iri!r}' に使えない文字が含まれています: {shown}")
    if not _SCHEME_PATTERN.match(iri):
        raise TermIriError(
            f"用語 IRI '{iri}' は絶対 IRI ではありません"
            "(`https://example.com/ontology#Product` のようにスキームから書いてください)"
        )
    return iri
