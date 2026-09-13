"""SPA のリダイレクト URI の合流(ADR-0045 決定3・4、`P2A-20`)。

固定するのは 3 つ。

1. **既存の URI を落とさない**(複合プロパティの罠。`P1-09` の罠1)
2. **localhost をもう 1 つ足させない**(ポートが照合で無視されるため、
   どちらの登録が使われるか不定になる。Entra の文書が明示的に禁じている)
3. **http は localhost だけ**(デプロイ環境で http を登録させない)
"""

from __future__ import annotations

import pytest

from ontology_core.auth.spa_redirect import (
    SpaRedirectError,
    is_loopback,
    merge_spa_redirect_uri,
    validate_spa_redirect_uri,
)

LOCAL = "http://localhost:5173"
DEPLOYED = "https://proud-forest-035e70d00.6.azurestaticapps.net"


def test_既存の_URI_を落とさない() -> None:
    """**複合プロパティの罠**(`P1-09` の罠1)。

    部分更新すると既存が丸ごと消える。**常に完全な配列を返す。**
    """
    merged = merge_spa_redirect_uri({"redirectUris": [DEPLOYED]}, uri=LOCAL)
    assert merged.changed
    assert merged.redirect_uris == [DEPLOYED, LOCAL]


def test_空の状態から足せる() -> None:
    merged = merge_spa_redirect_uri({"redirectUris": []}, uri=LOCAL)
    assert merged.redirect_uris == [LOCAL]
    assert merged.changed


def test_spa_が無い形でも落ちない() -> None:
    """`az ad app show` が `spa` を返さないことがある。"""
    assert merge_spa_redirect_uri(None, uri=LOCAL).redirect_uris == [LOCAL]
    assert merge_spa_redirect_uri({}, uri=LOCAL).redirect_uris == [LOCAL]


def test_同じ_URI_なら送らない() -> None:
    """**冪等である。** 何度実行しても Entra へ書き込まない。"""
    merged = merge_spa_redirect_uri({"redirectUris": [LOCAL, DEPLOYED]}, uri=LOCAL)
    assert not merged.changed
    assert merged.redirect_uris == [LOCAL, DEPLOYED]


def test_localhost_を_2_つ登録させない() -> None:
    """**これが決定4 の本体である。**

    Entra の文書:

    > Do not register multiple localhost redirect URIs where only the port
    > differs. The login server picks one arbitrarily.

    ポートが照合で無視されるので、1 つ登録すれば別のポートでも通る。
    足すと**どちらの登録の型が使われるか不定**になる。
    """
    with pytest.raises(SpaRedirectError, match="ポートが照合で無視される"):
        merge_spa_redirect_uri({"redirectUris": [LOCAL]}, uri="http://localhost:4173")


def test_localhost_が_あっても_https_のオリジンは足せる() -> None:
    """デプロイ済みオリジンの追加は禁じられていない。"""
    merged = merge_spa_redirect_uri({"redirectUris": [LOCAL]}, uri=DEPLOYED)
    assert merged.redirect_uris == [LOCAL, DEPLOYED]


def test_https_の_localhost_も_localhost_として扱う() -> None:
    """スキームが違ってもホストが `localhost` なら同じ扱いである。"""
    with pytest.raises(SpaRedirectError):
        merge_spa_redirect_uri({"redirectUris": ["https://localhost:5173"]}, uri=LOCAL)


@pytest.mark.parametrize(
    "uri",
    [
        "http://contoso.example/callback",
        "http://127.0.0.1:5173",
        "http://192.168.0.2:5173",
    ],
)
def test_http_は_localhost_だけ(uri: str) -> None:
    """**HTTP は localhost だけ例外である**(Entra の文書)。

    `127.0.0.1` も loopback だが、`http` での登録はポータルから行えない
    (マニフェストの編集が必要)。**扱える形だけを扱う。**
    """
    with pytest.raises(SpaRedirectError, match="http は localhost にだけ"):
        validate_spa_redirect_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost",
        "http://localhost:5173",
        "http://localhost:5173/callback",
        "https://localhost:5173",
        "https://contoso.example",
        "https://contoso.example/auth",
    ],
)
def test_通る形(uri: str) -> None:
    assert validate_spa_redirect_uri(uri) == uri


@pytest.mark.parametrize(
    "uri",
    [
        "",
        " https://contoso.example",
        "https://contoso.example ",
        "ftp://contoso.example",
        "contoso.example",
        "https://contoso.example#fragment",
        "https://contoso.example/a(b)",
        "https://contoso.example/a;b",
    ],
)
def test_通らない形(uri: str) -> None:
    """**理由を捨てない。** どの制約に触れたかがメッセージに出る。"""
    with pytest.raises(SpaRedirectError):
        validate_spa_redirect_uri(uri)


def test_loopback_の判定() -> None:
    assert is_loopback("http://localhost:5173")
    assert is_loopback("https://localhost")
    assert not is_loopback("https://contoso.example")
    # **`127.0.0.1` を `localhost` と同一視しない。** 扱える形だけを扱う。
    assert not is_loopback("http://127.0.0.1:5173")
