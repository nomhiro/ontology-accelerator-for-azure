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

# lint・型検査・テストをまとめて実行する(integration は含まない)
check: lint typecheck test

# lint・型検査・テスト・integrationテストをすべて実行する(CI と同じ内容)
check-all: lint typecheck test test-integration

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

# 単体テスト(DB不要)
test:
    uv run pytest -m "not integration"

# integrationテスト(要: just up)
test-integration:
    uv run pytest -m integration

# Bicep をビルドして構文を検証する
lint-infra:
    az bicep build --file infra/main.bicep --stdout

# ---------------------------------------------------------------------------
# ローカル実行
# ---------------------------------------------------------------------------

# Fuseki / PostgreSQL / Azurite を起動し、正本 Blob のコンテナを作る
#
# Azurite にはコンテナを自動作成する仕組みが無い(本番は
# infra/modules/shared.bicep の ontologyContainer が作る)。コンテナが無いと
# publish と DELETE /namespaces/{name} が ContainerNotFound で 500 になるため、
# ここで作る。冪等なので何度実行してもよい。
up:
    docker compose up -d --build
    uv run --directory {{justfile_directory()}} python scripts/init-local-storage.py

# 停止する(データは残る)
down:
    docker compose down

# 停止してデータも消す
clean:
    docker compose down -v

# DBマイグレーションを適用する(要: just up、.env を用意しておくこと)
#
# `cd packages/api && ...` は使わない。Windows PowerShell 5.1 (powershell.exe)
# は `&&` をステートメント区切りとして解釈できずに壊れる
# (fix(justfile): gen-api がPowerShellで壊れるのを修正 と同種の罠)。
# `uv run --directory` でシェルをまたいで安全に作業ディレクトリを切り替える。
#
# `--directory` は `.env` の探索(pydantic-settings が cwd 相対で読む)も
# `packages/api` 基準に変えてしまい、リポジトリルートの `.env` が無視されて
# 既定値(AUTH_MODE=entra・POSTGRES_PASSWORD 空)にフォールバックする
# (実測: `.env` があるのに Entra 経路に切り替わりローカル PostgreSQL の
# 認証に失敗する)。`--env-file` で明示的にルートの `.env` を渡し、
# `--directory` より前に環境変数として展開させることで回避する。
migrate:
    uv run --env-file {{justfile_directory()}}/.env --directory packages/api alembic upgrade head

# 新しいマイグレーションを生成する
migrate-new message:
    uv run --env-file {{justfile_directory()}}/.env --directory packages/api alembic revision --autogenerate -m "{{message}}"

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
#
# ファイルの書き出しはシェルのリダイレクトに任せず Python 側で行う。
# PowerShell の `>` は UTF-16LE で書き出すため、Node (openapi-typescript) が
# 解釈できない JSON ができてしまう。
gen-api:
    uv run python -c "import json, pathlib; from ontology_api.main import app; pathlib.Path('openapi.json').write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding='utf-8')"
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
