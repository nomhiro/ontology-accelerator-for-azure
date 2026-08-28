"""Core API のエントリポイント。

FastAPI / Pydantic をスキーマの正本とし、生成された `openapi.json` から Web 用の
TypeScript 型を作る(`docs/adr/0004-api-contract-strategy.md`)。
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ontology_api import __version__
from ontology_api.routers import namespaces, sparql
from ontology_core.config import AuthMode, get_settings

_settings = get_settings()
logging.basicConfig(level=_settings.log_level.upper())
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Ontology Accelerator for Azure - Core API",
    version=__version__,
    description=(
        "オントロジーの管理と、AI エージェントへのコンテキスト提供を担う API。"
        "トリプルストアへの書き込み口はこの API のみ。"
    ),
)

# Web (Static Web Apps) からの呼び出しを許可する。デプロイ時は許可元を絞ること。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _settings.auth_mode is AuthMode.DISABLED else [],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(namespaces.router)
app.include_router(sparql.router)

if _settings.auth_mode is AuthMode.DISABLED:
    logger.warning(
        "AUTH_MODE=disabled で起動しています。トークン検証を行いません。ローカル開発専用の設定です"
    )


class HealthResponse(BaseModel):
    """ヘルスチェックの応答。"""

    status: Literal["ok"]
    version: str
    auth_mode: AuthMode


@app.get("/healthz", tags=["meta"], summary="プロセスの生存確認")
async def healthz() -> HealthResponse:
    """プロセスが応答可能かどうかだけを返す。

    依存先(ストア・PostgreSQL)の到達性は含めない。Container Apps の liveness probe
    が依存先の一時的な不調でレプリカを落とさないようにするため。
    """
    return HealthResponse(status="ok", version=__version__, auth_mode=_settings.auth_mode)
