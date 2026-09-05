#!/bin/sh
# API の起動ラッパー。
#
# **かつてはここでマイグレーションを実行していた(Task 8)。ADR-0011 でその判断を
# 覆した。** 監査証跡(audit_events)を API を掌握した攻撃者からも守るため、
# テーブルの所有者(`ontology_owner`)とアプリの実行時ロール(この UAMI)を分離
# した。所有者は常に DROP/TRUNCATE できるため、サーバー管理者を外すだけでは
# 監査を守れない — 所有権そのものを分けるしかなく、その結果アプリのロールには
# DDL 権限が無くなった。ここで `alembic upgrade head` を実行すると起動時に必ず
# 失敗し、`set -eu` のため uvicorn 自体が起動しなくなる。
#
# マイグレーションは `postdeploy` フックで運用者(Entra 管理者・`ontology_owner`
# のメンバー)が実行する(scripts/postdeploy.sh / .ps1、scripts/bootstrap-db.py)。
# `ontology_api.migrate`(アドバイザリロックによる直列化)は変更なしで
# `just migrate`(ローカル開発)と postdeploy の両方から使われ続ける。
#
# この帰結として、API は **マイグレーション未適用のままデプロイ直後に起動しうる**
# (`/healthz` は DB を触らないため Startup / Liveness プローブは通る。DB を触る
# エンドポイントは postdeploy 完了までエラーを返す。ADR-0011 決定5)。
set -eu
exec uvicorn ontology_api.main:app --host 0.0.0.0 --port 8000
