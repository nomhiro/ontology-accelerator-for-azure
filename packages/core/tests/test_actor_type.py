"""トークンから主体の種別を読む(ADR-0035 決定1、`P2A-16`)。

ここで固定するのは**推測しないこと**である。

`idtyp` は Entra の**任意クレーム**で、リソース側のアプリ登録に設定しないと
発行されない。設定していないテナントでは**サービスプリンシパルのトークンにも
付かない**。したがって「クレームが無い」は「人間である」を意味しない。

以前の実装はこう書いていた。

```python
is_app = claims.get("idtyp") == "app" or "oid" not in claims
```

アプリ専用トークンにも `oid`(サービスプリンシパルのオブジェクト ID)は付く
ので、この式は `idtyp` を設定していないテナントのサービスプリンシパルを
**人間として判定する**。ADR-0026 決定3 が「最悪の誤り」と呼んだものの逆向き
である(自動化された行為を人間の行為として見せる)。

**このファイルの中心は `test_oid_があっても人間だと判定しない` である。**
"""

from __future__ import annotations

from typing import Any

import pytest

from ontology_core.auth.entra import Principal, TokenVerifier
from ontology_core.models import ActorType

_OID = "11111111-2222-3333-4444-555555555555"


def _principal(**claims: Any) -> Principal:
    """クレームから主体を組み立てる。**署名の検証は別の関心である。**

    `_to_principal` は純粋な写しなので、JWKS を用意せずに検査できる。
    """
    return TokenVerifier._to_principal(claims)


# ------------------------------------------------------------ 推測しない


def test_oid_があっても人間だと判定しない() -> None:
    """**このファイルの中心。**

    `idtyp` を設定していないテナントのサービスプリンシパルは、`oid` を
    持ったまま `idtyp` 無しで届く。以前の実装はこれを
    `is_service_principal = False`(= 人間)と判定していた。

    **分からないなら `UNKNOWN` である。** 人間だと名乗らせない。
    """
    principal = _principal(sub="sp-sub", oid=_OID)
    assert principal.actor_type is ActorType.UNKNOWN, (
        "oid の有無から種別を推測してはならない。アプリ専用トークンにも oid は付く"
    )


def test_oid_が無くてもサービスプリンシパルだと判定しない() -> None:
    """逆向きも推測しない(ADR-0035 決定1)。

    `oid` が無いのは稀だが、無いことは「機械である」の証拠にもならない。
    """
    assert _principal(sub="s").actor_type is ActorType.UNKNOWN


@pytest.mark.parametrize(
    ("idtyp", "expected"),
    [
        ("app", ActorType.SERVICE_PRINCIPAL),
        ("user", ActorType.USER),
    ],
)
def test_idtyp_があればそのまま読む(idtyp: str, expected: ActorType) -> None:
    assert _principal(sub="s", oid=_OID, idtyp=idtyp).actor_type is expected


@pytest.mark.parametrize("idtyp", ["device", "App", "APP", "", "unknown", "person"])
def test_知らない_idtyp_は_unknown_に畳む(idtyp: str) -> None:
    """**知らない値を既知のどれかに丸めない**(ADR-0035 決定2)。

    `device` は人間の説明責任を立証しない。大文字小文字の違いも畳む —
    `"App"` を `app` として読むと、仕様に無い綴りに意味を与えることになる。
    """
    assert _principal(sub="s", oid=_OID, idtyp=idtyp).actor_type is ActorType.UNKNOWN


@pytest.mark.parametrize("idtyp", [None, 1, ["app"], {"v": "app"}, True])
def test_文字列でない_idtyp_も_unknown_に畳む(idtyp: object) -> None:
    """**型を信用しない。** クレームは外部由来である。

    `str()` で潰すと `['app']` のような値が入り、対応表に無いので結果は
    同じだが、**真偽値は `str(True) == "True"` になる**ので畳み方を
    間違えると `"app"` に化ける経路を作りうる。型で先に落とす。
    """
    assert _principal(sub="s", oid=_OID, idtyp=idtyp).actor_type is ActorType.UNKNOWN


# ------------------------------------------------------------ 既定と逃げ道


def test_既定は_unknown_である() -> None:
    """**真偽値の既定(`False` = 人間)を置き換えた**(ADR-0035 決定1)。"""
    assert Principal(subject="s").actor_type is ActorType.UNKNOWN


def test_ローカル開発の主体も_unknown_である() -> None:
    """`AUTH_MODE=disabled` には検証したトークンが無い(ADR-0035 決定1)。

    人間だと名乗る根拠が無い。副作用として**ローカル開発の書き出しが
    「`idtyp` 未設定のテナント」と同じ見た目になる** — 運用者が設定漏れの
    状態を手元で見られる。
    """
    assert Principal.local_dev().actor_type is ActorType.UNKNOWN


def test_真偽値の派生プロパティを残していない() -> None:
    """**`is_service_principal` を復活させない**(ADR-0035 決定1)。

    真偽値は `unknown` を「サービスプリンシパルではない」に畳む。それが
    直したい誤りそのものなので、出口を用意しない。
    """
    assert not hasattr(Principal(subject="s"), "is_service_principal")


def test_種別は他のクレームの読み方を変えない() -> None:
    """`idtyp` を足したことで他のフィールドが壊れていないこと。"""
    principal = _principal(
        sub="s",
        oid=_OID,
        name="Alice",
        roles=["platform-admin"],
        idtyp="user",
    )
    assert principal.object_id == _OID
    assert principal.display_name == "Alice"
    assert principal.platform_roles == ("platform-admin",)
    assert principal.actor_type is ActorType.USER


def test_サービスプリンシパルの表示名は_app_displayname_から取る() -> None:
    """`name` が無いアプリ専用トークンでも主体が読める(既存の振る舞い)。"""
    principal = _principal(sub="s", oid=_OID, app_displayname="scan-job", idtyp="app")
    assert principal.display_name == "scan-job"
    assert principal.actor_type is ActorType.SERVICE_PRINCIPAL


# ------------------------------------------------------------ 語彙


def test_種別の値は_3_つだけである() -> None:
    """**列挙を広げるときは ADR-0035 決定2 を読むこと。**

    `audit_events.actor_type` に入る値であり、`AUTH_MODE=disabled` でも
    将来 Entra 以外の検証器を足したときにも同じ意味を持たなければならない。
    Entra の語彙(`idtyp`)をそのまま持ち込まないための歯止めである。
    """
    assert {t.value for t in ActorType} == {"user", "service-principal", "unknown"}
