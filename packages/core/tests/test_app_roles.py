"""`appRoles` の合成のテスト(`P2A-09`)。

**守りたいのは「既存の定義を消さないこと」と「ID を作り直さないこと」**である。
どちらも、壊れたときにエラーにならず**静かに権限を失う**形で表面化する。
"""

from __future__ import annotations

from typing import Any

from ontology_core.auth.app_roles import merge_app_role

_ARGS: dict[str, Any] = {
    "value": "platform-admin",
    "display_name": "Platform administrator",
    "description": "名前空間の作成と reconcile を行える",
}


def test_空から新規に作る() -> None:
    result = merge_app_role([], **_ARGS, new_role_id="11111111-1111-1111-1111-111111111111")
    assert result.changed is True
    assert result.role_id == "11111111-1111-1111-1111-111111111111"
    assert len(result.app_roles) == 1
    role = result.app_roles[0]
    assert role["value"] == "platform-admin"
    assert role["isEnabled"] is True
    assert role["allowedMemberTypes"] == ["User", "Application"]


def test_None_を空として扱う() -> None:
    """Graph はロールが 1 件も無いとき `appRoles` を空配列で返すが、
    `az` の出力経路によっては欠落しうる。落ちないようにする。"""
    result = merge_app_role(None, **_ARGS)
    assert len(result.app_roles) == 1


def test_既存の別のロールを落とさない() -> None:
    """**これが本題。** `appRoles` は複合プロパティなので、部分更新すると
    既存が丸ごと消える。合成の結果に既存が残っていなければ、そのまま送った
    時点で他のロールが消滅する。"""
    existing = [
        {
            "id": "aaaaaaaa-0000-0000-0000-000000000000",
            "allowedMemberTypes": ["User"],
            "value": "ontology-reader",
            "displayName": "Reader",
            "description": "既存の別ロール",
            "isEnabled": True,
        }
    ]
    result = merge_app_role(existing, **_ARGS)
    values = [r["value"] for r in result.app_roles]
    assert values == ["ontology-reader", "platform-admin"]
    # 既存の定義に手を入れていないこと(型も説明も変えない)。
    assert result.app_roles[0] == existing[0]


def test_既存の同名ロールの_ID_を再利用する() -> None:
    """**ID を作り直してはいけない。** `appRoleAssignments` は ID で参照するので、
    ID を変えると既存の割り当てが全部無効になり、**運用者が自分の権限を失う**。"""
    existing = [
        {
            "id": "bbbbbbbb-0000-0000-0000-000000000000",
            "allowedMemberTypes": ["User", "Application"],
            "value": "platform-admin",
            "displayName": "Platform administrator",
            "description": "名前空間の作成と reconcile を行える",
            "isEnabled": True,
        }
    ]
    result = merge_app_role(existing, **_ARGS, new_role_id="cccccccc-0000-0000-0000-000000000000")
    assert result.role_id == "bbbbbbbb-0000-0000-0000-000000000000"
    assert result.changed is False, "同じ定義なら PATCH を送る必要がない"
    assert len(result.app_roles) == 1


def test_無効化されている同名ロールを有効化する() -> None:
    """`isEnabled: false` のロールはトークンに乗らない。
    「定義はあるのに効かない」状態は原因が分かりにくい。"""
    existing = [
        {
            "id": "dddddddd-0000-0000-0000-000000000000",
            "allowedMemberTypes": ["User", "Application"],
            "value": "platform-admin",
            "displayName": "Platform administrator",
            "description": "名前空間の作成と reconcile を行える",
            "isEnabled": False,
        }
    ]
    result = merge_app_role(existing, **_ARGS)
    assert result.changed is True
    assert result.app_roles[0]["isEnabled"] is True
    assert result.role_id == "dddddddd-0000-0000-0000-000000000000"


def test_アプリケーションが許可されていなければ足す() -> None:
    """エージェント(クライアント資格情報フロー)にも割り当てられる必要がある。
    `User` だけの定義だとサービスプリンシパルに割り当てられない。"""
    existing = [
        {
            "id": "eeeeeeee-0000-0000-0000-000000000000",
            "allowedMemberTypes": ["User"],
            "value": "platform-admin",
            "displayName": "Platform administrator",
            "description": "名前空間の作成と reconcile を行える",
            "isEnabled": True,
        }
    ]
    result = merge_app_role(existing, **_ARGS)
    assert result.changed is True
    assert result.app_roles[0]["allowedMemberTypes"] == ["User", "Application"]


def test_入力の辞書を書き換えない() -> None:
    """呼び出し側が「送る前の状態」と比較できるようにする。
    ここで入力を破壊すると、差分の判断が壊れる。"""
    existing = [
        {
            "id": "ffffffff-0000-0000-0000-000000000000",
            "allowedMemberTypes": ["User"],
            "value": "platform-admin",
            "displayName": "old",
            "description": "old",
            "isEnabled": False,
        }
    ]
    snapshot = [dict(r) for r in existing]
    merge_app_role(existing, **_ARGS)
    assert existing == snapshot


def test_ID_が欠けている同名ロールは作り直す() -> None:
    existing = [{"value": "platform-admin", "isEnabled": True}]
    result = merge_app_role(existing, **_ARGS, new_role_id="99999999-0000-0000-0000-000000000000")
    assert result.role_id == "99999999-0000-0000-0000-000000000000"
    assert result.changed is True


def test_新規の_ID_は毎回異なる() -> None:
    a = merge_app_role([], **_ARGS)
    b = merge_app_role([], **_ARGS)
    assert a.role_id != b.role_id
