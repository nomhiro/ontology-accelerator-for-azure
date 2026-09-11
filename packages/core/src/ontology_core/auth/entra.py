"""Microsoft Entra ID が発行したアクセストークンの検証。

人間の利用者は認可コードフロー、AI エージェントはクライアント資格情報フローで
トークンを取得し、いずれもここで検証する。名前空間ごとのロールは正本の PostgreSQL
側で解決するため、このモジュールは「誰であるか」までしか扱わない。
"""

from __future__ import annotations

from typing import Any

import jwt
from jwt import PyJWKClient
from pydantic import BaseModel, ConfigDict

from ontology_core.models import ActorType

__all__ = ["Principal", "TokenVerificationError", "TokenVerifier"]

_JWKS_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
_ISSUER_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/v2.0"

#: `idtyp` クレームから主体の種別への対応(ADR-0035 決定1)。
#:
#: **ここに無い値は `UNKNOWN` に畳む**(決定2)。`device` を別の値にしても
#: 「人間が説明責任を負うか」には答えられない。
_ACTOR_TYPE_BY_IDTYP: dict[str, ActorType] = {
    "app": ActorType.SERVICE_PRINCIPAL,
    "user": ActorType.USER,
}


class TokenVerificationError(Exception):
    """トークンが検証を通らなかったことを表す。"""


class Principal(BaseModel):
    """認証済みの主体。"""

    model_config = ConfigDict(frozen=True)

    subject: str
    object_id: str = ""
    display_name: str = ""
    platform_roles: tuple[str, ...] = ()
    #: 人間かサービスプリンシパルか(ADR-0035 決定1)。
    #:
    #: **既定は `UNKNOWN` である。** 真偽値ではないのは、`idtyp` が無いことが
    #: 「人間である」を意味しないためである(任意クレームなので、設定して
    #: いないテナントではサービスプリンシパルにも付かない)。
    actor_type: ActorType = ActorType.UNKNOWN

    @classmethod
    def local_dev(cls) -> Principal:
        """`AUTH_MODE=disabled` のときに使うローカル開発用の主体。

        デプロイ環境で使ってはならない。Entra ID にアプリ登録できない利用者が
        まず動かせるようにするための逃げ道である。

        **`actor_type` は `UNKNOWN` である**(ADR-0035 決定1)。検証した
        トークンが存在しないので、人間だと名乗る根拠が無い。副作用として
        ローカル開発の書き出しが「`idtyp` 未設定のテナント」と同じ見た目に
        なり、運用者が設定漏れの状態を手元で見られる。
        """
        return cls(
            subject="local-dev",
            object_id="00000000-0000-0000-0000-000000000000",
            display_name="Local Developer",
            platform_roles=("platform-admin",),
        )


class TokenVerifier:
    """テナントの JWKS を用いてアクセストークンを検証する。

    JWKS は `PyJWKClient` が内部でキャッシュするため、インスタンスはプロセス内で
    再利用すること。
    """

    def __init__(self, *, tenant_id: str, audience: str) -> None:
        if not tenant_id or not audience:
            raise ValueError("ENTRA_TENANT_ID と ENTRA_API_AUDIENCE の設定が必要です")
        self._audience = audience
        self._issuer = _ISSUER_TEMPLATE.format(tenant_id=tenant_id)
        self._jwks_client = PyJWKClient(_JWKS_URL_TEMPLATE.format(tenant_id=tenant_id))

    def verify(self, token: str) -> Principal:
        """トークンを検証し、主体を返す。

        Raises:
            TokenVerificationError: 署名・発行者・対象者のいずれかが妥当でないとき。
        """
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except Exception as exc:  # PyJWT は多様な例外を投げるためここで一本化する
            raise TokenVerificationError(f"トークンの検証に失敗しました: {exc}") from exc

        return self._to_principal(claims)

    @staticmethod
    def _to_principal(claims: dict[str, Any]) -> Principal:
        """クレームから主体を組み立てる。

        **`oid` の有無から種別を推測しない**(ADR-0035 決定1)。アプリ専用
        トークンにもサービスプリンシパルの `oid` は付くので、
        `"oid" not in claims` という判定は成り立たない。成り立たない向きが
        **自動化された行為を人間の行為として見せる**側なので、推測を捨てて
        `UNKNOWN` を返す。

        `idtyp` は任意クレームである。リソース側(この API のアプリ登録)に
        設定する手順は `scripts/setup-app-role.py` が持つ(決定7)。
        """
        roles = claims.get("roles") or []
        idtyp = claims.get("idtyp")
        actor_type = ActorType.UNKNOWN
        if isinstance(idtyp, str):
            actor_type = _ACTOR_TYPE_BY_IDTYP.get(idtyp, ActorType.UNKNOWN)
        return Principal(
            subject=str(claims.get("sub", "")),
            object_id=str(claims.get("oid", "")),
            display_name=str(claims.get("name") or claims.get("app_displayname") or ""),
            platform_roles=tuple(str(role) for role in roles),
            actor_type=actor_type,
        )
