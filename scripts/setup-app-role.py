"""Entra アプリ登録に `platform-admin` アプリロールと `idtyp` 任意クレームを設定する。

`P2A-09`(アプリロール)と `P2A-16`(`idtyp` 任意クレーム、
[ADR-0035](../docs/adr/0035-actor-type.md) 決定7)。

`P2A-06` で名前空間の作成と `POST /admin/reconcile` に `platform-admin` が必要に
なった([ADR-0014](../docs/adr/0014-namespace-rbac.md) 決定2・3)。**割り当てが
無いと `azd up` の `postdeploy` が 403 で止まる。**

使い方:

    # ENTRA_API_AUDIENCE(アプリ登録の appId)は azd 環境から拾う
    uv run python scripts/setup-app-role.py

    # 明示する / 他の主体(エージェントのサービスプリンシパル等)に割り当てる
    uv run python scripts/setup-app-role.py --app-id <appId> --principal-id <オブジェクトID>

    # 何をするかだけ見る
    uv run python scripts/setup-app-role.py --dry-run

    # 任意クレームの設定を飛ばす(アプリ登録の書き込み権限が無いとき)
    uv run python scripts/setup-app-role.py --skip-optional-claims

**なぜ Bicep でやらないのか**: Entra のアプリ登録は ARM のリソースではなく
`azd` の管理外にある(`azd down` でも消えない永続的な成果物。`P1-09` に記録)。
Bicep からアプリロールを定義することはできない。

**なぜシェルスクリプトではないのか**: `appRoles` は JSON の配列で、
POSIX sh と PowerShell の両方で正しくクォートするのが困難である。`az` を
呼ぶだけなら Python が最も移植性が高い(このリポジトリは既に uv に依存する)。

## `idtyp` 任意クレーム(`P2A-16`)

**設定しないと、監査証跡は人間と機械を永久に区別できない。** `idtyp` は
任意クレームで、設定していないテナントでは**サービスプリンシパルの
トークンにも付かない**。付かなければ `actor_type` は `unknown` のまま
記録され、PROV-O の `prov:Person` / `prov:SoftwareAgent` は出ない
(ADR-0035 決定1)。

設定するのは**リソース側**(この API のアプリ登録)である。クライアント側
ではない — アクセストークンはリソースが所有する。

**冪等である。** 既に同じ定義と割り当てがあれば何も送らない。同名のロールが
あれば **ID を再利用する** — ID を作り直すと既存の割り当て
(`appRoleAssignments` は ID で参照する)がすべて無効になり、運用者が自分の
権限を失う。既存の別のロールも落とさない(`appRoles` は複合プロパティなので
部分更新すると丸ごと消える。同じ罠を `api` で踏んでいる。`P1-09` の罠1)。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from ontology_core.auth.app_roles import merge_app_role
from ontology_core.auth.optional_claims import merge_optional_claim
from ontology_core.console import decode_output, say, warn
from ontology_core.models import PlatformRole

_GRAPH = "https://graph.microsoft.com/v1.0"
_ROLE_VALUE = PlatformRole.PLATFORM_ADMIN.value
_ROLE_DISPLAY_NAME = "Platform administrator"
_ROLE_DESCRIPTION = "名前空間の作成と POST /admin/reconcile を行える(ADR-0014)"

#: 主体の種別を判別するための任意クレーム(ADR-0035 決定7)。
_CLAIM_NAME = "idtyp"
# **変数名に "token" を入れない。** 静的解析ツールが S105
# (ハードコードされた資格情報)として誤検知する。
_CLAIM_TARGET = "accessToken"
#: ユーザートークンにも `idtyp` を出させる追加プロパティ。
#:
#: **実機で効くことを確認した**(2026-09-13。`P2A-18`、ADR-0035 補記1)。
#: v2.0 のアクセストークンに `idtyp: "user"` が乗る — 文書は v1.0 固有の節に
#: 載せているが、実際には v2.0 でも効く。**この組み合わせを崩さないこと。**
_CLAIM_ADDITIONAL = ("include_user_token",)


class AzError(RuntimeError):
    """`az` の呼び出しが失敗した。"""


def _az_path() -> str:
    """`az` のフルパスを返す(S607: 部分パスでのプロセス起動を避ける)。"""
    path = shutil.which("az")
    if path is None:
        raise AzError("az CLI が見つかりません(PATH を確認してください)")
    return path


def _az(*args: str) -> str:
    """`az` を実行して標準出力を返す。失敗したら stderr を添えて例外にする。

    **stderr を捨てない。** Graph の失敗は「権限が無い」「オブジェクトが無い」
    のどちらかで、対処が違う。理由を落とすと運用者が判断できない。

    **`text=True` を使わない**(実測で踏んだ)。`encoding="utf-8"` を指定すると、
    日本語 Windows で `az` が cp932 の警告を返したときに**読み取りスレッドが
    `UnicodeDecodeError` で死に、`proc.stdout` が `None` になる** — 呼び出し側
    には `AttributeError: 'NoneType' object has no attribute 'strip'` という
    **原因を指さない例外**が届く。バイト列で受けて `decode_output` に任せる。
    """
    proc = subprocess.run(  # noqa: S603 -- フルパス解決済み、固定の引数リストのみ
        [_az_path(), *args],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = decode_output(proc.stderr).strip()
        raise AzError(f"az {' '.join(args)} が失敗しました:\n{detail}")
    return decode_output(proc.stdout).strip()


def _az_json(*args: str) -> Any:
    out = _az(*args)
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise AzError(f"az の出力を JSON として読めません: {exc}") from exc


def _graph_get(url: str) -> Any:
    return _az_json("rest", "--method", "GET", "--url", url)


def _graph_send(method: str, url: str, body: dict[str, Any]) -> Any:
    """Graph へ JSON を送る。**本文はファイル経由で渡す。**

    **JSON をコマンドラインに載せてはいけない**(実測で踏んだ)。Windows の
    `az` は `az.cmd`(バッチ)で、`{"appRoles": [{"id": ...}]}` のような
    引数を**cmd が再解析して壊す** — 返ってくるのは
    `"..." の使い方が誤っています。` という**az でも Graph でもないエラー**で、
    原因が分からない。

    `az` は多くのパラメータで `@<ファイル>` による読み込みに対応しているので、
    一時ファイルに書いて渡す。**シェルの引用規則を一切通らない。**

    ファイルは必ず消す。**本文に秘密は含まれない**(ロール定義と ID のみ)が、
    一時ファイルを残す理由が無い。
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", encoding="utf-8", delete=False
    ) as handle:
        # **`encoding="utf-8"` を明示する。** 既定は cp932 になり、
        # ロールの説明(日本語)が壊れる。
        json.dump(body, handle, ensure_ascii=False)
        body_path = handle.name
    try:
        return _az_json(
            "rest",
            "--method",
            method,
            "--url",
            url,
            "--headers",
            "Content-Type=application/json",
            "--body",
            f"@{body_path}",
        )
    finally:
        pathlib.Path(body_path).unlink(missing_ok=True)


