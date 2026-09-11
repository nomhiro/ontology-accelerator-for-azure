"""領域間マッピング(ADR-0023、`P2B-10`)。

[ADR-0009](../../../../docs/adr/0009-ontology-operations.md) 決定8 は「領域を
またぐ矛盾は統合せずマッピングする」と決めた。その実装である。

## `owl:equivalentClass` を提供しない

ADR-0009 決定8 は SKOS の `*Match` と並べて `owl:equivalentClass` も候補に
挙げていた。**これを却下した**(ADR-0023 決定1)。理由は実測である。

営業(`a:`)と経理(`b:`)の「優良顧客」を、互いに素なクラスの下に置いたまま
結んで ELK にかけた。

| 結んだ述語 | ELK の結論 |
|---|---|
| `owl:equivalentClass` | **充足不能クラス 2 件**(両方が使えなくなった) |
| `skos:exactMatch` | 充足不能クラスなし |

**`equivalentClass` はクラスの外延の同一性を主張するので、推論器が一方の
制約を他方へ流し込む。** マッピングは領域が違うから張るもので、領域が違えば
制約が食い違うのが普通である。つまり「食い違っている 2 つを結ぶ」という
主用途において、論理的帰結を持つ述語は構造的に危ない。

しかも**この壊れ方は承認では止まらない** — 推論器は CI にしか居らず
(ADR-0021 決定5)、CI が検査するのは同梱物だけである。

## 「統合しない」は二重で担保している

1. **述語をここで制限する**(論理的帰結を持つ述語を受け付けない)
2. **マッピングを TTL に書かない**(PostgreSQL に構造化して持つ。ADR-0023
   決定2)。TTL に入らなければ**推論器の視界に入らない**

## 相違を消さない

**逆向きのマッピングを自動生成しない**(決定3)。「A が B に exactMatch と
言っている」と「B が A に exactMatch と言っている」は別の事実であり、
自動生成は**相手が宣言していない主張を相手の名前空間に作る**。

**両側が違う述語を宣言したら、どちらも消さずに「争われている」と報告する**
(決定4)。自動で片方に寄せる実装は、相違を消す実装である。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ontology_core.iri import TermIriError, validate_term_iri

__all__ = [
    "SKOS_NAMESPACE",
    "MappingPredicate",
    "MappingSideBySide",
    "MappingValidationError",
    "compare_with_counterpart",
    "inverse_of",
    "predicate_iri",
    "validate_mapping",
]

SKOS_NAMESPACE = "http://www.w3.org/2004/02/skos/core#"


class MappingPredicate(StrEnum):
    """マッピングに使える述語(ADR-0023 決定1)。

    **SKOS のマッピング述語 5 つだけである。** `owl:equivalentClass` と
    `owl:sameAs` は**意図的に含めない** — 論理的帰結を持つため、互いに素な
    クラスの下にある用語を結ぶと両方が充足不能になる(モジュールの docstring
    に実測を記録した)。

    性質(SKOS の定義):

    | 述語 | 対称か | 推移的か | 逆 |
    |---|---|---|---|
    | `exact_match` | 対称 | **推移的** | 自分 |
    | `close_match` | 対称 | **推移的でない** | 自分 |
    | `broad_match` | 非対称 | — | `narrow_match` |
    | `narrow_match` | 非対称 | — | `broad_match` |
    | `related_match` | 対称 | — | 自分 |

    **推移的閉包は計算しない**(ADR-0023 の却下した代替案)。`exact_match` は
    推移的だが `close_match` は推移的でないので、混ぜて閉包を取ると
    「近いだけの用語を交換可能として扱う」誤った同一視を作る。
    """

    EXACT_MATCH = "exactMatch"
    CLOSE_MATCH = "closeMatch"
    BROAD_MATCH = "broadMatch"
    NARROW_MATCH = "narrowMatch"
    RELATED_MATCH = "relatedMatch"


def predicate_iri(predicate: MappingPredicate) -> str:
    """述語の絶対 IRI。TTL として書き出すとき、および API の表示に使う。"""
    return f"{SKOS_NAMESPACE}{predicate.value}"


# 逆向きの述語。**自動でマッピングを作るためではなく、相手側の宣言と
# 突き合わせるため**に使う(ADR-0023 決定3・4)。
_INVERSE: dict[MappingPredicate, MappingPredicate] = {
    MappingPredicate.EXACT_MATCH: MappingPredicate.EXACT_MATCH,
    MappingPredicate.CLOSE_MATCH: MappingPredicate.CLOSE_MATCH,
    MappingPredicate.RELATED_MATCH: MappingPredicate.RELATED_MATCH,
    MappingPredicate.BROAD_MATCH: MappingPredicate.NARROW_MATCH,
    MappingPredicate.NARROW_MATCH: MappingPredicate.BROAD_MATCH,
}


def inverse_of(predicate: MappingPredicate) -> MappingPredicate:
    """逆向きに宣言されるべき述語。

    対称な述語(`exactMatch` / `closeMatch` / `relatedMatch`)は自分自身、
    `broadMatch` と `narrowMatch` は互いを返す。

    **これは逆向きのマッピングを作るための関数ではない**(ADR-0023 決定3 は
    自動生成を却下した)。相手側が宣言している述語と突き合わせて、
    **相互に宣言されているか / 争われているか**を判定するために使う。
    """
    return _INVERSE[predicate]


class MappingValidationError(ValueError):
    """マッピングとして受け付けられないことを表す。

    ルータがこれを捕まえて 422 にマップする。
    """


@dataclass(frozen=True)
class MappingSideBySide:
    """自分の宣言と、相手側の宣言を突き合わせた結果(ADR-0023 決定3・4)。

    Attributes:
        reciprocal: 相手側も同じ用語ペアを宣言しているか。
            **偽は異常ではない** — 相手がまだ宣言していないだけである
            (初期状態では片側だけが正常なので、これを警告にしない)。
        disputed: 相互に宣言されていて、**述語が食い違っている**か。
            `exactMatch` に対して `closeMatch` が返っている等。
            **どちらも消さない**(決定4)。
        counterpart: 相手側が宣言している述語。無ければ `None`。
    """

    reciprocal: bool
    disputed: bool
    counterpart: MappingPredicate | None


def compare_with_counterpart(
    predicate: MappingPredicate, counterpart: MappingPredicate | None
) -> MappingSideBySide:
    """自分の述語と相手側の述語を突き合わせる。

    **相手が宣言していないことと、相手が違うことを混同しない。**
    前者は `reciprocal=False`(正常)、後者は `disputed=True`(報告対象)である。
    """
    if counterpart is None:
        return MappingSideBySide(reciprocal=False, disputed=False, counterpart=None)
    return MappingSideBySide(
        reciprocal=True,
        disputed=counterpart is not inverse_of(predicate),
        counterpart=counterpart,
    )


def validate_mapping(
    *, source_term: str, target_term: str, predicate: str
) -> tuple[str, str, MappingPredicate]:
    """マッピングの入力を検証して正規化した組を返す。

    Returns:
        `(source_term, target_term, predicate)`。

    Raises:
        MappingValidationError: 述語が使えない、IRI が不正、
            または始点と終点が同じ用語のとき。
    """
    try:
        resolved = MappingPredicate(predicate)
    except ValueError as exc:
        allowed = ", ".join(p.value for p in MappingPredicate)
        # **`owl:equivalentClass` を名指しで説明する。** ADR-0009 決定8 が
        # 候補に挙げていたので、使おうとする人が必ず現れる。単に
        # 「使えません」と言うと設計判断だと分からない。
        hint = ""
        if predicate.endswith(("equivalentClass", "sameAs")):
            hint = (
                "。**論理的帰結を持つ述語はマッピングに使えません** — "
                "互いに素なクラスの下にある用語を結ぶと両方が充足不能になります"
                "(ADR-0023 決定1)"
            )
        raise MappingValidationError(
            f"述語 '{predicate}' は使えません(使えるのは {allowed}){hint}"
        ) from exc

    try:
        source = validate_term_iri(source_term)
        target = validate_term_iri(target_term)
    except TermIriError as exc:
        raise MappingValidationError(str(exc)) from exc

    if source == target:
        # 自分自身へのマッピングは情報を持たない。**黙って受け付けると、
        # 「相互に宣言されている」の判定が自分だけで成立してしまう。**
        raise MappingValidationError("始点と終点が同じ用語です")

    return source, target, resolved
