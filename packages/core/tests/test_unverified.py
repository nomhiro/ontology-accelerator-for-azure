"""検証せずにトークンを覗く道具のテスト(`P2A-09`)。"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from ontology_core.auth.unverified import (
    UnverifiedTokenError,
    unverified_claims,
    unverified_roles,
)


def _b64(data: bytes) -> str:
    """JWT と同じく、パディングを削った base64url にする。"""
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_token(claims: dict[str, Any]) -> str:
    """署名部分がでたらめな JWT を作る。**このモジュールは署名を見ない。**"""
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims).encode())
    return f"{header}.{payload}.not-a-real-signature"


def test_roles_を読み出す() -> None:
    token = make_token({"oid": "abc", "roles": ["platform-admin"]})
    assert unverified_roles(token) == ("platform-admin",)


def test_roles_が無いトークンは空を返す() -> None:
    """**Entra はロールが無いとき `roles` クレーム自体を落とす。**

    これは「読めなかった」ではなく「割り当てが 1 件も無い」である。
    例外にすると、運用者に「トークンが壊れている」と誤って報告してしまう。
    """
    assert unverified_roles(make_token({"oid": "abc"})) == ()


def test_パディングが必要な長さでも復号できる() -> None:
    """base64url のパディング(`=`)は JWT では削られる。

    ペイロードの長さを 1 バイトずつ変えて、4 で割った余りが 0/1/2/3 の
    すべての場合を通す。**ここを間違えると「たまたま通る」テストになる。**
    """
    for extra in range(4):
        claims = {"roles": ["platform-admin"], "pad": "x" * extra}
        assert unverified_roles(make_token(claims)) == ("platform-admin",)


def test_複数のロールを保持する() -> None:
    token = make_token({"roles": ["platform-admin", "other-role"]})
    assert unverified_roles(token) == ("platform-admin", "other-role")


def test_クレームをそのまま読める() -> None:
    token = make_token({"oid": "abc", "aud": "some-guid", "roles": []})
    claims = unverified_claims(token)
    assert claims["oid"] == "abc"
    assert claims["aud"] == "some-guid"


def test_JWT_でない文字列は理由付きで失敗する() -> None:
    """**「読めない」と「権限が無い」を混ぜない。**

    `az account get-access-token` の出力を取り違えて渡す事故は起こりうる。
    そのとき「platform-admin がありません」と報告すると、運用者は存在しない
    ロールの割り当てを疑って時間を失う。
    """
    with pytest.raises(UnverifiedTokenError, match="3 つの部分"):
        unverified_roles("これはトークンではありません")


def test_ペイロードが_JSON_でなければ失敗する() -> None:
    header = _b64(b'{"alg":"RS256"}')
    payload = _b64(b"not json at all")
    with pytest.raises(UnverifiedTokenError, match="JSON として読めません"):
        unverified_roles(f"{header}.{payload}.sig")


def test_ペイロードが_JSON_オブジェクトでなければ失敗する() -> None:
    header = _b64(b'{"alg":"RS256"}')
    payload = _b64(b'["not", "an", "object"]')
    with pytest.raises(UnverifiedTokenError, match="JSON オブジェクトではありません"):
        unverified_roles(f"{header}.{payload}.sig")


def test_roles_が文字列なら失敗する() -> None:
    """**黙って 1 文字ずつに分解しない。**

    `tuple("platform-admin")` は 14 要素になる。その状態で
    `"platform-admin" in roles` を判定すると常に偽になり、原因が分からない。
    """
    with pytest.raises(UnverifiedTokenError, match="配列ではありません"):
        unverified_roles(make_token({"roles": "platform-admin"}))


def test_復号できない_base64_は理由付きで失敗する() -> None:
    with pytest.raises(UnverifiedTokenError):
        unverified_roles("header.!!!!.sig")