def _resolve_app_id(explicit: str | None) -> str:
    if explicit:
        return explicit
    from_env = os.environ.get("ENTRA_API_AUDIENCE", "").strip()
    if from_env:
        return from_env
    raise AzError(
        "アプリ登録の appId が分かりません。--app-id で渡すか、"
        "ENTRA_API_AUDIENCE を設定してください"
        "(azd 環境なら `azd env get-values | grep ENTRA_API_AUDIENCE`)"
    )


def _caller_principal() -> tuple[str, str]:
    """呼び出し元のオブジェクト ID と種別を返す。

    `preprovision` と同じ判定(ユーザーとして解決できなければ
    サービスプリンシパル)。**失敗の理由を捨てない**(`P1-14` の教訓)。
    """
    try:
        oid = _az("ad", "signed-in-user", "show", "--query", "id", "-o", "tsv")
        if oid:
            return oid, "User"
    except AzError as exc:
        warn(f"setup-app-role: signed-in user として解決できません: {exc}")
    app_id = _az("account", "show", "--query", "user.name", "-o", "tsv")
    oid = _az("ad", "sp", "show", "--id", app_id, "--query", "id", "-o", "tsv")
    return oid, "ServicePrincipal"


def _ensure_service_principal(app_id: str, *, dry_run: bool) -> str:
    """アプリ登録に対応するサービスプリンシパルのオブジェクト ID を返す。

    **無ければ作る。** 割り当ての `resourceId` はサービスプリンシパル側の
    オブジェクト ID であり、アプリ登録だけでは割り当てられない。

    **`--dry-run` では作らない**(実測で踏んだ)。以前はここだけ `dry_run` を
    受けておらず、**`--dry-run` が `az ad sp create` を実行していた** —
    書き込む dry-run は dry-run ではない。作らないので後続の割り当ては
    確かめられないが、**「作る」と言って終わるほうが正しい**。
    """
    try:
        oid = _az("ad", "sp", "show", "--id", app_id, "--query", "id", "-o", "tsv")
        if oid:
            return oid
    except AzError:
        say("setup-app-role: サービスプリンシパルが無いため作成します")
    if dry_run:
        say("setup-app-role: --dry-run のため作成しません(割り当ての確認はここで止まります)")
        return ""
    _az("ad", "sp", "create", "--id", app_id)
    return _az("ad", "sp", "show", "--id", app_id, "--query", "id", "-o", "tsv")


