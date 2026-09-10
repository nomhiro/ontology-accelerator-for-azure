"""PostgreSQL の権限分離を初期化するブートストラップ SQL(ADR-0011 決定2)。

**目的**: `audit_events` を含む全テーブルの所有者を `ontology_owner`(NOLOGIN)に
し、アプリの実行時ロール(UAMI)には DML のみを与える。所有者とアプリのロールを
分けないと、アプリのロールが `DROP` / `TRUNCATE` できてしまい、監査証跡を API を
掌握した攻撃者から守れない([ADR-0011](../docs/adr/0011-database-privilege-separation.md)
の根拠)。

`postdeploy` から2回呼ばれる(順序が重要)。

    python scripts/bootstrap-db.py pre    # alembic upgrade head の前
    python scripts/bootstrap-db.py post   # alembic upgrade head の後

`pre` は `ontology_owner` の作成・UAMI の pgaadauth 登録・将来のテーブルへの
既定権限(`ALTER DEFAULT PRIVILEGES`)を設定する。これは `CREATE TABLE` より前で
なければ新しく作られるテーブルに権限が付かない。`post` は既存テーブルへの GRANT
と、`audit_events` の `DELETE` の剥奪(追記専用にする)を行う。`audit_events` は
マイグレーションで初めて作られるため、`post` は必ず `alembic upgrade head` の
**後**に実行する。

**接続を2つ使う理由**: `pgaadauth_create_principal` は Azure Database for
PostgreSQL の `postgres`(メンテナンス用)データベースにしか存在しない拡張関数で、
アプリの DB(既定 `ontology`)から呼ぶと
`function pgaadauth_create_principal(...) does not exist` になる(実測および
Microsoft Q&A で複数件確認: https://learn.microsoft.com/answers/questions/1655608/)。
ロール(`ontology_owner` や pgaadauth で作るロール)はクラスタ全体で共有されるため、
`postgres` DB で作ったロールをアプリの DB からの `GRANT` で使うことに問題は無い。

**冪等性**: `CREATE ROLE` は既に存在すると失敗するため `pg_roles` を先に引く。
`GRANT` / `ALTER DEFAULT PRIVILEGES` / `REVOKE` は PostgreSQL 側で重複実行しても
エラーにならない(no-op)ため、追加のガードは不要。

**接続情報**:
- `POSTGRES_HOST` / `POSTGRES_PORT`(既定 5432) / `POSTGRES_DATABASE`(既定 ontology):
  既存の azd 出力・`ontology_core.config.Settings` と同じ環境変数名
- `POSTGRES_ADMIN_USER`: このスクリプトが**接続する**識別子(= Entra 管理者として
  登録された運用者)。Azure では `AZURE_PRINCIPAL_NAME`(preprovision フックが
  設定)、ローカルのパスワード認証テストでは任意の管理者ロール名を渡す
- `POSTGRES_ADMIN_PASSWORD`: 設定されていればパスワード認証(ローカル開発・テスト
  専用)。空なら `az account get-access-token --resource-type oss-rdbms` で Entra
  トークンを取得する(運用者自身の az CLI ログインを使う。scripts/postdeploy.sh の
  トークン取得と同じ考え方)
- `POSTGRES_APP_ROLE`: DML のみを与える対象ロール名(= UAMI の名前。既存の
  `POSTGRES_USER` 出力と同じ値を渡す想定)

パスワード認証(`POSTGRES_ADMIN_PASSWORD` 設定あり = ローカル開発)で
`pgaadauth_create_principal` が見つからない場合はエラーにせずスキップする(拡張が
無い vanilla PostgreSQL への対応)。Entra トークン認証(Azure 実機)で見つからない
場合は、拡張が無効化されている実際の問題なので**握りつぶさずエラーにする**。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys

import asyncpg

_OWNER_ROLE = "ontology_owner"
# pgaadauth_create_principal が存在する Azure のメンテナンス用データベース。
_MAINTENANCE_DATABASE = "postgres"

# ファイアウォール規則反映直後の一時的な接続失敗を吸収するための再試行。
_CONNECT_ATTEMPTS = 6
_CONNECT_RETRY_SECONDS = 5.0


def _quote_ident(name: str) -> str:
    """SQL識別子として安全に埋め込む(二重引用符で囲み、内部の `"` は `""` にする)。

    UAMI 名はハイフンを含む(例: `id-xxxxxxxx`)ため無引用では構文エラーになる。
    運用者の UPN も `#` を含むことがあるが、こちらは `CURRENT_USER` 経由で扱うため
    (このモジュールでは)識別子として直接埋め込むことはない。
    """
    return '"' + name.replace('"', '""') + '"'


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} が設定されていません")
    return value


def _admin_password() -> str:
    """管理者接続のパスワード(または Entra トークン)を返す。"""
    password = os.environ.get("POSTGRES_ADMIN_PASSWORD", "")
    if password:
        return password
    # Entra トークンを取得する(運用者自身の az CLI ログインを使う)。
    # フルパスを解決してから渡す(S607: 部分パスでのプロセス起動を避ける)。
    az_path = shutil.which("az")
    if az_path is None:
        raise RuntimeError("az CLI が見つかりません(PATH を確認してください)")
    proc = subprocess.run(  # noqa: S603 -- フルパス解決済み、固定の引数リストのみで外部入力は関与しない
        [
            az_path,
            "account",
            "get-access-token",
            "--resource-type",
            "oss-rdbms",
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    token = proc.stdout.strip()
    if not token:
        raise RuntimeError("Entra トークンを取得できませんでした(az account get-access-token)")
    return token


async def _connect(database: str) -> asyncpg.Connection:
    """運用者(管理者)として指定したデータベースへ接続する。"""
    host = _require_env("POSTGRES_HOST")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    user = _require_env("POSTGRES_ADMIN_USER")
    password = _admin_password()

    last_error: Exception | None = None
    for attempt in range(1, _CONNECT_ATTEMPTS + 1):
        try:
            return await asyncpg.connect(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
            )
        except (OSError, asyncpg.PostgresError) as exc:
            last_error = exc
            if attempt < _CONNECT_ATTEMPTS:
                print(
                    f"bootstrap-db: {database} への接続に失敗しました "
                    f"({attempt}/{_CONNECT_ATTEMPTS})、再試行します: {exc}",
                    file=sys.stderr,
                )
                await asyncio.sleep(_CONNECT_RETRY_SECONDS)
    assert last_error is not None
    raise last_error


async def _ensure_owner_role(conn: asyncpg.Connection) -> None:
    """`ontology_owner`(NOLOGIN)を作り、接続中の運用者にメンバーシップを与える。

    メンバーシップを明示的に GRANT するのは、PostgreSQL 16 でロール作成者への
    暗黙のメンバーシップが `WITH SET FALSE` になったため(明示的な GRANT が
    無いと、後続の `SET ROLE ontology_owner`(alembic env.py)が失敗する)。
    """
    exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", _OWNER_ROLE)
    if exists:
        print(f"bootstrap-db: ロール {_OWNER_ROLE} は既にあります")
    else:
        await conn.execute(f"CREATE ROLE {_quote_ident(_OWNER_ROLE)} NOLOGIN")
        print(f"bootstrap-db: ロール {_OWNER_ROLE} を作成しました")
    await conn.execute(f"GRANT {_quote_ident(_OWNER_ROLE)} TO CURRENT_USER")


async def _ensure_app_principal(conn: asyncpg.Connection, app_role: str) -> None:
    """UAMI を PostgreSQL のロールとして登録する(pgaadauth 拡張、`postgres` DB上)。"""
    has_pgaadauth = await conn.fetchval(
        "SELECT EXISTS ("
        "  SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        "  WHERE n.nspname = 'pg_catalog' AND p.proname = 'pgaadauth_create_principal'"
        ")"
    )
    if not has_pgaadauth:
        using_password_auth = bool(os.environ.get("POSTGRES_ADMIN_PASSWORD", ""))
        if using_password_auth:
            # ローカル開発(vanilla PostgreSQL)には pgaadauth 拡張が無い。
            print(
                "bootstrap-db: pgaadauth_create_principal が見つかりません"
                "(パスワード認証接続のためローカル開発と判断してスキップします)"
            )
            return
        raise RuntimeError(
            "pgaadauth_create_principal が見つかりません。Entra 認証が有効な "
            "Azure Database for PostgreSQL の 'postgres' データベースに接続しているか "
            "確認してください"
        )

    already = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", app_role)
    if already:
        print(f"bootstrap-db: ロール {app_role} は既にあります(pgaadauth 登録済み)")
        return
    await conn.fetch("SELECT * FROM pgaadauth_create_principal($1, false, false)", app_role)
    print(f"bootstrap-db: ロール {app_role} を pgaadauth で作成しました")


async def _pre(app_role: str, database: str) -> None:
    """マイグレーション**前**に実行する部分。"""
    maintenance = await _connect(_MAINTENANCE_DATABASE)
    try:
        await _ensure_owner_role(maintenance)
        await _ensure_app_principal(maintenance, app_role)
    finally:
        await maintenance.close()

    quoted_owner = _quote_ident(_OWNER_ROLE)
    quoted_role = _quote_ident(app_role)
    conn = await _connect(database)
    try:
        # PostgreSQL 15 以降、public スキーマの CREATE は PUBLIC から剥奪されている
        # ため、ontology_owner(マイグレーションを SET ROLE で実行する側)に明示的に
        # 与える(ブリーフ記載の SQL には無かったが、無いと alembic upgrade head が
        # 'permission denied for schema public' で失敗する。実機で確認済み)。
        await conn.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {quoted_owner}")
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {quoted_role}")
        await conn.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {quoted_owner} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {quoted_role}"
        )
        await conn.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {quoted_owner} IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {quoted_role}"
        )
    finally:
        await conn.close()
    print("bootstrap-db: pre フェーズが完了しました")


async def _post(app_role: str, database: str) -> None:
    """マイグレーション**後**に実行する部分。"""
    quoted_role = _quote_ident(app_role)
    conn = await _connect(database)
    try:
        await conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {quoted_role}"
        )
        await conn.execute(
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {quoted_role}"
        )
        # 監査は追記専用にする(ADR-0011 決定2)。audit_events はマイグレーションで
        # 初めて作られるため、この post フェーズは alembic upgrade head の後で
        # なければならない。
        #
        # **`access_events` には DELETE を残す**(ADR-0018 決定2)。性質が違う —
        # `audit_events` は人の決定の記録で件数が緩やかに増え、消す理由が無い。
        # `access_events` は機械の参照の記録で、エージェントの稼働に比例して
        # 無限に伸びる。ただし削除は運用者の明示的な操作
        # (`POST /admin/access-log/purge`)に限り、**削除したこと自体を
        # `audit_events` に記録する**ので、消えた事実は消せない場所に残る。
        await conn.execute(f"REVOKE DELETE ON audit_events FROM {quoted_role}")
    finally:
        await conn.close()
    print("bootstrap-db: post フェーズが完了しました")


async def _run(phase: str) -> None:
    app_role = _require_env("POSTGRES_APP_ROLE")
    database = os.environ.get("POSTGRES_DATABASE", "ontology")
    if phase == "pre":
        await _pre(app_role, database)
    else:
        await _post(app_role, database)


def main() -> int:
    parser = argparse.ArgumentParser(description="PostgreSQL の権限分離ブートストラップ")
    parser.add_argument("phase", choices=["pre", "post"])
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.phase))
    except Exception as exc:  # ここで捕まえて分かりやすいメッセージにする
        print(f"bootstrap-db: 失敗しました: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
