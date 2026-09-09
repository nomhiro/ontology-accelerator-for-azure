"""CLI 出力が UTF-8 に固定されることのテスト(`P2A-09`)。

**この検査は Windows でこそ意味がある。** Linux では `sys.stdout.encoding` が
既定で UTF-8 なので、`print` でも偶然通ってしまう。そのため
「`encoding` が cp932 のストリーム」を作って渡し、**それでも UTF-8 で
書かれること**を確認する。
"""

from __future__ import annotations

import io

import pytest

from ontology_core.console import say, warn

_JA = "名前空間の作成には platform-admin が必要です"


def _cp932_stream() -> io.TextIOWrapper:
    """Windows の既定と同じ cp932 のテキストストリームを作る。"""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp932", newline="")


def _written(stream: io.TextIOWrapper) -> bytes:
    stream.flush()
    buffer = stream.buffer
    assert isinstance(buffer, io.BytesIO)
    return buffer.getvalue()


def test_cp932_のストリームでも_UTF8_で書く() -> None:
    """**これが本題。** `print` に任せると cp932 のバイト列が出る。"""
    stream = _cp932_stream()
    say(_JA, stream=stream)
    assert _written(stream) == _JA.encode("utf-8") + b"\n"


def test_cp932_で表現できない文字でも落ちない() -> None:
    """絵文字や一部の記号は cp932 に無い。`print` だと
    `UnicodeEncodeError` で**スクリプトごと落ちる**。"""
    text = "完了 \U0001f600 — ダッシュ"
    stream = _cp932_stream()
    say(text, stream=stream)
    assert _written(stream) == text.encode("utf-8") + b"\n"


def test_改行を_1_つだけ付ける() -> None:
    stream = _cp932_stream()
    say("a", stream=stream)
    say("b", stream=stream)
    assert _written(stream) == b"a\nb\n"


def test_既にバッファに溜まった出力と順序が入れ替わらない() -> None:
    """`say` はテキスト層を飛ばして `buffer` に書くため、先に `flush`
    しなければ**前に書いた行より先に出てしまう**。"""
    stream = _cp932_stream()
    stream.write("さきに書いた\n")
    say("あとに書いた", stream=stream)
    assert _written(stream) == "さきに書いた\n".encode("cp932") + "あとに書いた\n".encode()


def test_say_の既定の宛先は標準出力(capsysbinary: pytest.CaptureFixture[bytes]) -> None:
    """バイト列で受ける(`capsysbinary`)のは、**文字列で受けると pytest 側の
    デコードで UTF-8 化されてしまい、このモジュールが何を書いたのかが
    分からなくなる**ため。"""
    say(_JA)
    out, err = capsysbinary.readouterr()
    assert out == _JA.encode("utf-8") + b"\n"
    assert err == b""


def test_warn_は標準エラーに書く(capsysbinary: pytest.CaptureFixture[bytes]) -> None:
    warn(_JA)
    out, err = capsysbinary.readouterr()
    assert out == b""
    assert err == _JA.encode("utf-8") + b"\n"
