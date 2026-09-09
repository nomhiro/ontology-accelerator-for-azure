"""Entra アプリ登録の `appRoles` の合成(`P2A-09`)。

`scripts/setup-app-role.py` が使う純粋関数を置く。**なぜコアに置くのか**:
`appRoles` は Microsoft Graph の**複合プロパティ**で、部分更新すると
**既存の定義が丸ごと消える**。同じ罠を `api`(スコープと
`preAuthorizedApplications`)で既に踏んでいる(`P1-09` の罠1)。
消してはいけないものを消さないことは自動テストで固定する価値がある。

このモジュールは Graph を呼ばない。**辞書を受け取って辞書を返すだけ**である。
"""

from __future__ import annotations

import uuid
from typing import Any

__all__ = ["AppRoleMerge", "merge_app_role"]


class AppRoleMerge:
    """`appRoles` を合成した結果。

    Attributes:
        app_roles: PATCH でそのまま送る**完全な**配列。
        role_id: 対象のロールの ID(既存なら再利用した値)。
        changed: 送る必要があるか。既に同じ定義があれば `False`。
    """

    __slots__ = ("app_roles", "changed", "role_id")

    def __init__(self, *, app_roles: list[dict[str, Any]], role_id: str, changed: bool) -> None:
        self.app_roles = app_roles
        self.role_id = role_id
        self.changed = changed


def merge_app_role(
    existing: list[dict[str, Any]] | None,
    *,
    value: str,
    display_name: str,
    description: str,
    allowed_member_types: tuple[str, ...] = ("User", "Application"),
    new_role_id: str | None = None,
) -> AppRoleMerge:
    """`value` のロールを含む**完全な** `appRoles` を返す。

    - **既存のロールは 1 件も落とさない。** 部分更新で消える性質があるため、
      呼び出し側は常にこの戻り値の全体を送る
    - **同じ `value` が既にあれば ID を再利用する。** ID を作り直すと、
      既存の割り当て(`appRoleAssignments` は ID で参照する)がすべて
      無効になる。**運用者が自分の権限を失う**
    - **無効化されている同名ロールは有効化する。** `isEnabled: false` の
      ロールはトークンに乗らないため、あるのに効かない状態になる

    Args:
        new_role_id: 新規作成時に使う ID。省略時は `uuid4`。テストのために開けている。
    """
    roles = [dict(role) for role in (existing or [])]

    for role in roles:
        if role.get("value") != value:
            continue
        role_id = str(role.get("id") or "")
        if not role_id:
            # ID の無いロールは Graph が返さないが、返ってきたら作り直す
            # しかない(ID が無ければ割り当てもできないため、失うものが無い)。
            role_id = new_role_id or str(uuid.uuid4())
            role["id"] = role_id
            changed = True
        else:
            changed = False
        # 説明や許可される主体の型が違えば揃える。ID は変えない。
        desired: dict[str, Any] = {
            "allowedMemberTypes": list(allowed_member_types),
            "displayName": display_name,
            "description": description,
            "isEnabled": True,
        }
        for key, wanted in desired.items():
            if role.get(key) != wanted:
                role[key] = wanted
                changed = True
        return AppRoleMerge(app_roles=roles, role_id=role_id, changed=changed)

    role_id = new_role_id or str(uuid.uuid4())
    roles.append(
        {
            "id": role_id,
            "allowedMemberTypes": list(allowed_member_types),
            "value": value,
            "displayName": display_name,
            "description": description,
            "isEnabled": True,
        }
    )
    return AppRoleMerge(app_roles=roles, role_id=role_id, changed=True)
