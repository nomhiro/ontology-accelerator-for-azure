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

__all__ = ["Principal", "TokenVerificationError", "TokenVerifier"]

_JWKS_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
_ISSUER_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/v2.0"


class TokenVerificationError(Exception):
    """トークンが検証を通らなかったことを表す。"""


class Principal(BaseModel):
    """認証済みの主体。"""

    model_config = ConfigDict(frozen=True)

    subject: str
    object_id: str = ""
    display_name: str = ""
    platform_roles: tuple[str, ...] = ()
    is_service_principal: bool = False

    @classmethod
    def local_dev(cls) -> Principal:
        """`AUTH_MODE=disabled` のときに使うローカル開発用の主体。

        デプロイ環境で使ってはならない。Entra ID にアプリ登録できない利用者が
        まず動かせるようにするための逃げ道である。
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
        roles = claims.get("roles") or []
        # クライアント資格情報フローのトークンには idtyp=app が付く。
        is_app = claims.get("idtyp") == "app" or "oid" not in claims
        return Principal(
            subject=str(claims.get("sub", "")),
            object_id=str(claims.get("oid", "")),
            display_name=str(claims.get("name") or claims.get("app_displayname") or ""),
            platform_roles=tuple(str(role) for role in roles),
            is_service_principal=is_app,
        )