def _define_role(app_id: str, *, dry_run: bool) -> str:
    """アプリロールを定義し、その ID を返す。"""
    app = _az_json("ad", "app", "show", "--id", app_id)
    if not isinstance(app, dict):
        raise AzError(f"アプリ登録 '{app_id}' を取得できません")
    object_id = str(app.get("id") or "")
    if not object_id:
        raise AzError("アプリ登録のオブジェクト ID が取得できません")

    merged = merge_app_role(
        app.get("appRoles"),
        value=_ROLE_VALUE,
        display_name=_ROLE_DISPLAY_NAME,
        description=_ROLE_DESCRIPTION,
    )
    if not merged.changed:
        say(f"setup-app-role: '{_ROLE_VALUE}' は既に定義されています (id={merged.role_id})")
        return merged.role_id

    kept = [r.get("value") for r in merged.app_roles if r.get("value") != _ROLE_VALUE]
    say(
        f"setup-app-role: '{_ROLE_VALUE}' を定義します (id={merged.role_id})。"
        f"同時に送る既存のロール: {kept or 'なし'}"
    )
    if dry_run:
        say("setup-app-role: --dry-run のため送信しません")
        return merged.role_id
    # **完全な配列を送る。** appRoles は複合プロパティで、部分更新すると
    # 既存の定義が丸ごと消える。
    _graph_send("PATCH", f"{_GRAPH}/applications/{object_id}", {"appRoles": merged.app_roles})
    say("setup-app-role: 定義しました")
    return merged.role_id


def _ensure_optional_claim(app_id: str, *, dry_run: bool) -> None:
    """`idtyp` を任意クレームとして設定する(ADR-0035 決定7)。

    **完全な `optionalClaims` を送る。** `appRoles` と同じ複合プロパティで、
    部分更新すると既存の任意クレームが丸ごと消える(`P1-09` の罠1)。
    """
    app = _az_json("ad", "app", "show", "--id", app_id)
    if not isinstance(app, dict):
        raise AzError(f"アプリ登録 '{app_id}' を取得できません")
    object_id = str(app.get("id") or "")
    if not object_id:
        raise AzError("アプリ登録のオブジェクト ID が取得できません")

    existing = app.get("optionalClaims")
    merged = merge_optional_claim(
        existing if isinstance(existing, dict) else None,
        token_type=_CLAIM_TARGET,
        name=_CLAIM_NAME,
        additional_properties=_CLAIM_ADDITIONAL,
    )
    if not merged.changed:
        say(f"setup-app-role: 任意クレーム '{_CLAIM_NAME}' は既に設定されています")
        return

    kept = sum(
        len([c for c in claims if c.get("name") != _CLAIM_NAME])
        for claims in merged.optional_claims.values()
    )
    say(
        f"setup-app-role: 任意クレーム '{_CLAIM_NAME}' を {_CLAIM_TARGET} に設定します。"
        f"同時に送る既存のクレーム: {kept} 件"
    )
    if dry_run:
        say("setup-app-role: --dry-run のため送信しません")
        return
    _graph_send(
        "PATCH",
        f"{_GRAPH}/applications/{object_id}",
        {"optionalClaims": merged.optional_claims},
    )
    say("setup-app-role: 設定しました")


