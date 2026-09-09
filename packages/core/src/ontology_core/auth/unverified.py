"""アクセストークンを**検証せずに**覗くための道具(`P2A-09`)。

## これを認可に使ってはならない

このモジュールは署名を検証しない。したがって**中身は攻撃者が自由に作れる**。
権限の判定に使ってはならない。判定に使うのは
`ontology_core.auth.entra.TokenVerifier`(JWKS で署名を検証する)だけである。

## では何のためにあるのか

**デプロイ前に「自分のトークンに何が乗っているか」を運用者に見せるため**である。
`P2A-06` で名前空間の作成に `platform-admin` アプリロールが必要になった結果、
割り当てが無いと `azd up` の `postdeploy` が 403 で止まる(約 11 分と課金を
使い切った後に)。`preprovision` で先に落とすには、**発行されたトークンの
`roles` クレームを見る**のが唯一正確な方法である。

Microsoft Graph の `appRoleAssignments` を読む方法もあるが採らなかった。

- **グループ経由の割り当てを見落とす。** ユーザーの `appRoleAssignments` は
  直接の割り当てしか返さない。グループに割り当てられている場合、実際には
  権限があるのに「無い」と報告して**偽のブロッカーになる**
- **Graph の読み取り権限を要求する。** テナントによっては運用者が持たない
- **トークンが権限の実体である。** API が見るのは `roles` クレームであり、
  ここで見るべきものもそれである。同じものを見れば食い違いが起きない
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

__all__ = ["UnverifiedTokenError", "unverified_claims", "unverified_roles"]


class UnverifiedTokenError(Exception):
    """JWT として読めなかったことを表す。

    「読めない」と「読めたが権限が無い」は運用者にとって別の状況である
    (前者は `az login` やスコープの問題、後者はロールの割り当ての問題)。
    そのため空の結果を返さずに例外にする。
    """


def _decode_segment(segment: str) -> bytes:
    """JWT の base64url セグメントを復号する。

    JWT はパディングの `=` を削るため、4 の倍数に戻してから復号する。
    """
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except (binascii.Error, ValueError) as exc:
        raise UnverifiedTokenError(f"base64url として復号できません: {exc}") from exc


def unverified_claims(token: str) -> dict[str, Any]:
    """署名を検証せずにクレームを返す。**認可に使ってはならない。**

    Raises:
        UnverifiedTokenError: JWT として読めないとき。
    """
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise UnverifiedTokenError(
            f"JWT は 3 つの部分から成るはずですが {len(parts)} 個でした"
            "(アクセストークンではなく別の文字列を渡していませんか)"
        )
    try:
        claims = json.loads(_decode_segment(parts[1]))
    except json.JSONDecodeError as exc:
        raise UnverifiedTokenError(f"ペイロードが JSON として読めません: {exc}") from exc
    if not isinstance(claims, dict):
        raise UnverifiedTokenError("ペイロードが JSON オブジェクトではありません")
    return claims


def unverified_roles(token: str) -> tuple[str, ...]:
    """署名を検証せずに `roles` クレームを返す。**認可に使ってはならない。**

    `roles` が無いトークンでは空を返す。**これは「読めなかった」ではなく
    「ロールが 1 件も割り当てられていない」である**(Entra はロールが無いとき
    クレーム自体を落とす)。読めなかった場合は `UnverifiedTokenError` になる。

    Raises:
        UnverifiedTokenError: JWT として読めないとき。
    """
    roles = unverified_claims(token).get("roles")
    if roles is None:
        return ()
    if not isinstance(roles, list):
        # 単一の文字列で来ることは仕様上無いが、来たときに黙って
        # 1 文字ずつに分解しないようにする。
        raise UnverifiedTokenError(f"roles クレームが配列ではありません: {type(roles).__name__}")
    return tuple(str(role) for role in roles)
