"""`setup-app-role.py` の `--dry-run` は何も書き込まない(実機確認で踏んだ)。

## 何を踏んだのか

`--dry-run` を付けて実行したのに、**`az ad sp create` が実行されて
サービスプリンシパルが作られた**。`_ensure_service_principal` だけが
`dry_run` を受け取っていなかった。

**書き込む dry-run は dry-run ではない。** 「何をするか見てから決める」
ために付けるフラグで書き込みが起きると、**確認する手段そのものが副作用を
持つ**ことになる。

## どう固定するか

`az` の呼び出しを全部記録し、**変更を伴うものが 1 つも無い**ことを確かめる。
個々の分岐に `if dry_run` があるかを見るのではなく、**外から見た振る舞い**で
固定する — 新しい呼び出しを足しても落ちる。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "setup-app-role.py"

#: 変更を伴う `az` の呼び出し。**1 つでも出たら `--dry-run` が壊れている。**
MUTATING = (
    ("ad", "sp", "create"),
    ("rest", "--method", "PATCH"),
    ("rest", "--method", "POST"),
    ("rest", "--method", "PUT"),
    ("rest", "--method", "DELETE"),
)


def _load() -> ModuleType:
    """ハイフンを含むファイル名のスクリプトを読み込む。

    **`import` できない名前**(`setup-app-role`)なので、`importlib` で
    直接読む。スクリプトをテストできる形に置き換えるより、**運用者が実際に
    実行するファイルそのもの**を読むほうが確実である。
    """
    spec = importlib.util.spec_from_file_location("setup_app_role_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script() -> ModuleType:
    return _load()


def _install_recorder(script: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """`az` の呼び出しを記録し、読み取りには嘘の応答を返す。"""
    calls: list[tuple[str, ...]] = []

    def fake_az(*args: str) -> str:
        calls.append(args)
        # `az ad app show`(アプリ登録の取得)
        if args[:3] == ("ad", "app", "show"):
            return json.dumps({"id": "app-object-id", "appRoles": [], "optionalClaims": {}})
        # `az ad sp show`(サービスプリンシパルの取得) — **無いことにする**
        if args[:3] == ("ad", "sp", "show"):
            raise script.AzError("存在しません")
        # `az ad signed-in-user show`
        if args[:3] == ("ad", "signed-in-user", "show"):
            return "caller-object-id"
        return ""

    monkeypatch.setattr(script, "_az", fake_az)
    return calls


def test_dry_run_は何も書き込まない(script: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """**これが実機で踏んだ形である。**

    サービスプリンシパルが無い状態で `--dry-run` を実行すると、以前は
    `az ad sp create` が走っていた。
    """
    calls = _install_recorder(script, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["setup-app-role.py", "--app-id", "test-app-id", "--dry-run"])

    assert script.main() == 0

    for call in calls:
        for mutating in MUTATING:
            assert call[: len(mutating)] != mutating, (
                f"--dry-run が変更を伴う呼び出しをしている: az {' '.join(call)}"
            )


def test_dry_run_でも読み取りは行う(script: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """**何も呼ばないのでは「何をするか」が分からない。**

    読み取り(`show`)は行う。行わないと、既に定義済みかどうかも言えない。
    """
    calls = _install_recorder(script, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["setup-app-role.py", "--app-id", "test-app-id", "--dry-run"])
    script.main()
    assert any(call[:3] == ("ad", "app", "show") for call in calls), "アプリ登録を読んでいない"


def test_dry_run_でないときは書き込む(script: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """**逆向きも確かめる。** 何も書かない実装でもテストが通ってしまわないように。"""
    calls: list[tuple[str, ...]] = []

    def fake_az(*args: str) -> str:
        calls.append(args)
        if args[:3] == ("ad", "app", "show"):
            return json.dumps({"id": "app-object-id", "appRoles": [], "optionalClaims": {}})
        if args[:3] == ("ad", "sp", "show"):
            # 1 回目(存在確認)は無い、`create` の後は返す。
            if any(call[:3] == ("ad", "sp", "create") for call in calls):
                return "sp-object-id"
            raise script.AzError("存在しません")
        if args[:3] == ("ad", "signed-in-user", "show"):
            return "caller-object-id"
        if args[:1] == ("rest",) and "appRoleAssignedTo" in " ".join(args):
            return json.dumps({"value": []})
        return ""

    monkeypatch.setattr(script, "_az", fake_az)
    monkeypatch.setattr(sys, "argv", ["setup-app-role.py", "--app-id", "test-app-id"])

    assert script.main() == 0
    assert any(call[:3] == ("ad", "sp", "create") for call in calls), (
        "サービスプリンシパルを作っていない"
    )
    assert any(call[:3] == ("rest", "--method", "PATCH") for call in calls), (
        "ロールを定義していない"
    )


def test_Graph_の本文をコマンドラインに載せない(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Windows の `az.cmd` は JSON の引数を壊す**(実機で踏んだ)。

    `{"appRoles": [...]}` をそのまま渡すと、cmd が再解析して
    `"..." の使い方が誤っています。` という**az でも Graph でもないエラー**に
    なる。`@<ファイル>` で渡せばシェルの引用規則を通らない。

    **ファイルは必ず消す。**
    """
    seen: dict[str, Any] = {}

    def fake_az(*args: str) -> str:
        for index, arg in enumerate(args):
            if arg == "--body":
                seen["body_arg"] = args[index + 1]
                # 渡した時点でファイルが存在していることを確かめる。
                seen["existed"] = Path(args[index + 1].removeprefix("@")).is_file()
                seen["content"] = Path(args[index + 1].removeprefix("@")).read_text(
                    encoding="utf-8"
                )
        return ""

    monkeypatch.setattr(script, "_az", fake_az)
    script._graph_send("PATCH", "https://graph.example/x", {"appRoles": [{"id": "a"}]})

    body_arg = seen["body_arg"]
    assert body_arg.startswith("@"), "JSON をコマンドラインに直接載せている"
    assert seen["existed"], "ファイルを作る前に渡している"
    assert json.loads(seen["content"]) == {"appRoles": [{"id": "a"}]}
    # **後始末**。
    assert not Path(body_arg.removeprefix("@")).exists(), "一時ファイルが残っている"


def test_日本語の本文が壊れない(script: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """**一時ファイルは cp932 ではなく UTF-8 で書く。**

    ロールの説明は日本語である。既定のエンコーディングに任せると、
    Windows では cp932 で書かれて Graph に壊れた文字列が届く。
    """
    captured: dict[str, str] = {}

    def fake_az(*args: str) -> str:
        for index, arg in enumerate(args):
            if arg == "--body":
                captured["content"] = Path(args[index + 1].removeprefix("@")).read_text(
                    encoding="utf-8"
                )
        return ""

    monkeypatch.setattr(script, "_az", fake_az)
    script._graph_send(
        "PATCH", "https://graph.example/x", {"description": script._ROLE_DESCRIPTION}
    )
    assert json.loads(captured["content"])["description"] == script._ROLE_DESCRIPTION
