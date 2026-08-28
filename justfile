# 開発タスク。Windows と Linux / macOS の両方で同じコマンドが使えるように、
# シェル固有の記法を避けて uv / pnpm / docker のサブコマンドに寄せている。
#
#   just            タスク一覧を表示する
#   just setup      依存関係を入れる
#   just check      lint と型検査とテストをまとめて実行する

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# 既定のタスク: 一覧表示
default:
    @just --list

# ---------------------------------------------------------------------------
# セットアップ
# ---------------------------------------------------------------------------

# Python と Node の依存関係を入れる
setup: setup-py setup-web

setup-py:
    uv sync --all-packages

setup-web:
    pnpm install

# ---------------------------------------------------------------------------
# 検査
# ---------------------------------------------------------------------------

# lint・型検査・テストをまとめて実行する(CI と同じ内容)
check: lint typecheck test

lint:
    uv run ruff check .
    uv run ruff format --check .

# 自動整形する
fmt:
    uv run ruff check --fix .
    uv run ruff format .

typecheck:
    uv run mypy packages

typecheck-web:
    pnpm --filter @ontology-accelerator/web typecheck

test:
    uv run pytest

# Bicep をビルドして構文を検証する
lint-infra:
    az bicep build --file infra/main.bicep --stdout

# ---------------------------------------------------------------------------
# ローカル実行
# ---------------------------------------------------------------------------

# Fuseki と PostgreSQL を起動する
up:
    docker compose up -d --build

# 停止する(データは残る)
down:
    docker compose down

# 停止してデータも消す
clean:
    docker compose down -v

# Core API を起動する(要: just up)
dev-api:
    uv run uvicorn ontology_api.main:app --reload --port 8000

# MCP サーバーを起動する(要: just dev-api)
dev-mcp:
    uv run uvicorn ontology_mcp.server:build_app --factory --reload --port 8080

# Web を起動する
dev-web:
    pnpm --filter @ontology-accelerator/web dev

# ---------------------------------------------------------------------------
# API 契約
# ---------------------------------------------------------------------------

# FastAPI の OpenAPI から Web 用の TypeScript 型を生成する
gen-api:
    uv run python -c "import json; from ontology_api.main import app; print(json.dumps(app.openapi(), ensure_ascii=False, indent=2))" > openapi.json
    pnpm --filter @ontology-accelerator/web gen:api

# ---------------------------------------------------------------------------
# デプロイ
# ---------------------------------------------------------------------------

# Azure にデプロイする
deploy:
    azd up

# Azure から削除する
destroy:
    azd down --purge
