"""表示名と商標の約束を機械的に検査する(`P4-05`、ADR-0051)。

## なぜテストにするのか

[ADR-0051](../../../docs/adr/0051-display-name.md) は Microsoft の一次情報に
沿って 4 つの条件を満たしたことを確認した。**確認しただけでは、次に
README を書き換えた人が破る。**

| 条件 | ここで固定する形 |
|---|---|
| 製品名で**始まらない** | 表示名が `Azure` / `Microsoft` / `AWS` / `Amazon` で始まらない |
| `certified` / `official` / `authentic` / `licensed` を使わない | 公開する文書に現れない |
| 非提携を明記する | README と NOTICE にその文がある |
| 平文で書く | (検査しない。下記) |

**「平文で書く」は検査していない。** ロゴ画像を入れたかどうかは拡張子では
判定できず、判定できないものを「検査した」と書けない。**画像を足すときは
ADR-0051 決定1 を読むこと。**

## 「ライセンス」の語は除外する

`licensed` は禁止語だが、**このリポジトリは Apache-2.0 の文書で
`license` を大量に使う**。禁止されているのは「**Microsoft から許諾を
受けている**」と読める使い方であって、自分のライセンスの話ではない。
だから**語形を限定する**(`licensed by` / `Microsoft licensed` のような形)。

## コードスパンの中は検査しない

**バッククォートで囲まれた語は「その語を名指している」のであって、
主張していない。** README は「`certified` は使いません」と**禁止語を
挙げて説明している**ので、素朴に検索すると**検査が自分の説明文に
反応して落ちる**(実際に落ちた。このリポジトリで 3 度目の形である —
`test_受け付けない構成を題材に使っていない` と
`test_embedding_contract.py` でも踏んだ)。

だから**インラインのコードスパンとコードブロックを外してから探す**。
「Microsoft 公式」のような平文の主張は外れないので、検査の目的は保たれる。
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]

#: 確定した表示名(ADR-0051 決定1)。
DISPLAY_NAME = "Ontology Accelerator for Azure"

#: 他社の商標。**表示名の先頭に置いてはいけない。**
_MARKS = ("Azure", "Microsoft", "AWS", "Amazon")

#: 公開する文書。**内部の作業記録(`.superpowers/`)は対象にしない。**
_PUBLIC_DOCS = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "NOTICE",
)

#: コードブロック(3 連のバッククォートで囲まれた範囲)。
_FENCE = re.compile("```.*?```", re.DOTALL)

#: インラインのコードスパン。**改行は含めない** — 離れた 2 つの
#: バッククォートが本文をまるごと飲み込まないようにする。
_CODE_SPAN = re.compile("`[^`\n]+`")

#: 法的な提携を含意するため使えない語(ADR-0051 の一次情報)。
#:
#: **`licensed` は語形を限定する。** 自分のライセンスの話と区別するため、
#: 「誰かから許諾を受けている」と読める形だけを禁じる。
_FORBIDDEN = (
    re.compile(r"\bcertified\b", re.IGNORECASE),
    re.compile(r"\bauthentic\b", re.IGNORECASE),
    re.compile(r"\blicensed by\b", re.IGNORECASE),
    re.compile(r"\b(?:Microsoft|Azure|AWS|Amazon)[- ]licensed\b", re.IGNORECASE),
    re.compile(r"\b(?:Microsoft|Azure|AWS|Amazon)[- ]official\b", re.IGNORECASE),
    re.compile(r"(?:Microsoft|Azure|AWS|Amazon)\s*(?:の)?(?:公式|認定|公認)"),
)


def _without_code(body: str) -> str:
    """コードブロックとインラインのコードスパンを落とす。

    **語を名指しているだけの箇所を検査の対象から外す**ため。
    置き換えではなく削除にすると行の対応が崩れるので、空白で埋める
    (見つけた語の位置を報告に使えるようにしておく)。
    """
    body = _FENCE.sub(lambda m: " " * len(m.group(0)), body)
    return _CODE_SPAN.sub(lambda m: " " * len(m.group(0)), body)


def test_表示名が他社の商標で始まらない() -> None:
    """**Microsoft が明示的に避けるよう書いている形である。**

    `Xbox Points Calculator` ではなく `Bob's Points Calculator for Xbox` が
    良い名前だ、という例が一次情報にある。
    """
    for mark in _MARKS:
        assert not DISPLAY_NAME.startswith(mark), (
            f"表示名が '{mark}' で始まっている。Microsoft が配布しているものと誤認される"
        )


def test_表示名が_README_の見出しと一致する() -> None:
    """**名前が 2 か所で食い違っていてはいけない。**

    README の 1 行目が利用者にとっての名前である。
    """
    first = (_ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
    assert first.strip() == f"# {DISPLAY_NAME}"


def test_表示名が_devcontainer_と一致する() -> None:
    """`.devcontainer/devcontainer.json` の `name` も同じ名前にする。"""
    import json

    body = json.loads((_ROOT / ".devcontainer/devcontainer.json").read_text(encoding="utf-8"))
    assert body["name"] == DISPLAY_NAME


def test_azd_のテンプレート名がスラッグと一致する() -> None:
    """`azure.yaml` の `name` / `template` はリポジトリのスラッグに合わせる。

    **表示名ではなくスラッグである**(azd の識別子なので空白を含められない)。
    """
    body = (_ROOT / "azure.yaml").read_text(encoding="utf-8")
    assert "name: ontology-accelerator-for-azure" in body
    assert "template: ontology-accelerator-for-azure@" in body


def test_法的な提携を含意する語を公開文書で使っていない() -> None:
    """**正式な商標ライセンスが無い限り使えない語である**(ADR-0051)。

    `certified` / `official` / `authentic` / `licensed` は、Microsoft の
    一次情報が**明示的に禁じている**。
    """
    for name in _PUBLIC_DOCS:
        path = _ROOT / name
        if not path.exists():
            continue
        body = _without_code(path.read_text(encoding="utf-8"))
        for pattern in _FORBIDDEN:
            found = pattern.search(body)
            assert found is None, (
                f"{name}: 法的な提携を含意する語を使っている: {found.group(0)!r}"
                "(ADR-0051 の一次情報を参照)"
            )


def test_非提携の明記がある() -> None:
    """**Microsoft が配布しているものではないと明らかにする**(決定4)。

    `README.md` と `NOTICE` の両方に置く — README しか読まない利用者と、
    配布物だけを見る利用者の両方に届く必要がある。
    """
    for name in ("README.md", "NOTICE"):
        body = (_ROOT / name).read_text(encoding="utf-8")
        assert "提携" in body and ("承認" in body or "スポンサー" in body), (
            f"{name}: 非提携の明記が見つからない"
        )


def test_AWS_版の名前を自分の名前に使っていない() -> None:
    """**`Context Ontology Accelerator` は AWS 版の製品名である**(ADR-0008)。

    README の見出しと devcontainer の名前に現れてはいけない。
    **本文で AWS 版を指して言及するのは正当**なので、名前の位置だけを見る。
    """
    first = (_ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
    assert "Context Ontology Accelerator" not in first