def _assign(
    *, sp_object_id: str, role_id: str, principal_id: str, principal_type: str, dry_run: bool
) -> None:
    """ロールを割り当てる。既にあれば何もしない。"""
    existing = _graph_get(f"{_GRAPH}/servicePrincipals/{sp_object_id}/appRoleAssignedTo")
    assignments = existing.get("value", []) if isinstance(existing, dict) else []
    for a in assignments:
        if a.get("principalId") == principal_id and a.get("appRoleId") == role_id:
            say(f"setup-app-role: {principal_type} {principal_id} には既に割り当て済みです")
            return

    say(f"setup-app-role: {principal_type} {principal_id} に割り当てます")
    if dry_run:
        say("setup-app-role: --dry-run のため送信しません")
        return
    _graph_send(
        "POST",
        f"{_GRAPH}/servicePrincipals/{sp_object_id}/appRoleAssignedTo",
        {"principalId": principal_id, "resourceId": sp_object_id, "appRoleId": role_id},
    )
    say("setup-app-role: 割り当てました")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Entra アプリ登録に '{_ROLE_VALUE}' を定義して割り当てる"
    )
    parser.add_argument(
        "--app-id",
        help="アプリ登録の appId。省略時は ENTRA_API_AUDIENCE を使う",
    )
    parser.add_argument(
        "--principal-id",
        help="割り当て先のオブジェクト ID。省略時は az にログイン中の主体",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="何をするかだけ表示し、Entra を変更しない",
    )
    parser.add_argument(
        "--skip-optional-claims",
        action="store_true",
        help=f"'{_CLAIM_NAME}' 任意クレームの設定を飛ばす(ADR-0035 決定7)。"
        "飛ばすと監査証跡は主体の種別を unknown のまま記録する",
    )
    args = parser.parse_args()

    try:
        app_id = _resolve_app_id(args.app_id)
        say(f"setup-app-role: アプリ登録 {app_id} を対象にします")
        role_id = _define_role(app_id, dry_run=args.dry_run)
        if args.skip_optional_claims:
            warn(
                f"setup-app-role: --skip-optional-claims のため '{_CLAIM_NAME}' を"
                "設定しません。**監査証跡は主体の種別を unknown のまま記録します**"
                "(ADR-0035 決定7)"
            )
        else:
            _ensure_optional_claim(app_id, dry_run=args.dry_run)
        sp_object_id = _ensure_service_principal(app_id, dry_run=args.dry_run)
        if args.principal_id:
            principal_id, principal_type = args.principal_id, "指定された主体"
        else:
            principal_id, principal_type = _caller_principal()
        if not sp_object_id:
            # `--dry-run` でサービスプリンシパルを作らなかった場合だけここに来る。
            # **「確かめられなかった」と言う。** 割り当て済みかどうかは
            # サービスプリンシパルが無いと問い合わせられない。
            say(
                f"setup-app-role: {principal_type} {principal_id} への割り当ては、"
                "サービスプリンシパルを作ってからでないと確かめられません"
            )
        else:
            _assign(
                sp_object_id=sp_object_id,
                role_id=role_id,
                principal_id=principal_id,
                principal_type=principal_type,
                dry_run=args.dry_run,
            )
    except AzError as exc:
        warn(f"setup-app-role: {exc}")
        return 1

    if not args.dry_run:
        # **既に手元にあるトークンには反映されない。**
        say(
            "setup-app-role: 完了しました。**トークンを取り直してください**"
            "(反映には数分かかることがあります)。確認:\n"
            f'  az account get-access-token --scope "api://{app_id}/.default" '
            "--query accessToken -o tsv | uv run python scripts/check-platform-admin.py"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
