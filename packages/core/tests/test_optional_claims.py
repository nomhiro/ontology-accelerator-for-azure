"""Entra アプリ登録の `optionalClaims` の合成(ADR-0035 決定7、`P2A-16`)。

**`optionalClaims` は Microsoft Graph の複合プロパティである。** 部分更新すると
既存の定義が丸ごと消える。同じ罠を `api`(スコープと
`preAuthorizedApplications`)と `appRoles` で既に踏んでいる(`P1-09` の罠1)。

ここで固定するのは**消してはいけないものを消さないこと**である。
`scripts/setup-app-role.py` は実テナントを触るので自動テストで回せない。
純粋関数に切り出してあるのはこのためである(`test_app_roles.py` と同じ形)。
"""

from __future__ import annotations

from typing import Any

import pytest

from ontology_core.auth.optional_claims import TOKEN_TYPES, merge_optional_claim


def _merge(existing: dict[str, Any] | None, **kwargs: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "token_type": "accessToken",
        "name": "idtyp",
        "additional_properties": ("include_user_token",),
    }
    return merge_optional_claim(existing, **{**defaults, **kwargs}).optional_claims


def _claim(claims: dict[str, Any], *, token_type: str, name: str) -> dict[str, Any] | None:
    for claim in claims.get(token_type, []):
        if claim.get("name") == name:
            found: dict[str, Any] = claim
            return found
    return None


# -------------------------------------------------------- 新規に足す


def test_無い_optionalClaims_に足せる() -> None:
    merged = _merge(None)
    claim = _claim(merged, token_type="accessToken", name="idtyp")
    assert claim == {
        "name": "idtyp",
        "essential": False,
        "additionalProperties": ["include_user_token"],
    }


def test_全てのトークン種別のキーを必ず含める() -> None:
    """**送るのは全体である**(複合プロパティなので、送らなかったキーは消える)。"""
    merged = _merge(None)
    assert set(TOKEN_TYPES) <= set(merged)


def test_essential_は真にしない() -> None:
    """**`essential: true` はトークンを発行できないときに失敗させる意味を持つ。**

    このクレームが無いことの帰結は「種別が `unknown` になる」だけで、
    サインインを止める理由にはならない。
    """
    claim = _claim(_merge(None), token_type="accessToken", name="idtyp")
    assert claim is not None
    assert claim["essential"] is False


# -------------------------------------------- 消してはいけないものを消さない


def test_他のトークン種別のクレームを落とさない() -> None:
    """**`idToken` / `saml2Token` を巻き込まない。**"""
    existing = {
        "idToken": [{"name": "upn", "essential": False, "additionalProperties": []}],
        "saml2Token": [{"name": "groups", "essential": True, "additionalProperties": []}],
    }
    merged = _merge(existing)
    assert _claim(merged, token_type="idToken", name="upn") is not None
    assert _claim(merged, token_type="saml2Token", name="groups") is not None


def test_同じトークン種別の別のクレームを落とさない() -> None:
    existing = {"accessToken": [{"name": "acct", "essential": False}]}
    merged = _merge(existing)
    assert _claim(merged, token_type="accessToken", name="acct") is not None
    assert _claim(merged, token_type="accessToken", name="idtyp") is not None


def test_Graph_が知らないキーを返しても落とさない() -> None:
    """**将来のトークン種別を消さない。** 送らなかったキーは Entra から消える。"""
    existing = {"futureToken": [{"name": "x"}]}
    merged = _merge(existing)
    assert merged["futureToken"] == [{"name": "x"}]


def test_入力の辞書を書き換えない() -> None:
    """**Graph から取ってきたオブジェクトをその場で汚さない。**

    呼び出し側は「変更が必要か」を `changed` で判断してから送る。入力を
    書き換えると、送らない判断をしたときに手元の値だけが変わる。
    """
    existing: dict[str, Any] = {"accessToken": [{"name": "acct", "essential": False}]}
    snapshot = {"accessToken": [{"name": "acct", "essential": False}]}
    _merge(existing)
    assert existing == snapshot


def test_対象のクレームがある入力も書き換えない() -> None:
    """**変異テストで見つけた穴である。**

    上のテストは入力に `idtyp` が無い場合しか見ていなかったため、
    `[dict(claim) for claim in claims]` を `list(claims)` にする変異
    (内側の辞書を入力と共有する)が生き残った。**追加プロパティを足す経路が
    入力を汚す**のはこの場合である。
    """
    existing: dict[str, Any] = {
        "accessToken": [{"name": "idtyp", "essential": False, "additionalProperties": []}]
    }
    _merge(existing)
    assert existing == {
        "accessToken": [{"name": "idtyp", "essential": False, "additionalProperties": []}]
    }


# ------------------------------------------------------------ 冪等である


def test_既に同じ定義があれば送らない() -> None:
    existing = {
        "accessToken": [
            {"name": "idtyp", "essential": False, "additionalProperties": ["include_user_token"]}
        ]
    }
    assert not merge_optional_claim(
        existing,
        token_type="accessToken",
        name="idtyp",
        additional_properties=("include_user_token",),
    ).changed


def test_追加プロパティが足りなければ足す() -> None:
    existing = {"accessToken": [{"name": "idtyp", "essential": False}]}
    result = merge_optional_claim(
        existing,
        token_type="accessToken",
        name="idtyp",
        additional_properties=("include_user_token",),
    )
    assert result.changed
    claim = _claim(result.optional_claims, token_type="accessToken", name="idtyp")
    assert claim is not None
    assert claim["additionalProperties"] == ["include_user_token"]


def test_既存の追加プロパティを奪わない() -> None:
    """**別の目的で付けられた追加プロパティを消さない。** 足すだけにする。"""
    existing = {
        "accessToken": [
            {"name": "idtyp", "essential": False, "additionalProperties": ["something_else"]}
        ]
    }
    merged = _merge(existing)
    claim = _claim(merged, token_type="accessToken", name="idtyp")
    assert claim is not None
    assert claim["additionalProperties"] == ["something_else", "include_user_token"]


def test_essential_が真なら尊重する() -> None:
    """運用者が意図して真にしているなら下げない(下げると挙動が変わる)。"""
    existing = {
        "accessToken": [
            {"name": "idtyp", "essential": True, "additionalProperties": ["include_user_token"]}
        ]
    }
    result = merge_optional_claim(
        existing,
        token_type="accessToken",
        name="idtyp",
        additional_properties=("include_user_token",),
    )
    assert not result.changed
    claim = _claim(result.optional_claims, token_type="accessToken", name="idtyp")
    assert claim is not None
    assert claim["essential"] is True


# ------------------------------------------------------------ 入力の検証


@pytest.mark.parametrize("token_type", ["accesstoken", "AccessToken", "access_token", "jwt", ""])
def test_知らないトークン種別は拒否する(token_type: str) -> None:
    """**黙って新しいキーを作らない**(ADR-0035 決定7)。

    綴りを間違えると Graph は `optionalClaims` を受け付けたうえで**何も
    起きない**。「設定したのに効かない」は最も分かりにくい壊れ方である。
    """
    with pytest.raises(ValueError, match="token_type"):
        merge_optional_claim(None, token_type=token_type, name="idtyp")


def test_リストでない値は空として扱う() -> None:
    """Graph が `null` を返すことがある。**そこで落ちない。**"""
    merged = _merge({"idToken": None, "accessToken": None})
    assert _claim(merged, token_type="accessToken", name="idtyp") is not None
    assert merged["idToken"] == []
