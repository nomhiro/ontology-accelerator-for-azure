#!/bin/sh
# API の起動ラッパー。マイグレーションを適用してからサーバーを起動する。
#
# Alembic は同時実行を直列化しないため、複数レプリカが同時に起動すると
# alembic_version テーブルの作成が衝突して片方が UniqueViolationError で落ちる
# (実測で再現済み)。PostgreSQL のアドバイザリロックで直列化する。
set -eu
echo "entrypoint: DB マイグレーションを適用します" >&2
python -m ontology_api.migrate     # ロック取得 → alembic upgrade → 解放
exec uvicorn ontology_api.main:app --host 0.0.0.0 --port 8000
