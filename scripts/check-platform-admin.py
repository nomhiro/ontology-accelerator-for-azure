"""アクセストークンに `platform-admin` アプリロールが乗っているかを確認する(`P2A-09`)。

`P2A-06` で名前空間の作成に `platform-admin` が必要になった(ADR-0014 決定2・3)。
割り当てが無いと `azd up` の `postdeploy` が **403 で止まる** — 約 11 分の
プロビジョニングと課金を使い切った後に。`preprovision` からこれを呼んで
**デプロイの前に**落とすことで、無駄を防ぐ。

トークンは**標準入力**から渡す。引数にすると `ps` やシェルの履歴に残る。

使い方:

    az account get-access-token --scope "api://<appId>/.default" \\
        --query accessToken -o tsv | uv run python scripts/check-platform-admin.py

終了コード:

    0  `platform-admin` がある
    1  トークンが読めなかった(判定できていない。**ブロッカーとして扱わない**)
    2  読めたが `platform-admin` が無い(これがブロッカー)

**1 と 2 を分けているのは、呼び出し側の対処が違うため**である。2 は運用者が
ロールを割り当てれば解決する確定的な失敗だが、1 は「確認できなかった」で
あって権限が無いことを意味しない。1 をブロッカーにすると、判定の仕組み自体が
新しいブロッカーになる。
"""

from __future__ import annotations

import sys

from ontology_core.auth.unverified import UnverifiedTokenError, unverified_roles
from ontology_core.console import say, warn
from ontology_core.models import PlatformRole

_EXIT_OK = 0
_EXIT_UNREADABLE = 1
_EXIT_MISSING = 2

_REQUIRED = PlatformRole.PLATFORM_ADMIN.value


def main() -> int:
    token = sys.stdin.read().strip()
    if not token:
        warn("check-platform-admin: 標準入力が空です。トークンをパイプで渡してください")
        return _EXIT_UNREADABLE

    try:
        roles = unverified_roles(token)
    except UnverifiedTokenError as exc:
        warn(f"check-platform-admin: トークンを読めません: {exc}")
        return _EXIT_UNREADABLE

    if _REQUIRED in roles:
        say(f"check-platform-admin: '{_REQUIRED}' があります")
        return _EXIT_OK

    held = ", ".join(roles) if roles else "(1 件もありません)"
    warn(
        f"check-platform-admin: '{_REQUIRED}' がトークンに乗っていません\n"
        f"  トークンの roles: {held}\n"
        f"  名前空間の作成と POST /admin/reconcile はこのロールを要求します"
        f"(ADR-0014 決定2)。\n"
        f"  uv run python scripts/setup-app-role.py で定義・割り当てができます。\n"
        f"  **割り当てた直後は既存のトークンに反映されません。**"
        f" 反映には数分かかることがあります",
    )
    return _EXIT_MISSING


if __name__ == "__main__":
    sys.exit(main())
