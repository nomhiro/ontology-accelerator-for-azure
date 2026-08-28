# Core API のコンテナイメージ。
# uv のワークスペース内で packages/core を参照するため、
# ビルドコンテキストは**リポジトリのルート**であること。
#   docker build -f packages/api/Dockerfile -t ontology-api .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# ワークスペースの解決に必要なファイルのみを先に入れて依存解決をキャッシュする。
COPY pyproject.toml ./
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY packages/api/pyproject.toml packages/api/pyproject.toml
COPY packages/mcp-server/pyproject.toml packages/mcp-server/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-workspace --package ontology-api

# ソースを入れてワークスペースのパッケージ自体をインストールする。
COPY packages/core packages/core
COPY packages/api packages/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --package ontology-api


FROM python:3.12-slim-bookworm AS runtime

# 非 root で動かす。
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/packages /app/packages

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser
EXPOSE 8000

CMD ["uvicorn", "ontology_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
