"""領域間マッピングの述語と突き合わせのテスト(ADR-0023、`P2B-10`)。

**中心は 2 つである。**

1. **論理的帰結を持つ述語を受け付けない**(決定1)。`owl:equivalentClass` で
   結ぶと両方のクラスが充足不能になることを ELK で実測した
2. **「相手が宣言していない」と「相手が違うことを言っている」を混同しない**
   (決定3・4)。前者は正常、後者は報告対象である
"""

from __future__ import annotations

import pytest

from ontology_core.mapping import (
    SKOS_NAMESPACE,
    MappingPredicate,
    MappingValidationError,
    compare_with_counterpart,
    inverse_of,
    predicate_iri,
    validate_mapping,
)

_A = "https://e.example/sales#GoodCustomer"
_B = "https://e.example/finance#GoodCustomer"


# ------------------------------------------------------------ 述語の制限


@pytest.mark.parametrize("predicate", [p.value for p in MappingPredicate])
def test_SKOS_の_5_つは使える(predicate: str) -> None:
    source, target, resolved = validate_mapping(source_term=_A, target_term=_B, predicate=predicate)
    assert source == _A
    assert target == _B
    assert resolved.value == predicate


@pytest.mark.parametrize(
    "predicate",
    [
        "equivalentClass",
        "http://www.w3.org/2002/07/owl#equivalentClass",
        "sameAs",
        "http://www.w3.org/2002/07/owl#sameAs",
    ],
)
def test_論理的帰結を持つ述語は拒否する(predicate: str) -> None:
    """**これが ADR-0023 の中心である。**

    ADR-0009 決定8 が候補に挙げていたので、使おうとする人が必ず現れる。
    実測では互いに素なクラスの下にある用語を `equivalentClass` で結ぶと
    **両方のクラスが充足不能になった**。
    """
    with pytest.raises(MappingValidationError) as exc:
        validate_mapping(source_term=_A, target_term=_B, predicate=predicate)
    # **理由を伝える。** 単に「使えません」だと設計判断だと分からない。
    assert "論理的帰結" in str(exc.value)
    assert "充足不能" in str(exc.value)


def test_未知の述語は拒否し使える述語を並べる() -> None:
    with pytest.raises(MappingValidationError) as exc:
        validate_mapping(source_term=_A, target_term=_B, predicate="sortOfLike")
    message = str(exc.value)
    assert "exactMatch" in message
    assert "closeMatch" in message
    # 論理的帰結の説明は、`equivalentClass` 系のときだけ出す。
    assert "論理的帰結" not in message


def test_述語の_IRI_は_SKOS_の名前空間() -> None:
    assert predicate_iri(MappingPredicate.EXACT_MATCH) == SKOS_NAMESPACE + "exactMatch"
    assert predicate_iri(MappingPredicate.BROAD_MATCH) == SKOS_NAMESPACE + "broadMatch"


# -------------------------------------------------------------- IRI の検証


def test_相対_IRI_は拒否する() -> None:
    with pytest.raises(MappingValidationError):
        validate_mapping(source_term="#Product", target_term=_B, predicate="closeMatch")


def test_終点の相対_IRI_も拒否する() -> None:
    with pytest.raises(MappingValidationError):
        validate_mapping(source_term=_A, target_term="Product", predicate="closeMatch")


def test_始点と終点が同じなら拒否する() -> None:
    """**黙って受け付けると、「相互に宣言されている」が自分だけで成立する。**"""
    with pytest.raises(MappingValidationError, match="同じ用語"):
        validate_mapping(source_term=_A, target_term=_A, predicate="exactMatch")


def test_外部語彙への_IRI_は受け付ける() -> None:
    """**外部語彙へのマッピングが正当な主用途である**(ADR-0023 決定6)。"""
    _, target, _ = validate_mapping(
        source_term=_A,
        target_term="http://www.w3.org/2004/02/skos/core#Concept",
        predicate="closeMatch",
    )
    assert target == "http://www.w3.org/2004/02/skos/core#Concept"


# ------------------------------------------------------------ 逆向きの述語


def test_対称な述語の逆は自分自身() -> None:
    for predicate in (
        MappingPredicate.EXACT_MATCH,
        MappingPredicate.CLOSE_MATCH,
        MappingPredicate.RELATED_MATCH,
    ):
        assert inverse_of(predicate) is predicate


def test_broad_と_narrow_は互いの逆() -> None:
    assert inverse_of(MappingPredicate.BROAD_MATCH) is MappingPredicate.NARROW_MATCH
    assert inverse_of(MappingPredicate.NARROW_MATCH) is MappingPredicate.BROAD_MATCH


def test_すべての述語に逆がある() -> None:
    """**網羅していないと `KeyError` で落ちる。** 述語を足したら逆も足す。"""
    for predicate in MappingPredicate:
        assert inverse_of(predicate) in MappingPredicate


# ------------------------------------------------------ 突き合わせの判定


def test_相手が宣言していなければ相互でないが争いでもない() -> None:
    """**片側だけの主張は異常ではない**(ADR-0023 決定3)。

    初期状態では片側だけが正常なので、これを警告にしてはいけない。
    """
    compared = compare_with_counterpart(MappingPredicate.EXACT_MATCH, None)
    assert not compared.reciprocal
    assert not compared.disputed
    assert compared.counterpart is None


def test_相手が同じ述語なら相互で争いなし() -> None:
    compared = compare_with_counterpart(MappingPredicate.EXACT_MATCH, MappingPredicate.EXACT_MATCH)
    assert compared.reciprocal
    assert not compared.disputed


def test_相手が違う述語なら争い() -> None:
    """**どちらも消さない**(決定4)。自動で片方に寄せるのは相違を消す実装。"""
    compared = compare_with_counterpart(MappingPredicate.EXACT_MATCH, MappingPredicate.CLOSE_MATCH)
    assert compared.reciprocal
    assert compared.disputed
    assert compared.counterpart is MappingPredicate.CLOSE_MATCH


def test_broad_に_narrow_が返っていれば争いではない() -> None:
    """**逆向きの述語は一致とみなす。** `broadMatch` の逆は `narrowMatch`。

    ここを「同じ述語か」で判定すると、**正しく宣言された非対称な対応が
    すべて争いになる**。
    """
    compared = compare_with_counterpart(MappingPredicate.BROAD_MATCH, MappingPredicate.NARROW_MATCH)
    assert compared.reciprocal
    assert not compared.disputed


def test_broad_に_broad_が返っていれば争い() -> None:
    """両方が「相手の方が広い」と言っているので矛盾している。"""
    compared = compare_with_counterpart(MappingPredicate.BROAD_MATCH, MappingPredicate.BROAD_MATCH)
    assert compared.disputed
