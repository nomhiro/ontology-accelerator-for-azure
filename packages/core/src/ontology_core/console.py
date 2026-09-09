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
"""

from __future__ import annotations

import sys
from typing import TextIO

__all__ = ["say", "warn"]


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
