"""Entra アプリ登録の `optionalClaims` の合成(ADR-0035 決定7、`P2A-16`)。

`scripts/setup-app-role.py` が使う純粋関数を置く。**なぜコアに置くのか**:
`optionalClaims` は `appRoles` と同じ Microsoft Graph の**複合プロパティ**で、
部分更新すると**既存の定義が丸ごと消える**。同じ罠を `api`(スコープと
`preAuthorizedApplications`)と `appRoles` で既に踏んでいる(`P1-09` の罠1)。
消してはいけないものを消さないことは自動テストで固定する価値がある。

このモジュールは Graph を呼ばない。**辞書を受け取って辞書を返すだけ**である。

## なぜ `idtyp` を設定する必要があるのか

`idtyp` は**任意クレーム**である。設定しないと、サービスプリンシパルの
トークンにも付かない。付かないと `ActorType.UNKNOWN` として記録され、
**監査証跡は人間と機械を永久に区別できない**(ADR-0035 決定1)。

**設定するのはリソース側(この API のアプリ登録)である。** クライアント側
ではない — アクセストークンはリソースが所有する、と Entra の文書が明記して
いる。だからこのリポジトリのセットアップ手順に置ける。
"""

from __future__ import annotations

from typing import Any

__all__ = ["OptionalClaimMerge", "merge_optional_claim"]

#: `optionalClaims` が持つトークン種別のキー。**すべて保持する。**
#:
#: 合成の対象は 1 つだけだが、**送るのは全体**でなければならない
#: (複合プロパティなので、送らなかったキーは消える)。
TOKEN_TYPES = ("idToken", "accessToken", "saml2Token")


class OptionalClaimMerge:
    """`optionalClaims` を合成した結果。

    Attributes:
        optional_claims: PATCH でそのまま送る**完全な**オブジェクト。
        changed: 送る必要があるか。既に同じ定義があれば `False`。
    """

    __slots__ = ("changed", "optional_claims")

    def __init__(self, *, optional_claims: dict[str, list[dict[str, Any]]], changed: bool) -> None:
        self.optional_claims = optional_claims
        self.changed = changed


def merge_optional_claim(
    existing: dict[str, Any] | None,
    *,
    token_type: str,
    name: str,
    additional_properties: tuple[str, ...] = (),
) -> OptionalClaimMerge:
    """`name` のクレームを含む**完全な** `optionalClaims` を返す。

    - **既存のクレームは 1 件も落とさない。** 他のトークン種別(`idToken` /
      `saml2Token`)も、同じ種別の別のクレームも保持する
    - **既にあれば `additionalProperties` を揃える。** 足りない要素を
      **足すだけ**で、既存の要素は消さない — 別の目的で付けられた追加
      プロパティを奪わないため
    - **`essential` は既存の値を尊重する。** 無ければ `False` を入れる。
      このリポジトリが要求するクレームは「無ければ `unknown` になる」だけで
      **サインインを止める理由にはならない**(`essential: true` はトークンを
      発行できないときに失敗させる意味を持つ)

    Args:
        token_type: `idToken` / `accessToken` / `saml2Token` のいずれか。
        name: クレーム名(`idtyp` など)。
        additional_properties: 付けたい追加プロパティ(`include_user_token` など)。

    Raises:
        ValueError: `token_type` が Graph の知るキーでないとき。**黙って
            新しいキーを作らない** — 綴りを間違えると「設定したのに効かない」
            という最も分かりにくい壊れ方になる。
    """
    if token_type not in TOKEN_TYPES:
        raise ValueError(
            f"token_type は {TOKEN_TYPES} のいずれかでなければなりません: {token_type!r}"
        )

    merged: dict[str, list[dict[str, Any]]] = {}
    source = existing or {}
    for key in TOKEN_TYPES:
        claims = source.get(key)
        merged[key] = [dict(claim) for claim in claims] if isinstance(claims, list) else []
    # Graph が将来のキーを返してきても落とさない。
    for key, claims in source.items():
        if key not in merged and isinstance(claims, list):
            merged[key] = [dict(claim) for claim in claims]

    changed = False
    target = merged[token_type]
    for claim in target:
        if claim.get("name") != name:
            continue
        if "essential" not in claim:
            claim["essential"] = False
            changed = True
        props = claim.get("additionalProperties")
        current = list(props) if isinstance(props, list) else []
        for wanted in additional_properties:
            if wanted not in current:
                current.append(wanted)
                changed = True
        if current != props:
            claim["additionalProperties"] = current
            changed = True
        return OptionalClaimMerge(optional_claims=merged, changed=changed)

    target.append(
        {
            "name": name,
            "essential": False,
            "additionalProperties": list(additional_properties),
        }
    )
    return OptionalClaimMerge(optional_claims=merged, changed=True)
