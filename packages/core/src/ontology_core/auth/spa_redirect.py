"""SPA のリダイレクト URI をまとめる(ADR-0045 決定3・4、`P2A-20`)。

## なぜ `spa` 型でなければならないのか

**`web` 型のまま認可コードフローを使うと CORS で失敗する**
([Entra の文書](https://learn.microsoft.com/entra/identity-platform/v2-oauth2-auth-code-flow#redirect-uris-for-single-page-apps-spas))。
`spa` 型が PKCE と CORS を有効にする。

## なぜ localhost を 1 つに絞るのか

Entra の文書が明示している。

> Do not register multiple localhost redirect URIs where only the port
> differs. The login server picks one arbitrarily and uses the behavior
> associated with that registered redirect URI.

**localhost ではポートが照合で無視される**ので、`http://localhost:5173` を
登録すれば `http://localhost:4173`(`vite preview`)でも通る。ポートだけ違う
URI を足すと、**どの登録の型が使われるかが不定になる**。

## 複合プロパティの罠

`spa` は `appRoles` と `optionalClaims` と同じ複合プロパティである。
**部分更新すると既存の URI が丸ごと消える**(`P1-09` の罠1)。
`merge_spa_redirect_uri` は**常に完全な配列を返す**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "MergedSpaRedirects",
    "SpaRedirectError",
    "is_loopback",
    "merge_spa_redirect_uri",
    "validate_spa_redirect_uri",
]


class SpaRedirectError(ValueError):
    """リダイレクト URI が Entra の制約を満たさない(ADR-0045 決定3・4)。"""


@dataclass(frozen=True)
class MergedSpaRedirects:
    """合流した結果。

    Attributes:
        redirect_uris: **送るべき完全な配列**。部分更新してはいけない。
        changed: 送る必要があるか。**同じものが既にあれば送らない**(冪等)。
    """

    redirect_uris: list[str]
    changed: bool


def is_loopback(uri: str) -> bool:
    """localhost 宛か。

    **`127.0.0.1` も loopback だが、ここでは `localhost` だけを見る。**
    Entra は `http` スキームを `localhost` に対してだけ許し、`127.0.0.1` に
    対する `http` はポータルからは登録できない(マニフェストの編集が必要)。
    **扱える形だけを扱う。**
    """
    host = urlparse(uri).hostname
    return host == "localhost"


def validate_spa_redirect_uri(uri: str) -> str:
    """Entra の制約を確かめる(ADR-0045 決定3)。

    Raises:
        SpaRedirectError: 満たさないとき。**理由を捨てない。**
    """
    if not uri or uri != uri.strip():
        raise SpaRedirectError(f"リダイレクト URI が空か前後に空白があります: {uri!r}")
    parsed = urlparse(uri)
    if parsed.scheme not in {"http", "https"}:
        raise SpaRedirectError(
            f"スキームは http か https でなければなりません: {uri}(実際: {parsed.scheme!r})"
        )
    if parsed.scheme == "http" and not is_loopback(uri):
        # **HTTP は localhost だけ例外である**(Entra の文書)。
        raise SpaRedirectError(
            f"http は localhost にだけ使えます: {uri}。デプロイ環境では https を使ってください"
        )
    if parsed.fragment:
        raise SpaRedirectError(f"フラグメントを含められません: {uri}")
    for bad in "!$'(),;":
        if bad in uri:
            raise SpaRedirectError(f"使えない文字 {bad!r} が含まれています: {uri}")
    return uri


def merge_spa_redirect_uri(existing: Any, *, uri: str) -> MergedSpaRedirects:
    """`spa.redirectUris` に 1 つ足した完全な配列を返す。

    **既存を落とさない**(複合プロパティの罠)。`existing` は
    `az ad app show` が返す `spa` の値(辞書)か `None` を受ける。

    **localhost をもう 1 つ足そうとしたら拒否する**(ADR-0045 決定4)。
    ポートが照合で無視されるため、ポートだけ違う登録は
    **サーバが任意に 1 つを選ぶ**状態を作る。

    Raises:
        SpaRedirectError: 制約を満たさないとき、または localhost が
            既にあって別の localhost を足そうとしたとき。
    """
    validate_spa_redirect_uri(uri)

    current: list[str] = []
    if isinstance(existing, dict):
        raw = existing.get("redirectUris")
        if isinstance(raw, list):
            current = [str(item) for item in raw]

    if uri in current:
        # **冪等。** 同じものが既にあれば何も送らない。
        return MergedSpaRedirects(redirect_uris=list(current), changed=False)

    if is_loopback(uri):
        clashing = [item for item in current if is_loopback(item)]
        if clashing:
            raise SpaRedirectError(
                f"localhost のリダイレクト URI が既に登録されています: {clashing[0]}。"
                f"{uri} を足すと、**ポートが照合で無視されるため**どちらの登録が"
                "使われるか不定になります(Entra の文書が明示的に禁じています)。"
                "既存のものをそのまま使ってください"
                "(ポートが違っても通ります)"
            )

    return MergedSpaRedirects(redirect_uris=[*current, uri], changed=True)
