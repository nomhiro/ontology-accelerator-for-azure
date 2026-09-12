"""CLI の出力を UTF-8 に固定する(`P2A-09`)。

## なぜ `print` を使わないのか

**Windows の Python は `sys.stdout.encoding` が既定で `cp932` になる。**
`print` に日本語を渡すと cp932 のバイト列が出る。一方、このリポジトリの
シェルスクリプト(`preprovision.sh` などの `echo`)はソースの UTF-8 を
そのまま出す。**同じログに 2 つのエンコーディングが混ざる。**

害は 3 つある。

- azd のフックのログや CI の出力が読めない
- ログを機械的に検査するテストが通らない(`preprovision.test.sh` で実際に踏んだ)
- cp932 で表現できない文字があると `UnicodeEncodeError` で**スクリプトが落ちる**

このリポジトリには既に「ファイル出力はシェルに任せず、生成側の言語で
`encoding='utf-8'` を明示する」という規律がある(PowerShell の `>` が
UTF-16LE で書き出す問題)。これはその規律を標準出力にも広げたものである。

## 読む側も同じ問題を持つ(`decode_output`)

**`subprocess` で外部コマンドを呼ぶと、返ってくるバイト列もコードページに
なる。** `az` を `text=True, encoding="utf-8"` で呼ぶと、日本語 Windows では
**`UnicodeDecodeError` で読み取りスレッドが死に、`proc.stdout` が `None` に
なる**(実測。`AttributeError: 'NoneType' object has no attribute 'strip'`
という**原因を指さない例外**になる)。`decode_output` はこれを受け止める。
"""

from __future__ import annotations

import locale
import sys
from typing import TextIO

__all__ = ["decode_output", "say", "warn"]


def say(text: str, *, stream: TextIO | None = None) -> None:
    """UTF-8 で 1 行書く。既定は標準出力。

    `TextIO` の層を飛ばして `buffer` に直接書くため、`stream.encoding` が
    何であっても UTF-8 になる。**先に `flush` する**のは、`print` などで
    既にバッファに溜まっている出力と順序が入れ替わらないようにするため。
    """
    target = stream if stream is not None else sys.stdout
    target.flush()
    target.buffer.write(text.encode("utf-8") + b"\n")
    target.buffer.flush()


def warn(text: str) -> None:
    """UTF-8 で標準エラーに 1 行書く。"""
    say(text, stream=sys.stderr)


def decode_output(raw: bytes | None) -> str:
    """外部コマンドの出力を文字列にする。

    **UTF-8 を先に試し、失敗したらロケールのコードページで読む。**

    `az` を `subprocess.run(..., text=True, encoding="utf-8")` で呼ぶと、
    日本語 Windows では**警告が cp932 で返ってきて `UnicodeDecodeError` に
    なる**。そのとき死ぬのは読み取りスレッドなので、`subprocess` は例外を
    投げずに **`proc.stdout` を `None` にして戻る** — 呼び出し側には
    `AttributeError: 'NoneType' object has no attribute 'strip'` という
    **原因を指さない例外**が届く(実測。`scripts/setup-app-role.py` を
    実テナントに対して走らせて踏んだ)。

    **`errors="replace"` を UTF-8 の側で使わない。** 使うと、cp932 の出力が
    「読めたが文字化けした文字列」になり、**JSON として解析できるのに
    中身が壊れている**状態を作りうる。**読めなかったことを隠さない**ため、
    別のコーデックで読み直す。

    Args:
        raw: `capture_output=True`(バイト列)で受けた出力。読み取りに失敗して
            `None` になっていても受け取れる。

    Returns:
        デコードした文字列。`raw` が `None` なら空文字列。
    """
    if raw is None:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # ロケールのコードページで読み直す。**ここでは `replace` を許す** —
        # 診断のための文字列であり、読めない 1 文字のために全体を失うより
        # 読める形で渡すほうがよい。
        return raw.decode(locale.getpreferredencoding(False), errors="replace")
