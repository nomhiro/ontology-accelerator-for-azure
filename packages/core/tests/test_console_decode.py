"""外部コマンドの出力のデコード(`P2A-09` の実機確認で踏んだ)。

## 何を踏んだのか

`scripts/setup-app-role.py` を**実テナントに対して**走らせたら、

```
AttributeError: 'NoneType' object has no attribute 'strip'
```

で落ちた。原因は `subprocess.run(..., text=True, encoding="utf-8")` で、
**日本語 Windows の `az` が cp932 のバイトを返した**ことである。

**`subprocess` は例外を投げない。** 出力を読むのは別スレッドで、そこで
`UnicodeDecodeError` が起きると `proc.stdout` が `None` になって戻る。
呼び出し側には**原因を指さない `AttributeError`** が届く。

ローカルのテストでは踏めない。`az` を呼ばないからである。
"""

from __future__ import annotations

import locale

from ontology_core.console import decode_output


def test_UTF_8_はそのまま読む() -> None:
    assert decode_output("名前空間".encode()) == "名前空間"


def test_ASCII_はそのまま読む() -> None:
    assert decode_output(b'{"appId": "abc"}') == '{"appId": "abc"}'


def test_None_を受け取れる() -> None:
    """**読み取りスレッドが死ぬと `None` になる。**

    そこで落ちると、原因を指さない `AttributeError` になる(実測)。
    """
    assert decode_output(None) == ""


def test_空のバイト列は空文字列() -> None:
    assert decode_output(b"") == ""


def test_cp932_のバイト列でも落ちない() -> None:
    """**これが実機で踏んだ形である。**

    `0x83` で始まるバイトは UTF-8 として不正で、**以前はここで
    `UnicodeDecodeError` になっていた**。
    """
    raw = "エラー: 見つかりません".encode("cp932")
    # UTF-8 として読めないことを確かめてから、
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:  # pragma: no cover - cp932 と UTF-8 が一致する環境は想定しない
        raise AssertionError("この入力は UTF-8 として読めてしまう(テストの前提が崩れている)")

    # 落ちずに何かを返す。
    decoded = decode_output(raw)
    assert decoded
    if locale.getpreferredencoding(False).lower() in {"cp932", "shift_jis", "ms932"}:
        # 日本語 Windows では**正しく読める**。
        assert decoded == "エラー: 見つかりません"
    else:
        # それ以外の環境では読めない文字が置換される。**落ちないことが要件。**
        assert "エラー" in decoded or "�" in decoded


def test_UTF_8_の側で置換しない() -> None:
    """**「読めたが文字化けした」を作らない。**

    UTF-8 に `errors="replace"` を付けると、cp932 の出力が
    **JSON として解析できるのに中身が壊れている**状態になりうる。
    だから別のコーデックで読み直す。

    ここでは「UTF-8 として読める入力に置換文字が入らない」ことで、
    UTF-8 の経路が厳密であることを確かめる。
    """
    assert "�" not in decode_output("日本語のテキスト".encode())
