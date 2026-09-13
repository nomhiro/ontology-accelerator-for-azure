"""ライセンスの機械的な検査(ADR-0049、`P4-03`)。

**この検査のいちばん重要な性質は「判定できなかったを問題なしにしない」
ことである。** ライセンスの綴りは揺れるので、寄せる表に無いものを通す
実装は簡単に書けてしまう。そうすると**「緑なのに何も検証していない」**
状態になる。

ここで固定するのは 5 つである。

1. **コピーレフトを検出して落とす**(`denied`)
2. **知らない綴りを通さない**(`unknown`)。`allowed` に丸めない
3. **`OR` はどちらか一方でよく、`AND` は両方が要る**(SPDX の意味論)
4. **`License` 欄の全文を読まない。** 分類子へ回す
5. **検査を実行できなかったことを「問題なし」にしない**(終了コード 2)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check-licenses.py"


def _load() -> ModuleType:
    """ハイフンを含むファイル名のスクリプトを読み込む。

    **運用者が実際に実行するファイルそのもの**を読む
    (`test_setup_app_role_cli.py` と同じ形)。
    """
    name = "check_licenses_script"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # **`sys.modules` に登録してから実行する。** スクリプトは
    # `from __future__ import annotations` と `@dataclass` を使っており、
    # `dataclasses` は文字列の注釈を解くために
    # `sys.modules[cls.__module__]` を引く。登録していないと
    # **`AttributeError: 'NoneType' object has no attribute '__dict__'`**
    # になる(実測。原因が dataclass の中なので、メッセージが
    # 「モジュールを登録していない」ことを指さない)。
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


@pytest.fixture
def script() -> ModuleType:
    return _load()


class _Metadata:
    """`importlib.metadata` のメタデータを真似た代役。"""

    def __init__(self, fields: dict[str, str], classifiers: list[str] | None = None) -> None:
        self._fields = fields
        self._classifiers = classifiers or []

    def __getitem__(self, key: str) -> str | None:
        """`dist.metadata["Name"]` の形に答える(本物と同じ)。"""
        return self._fields.get(key)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._fields.get(key, default)

    def get_all(self, key: str) -> list[str] | None:
        return self._classifiers if key == "Classifier" else None


# ------------------------------------- 綴りを寄せる


def test_知らない綴りは_None_になる(script: ModuleType) -> None:
    """**「知らないから通す」を作らない。**"""
    assert script.normalize("Totally New License 9.9") is None


def test_空の表記も_None_になる(script: ModuleType) -> None:
    assert script.normalize("   ") is None


def test_大文字小文字と空白の揺れを吸収する(script: ModuleType) -> None:
    assert script.normalize("  APACHE-2.0 ") == "Apache-2.0"
    assert script.normalize("Apache  2.0") == "Apache-2.0"


# ------------------------------------- 判定(決定1・2)


@pytest.mark.parametrize(
    "raw",
    ["MIT", "Apache-2.0", "BSD-3-Clause", "MPL-2.0", "Mozilla Public License 2.0 (MPL 2.0)"],
)
def test_許諾済みのライセンスを通す(script: ModuleType, raw: str) -> None:
    verdict, normalized = script.evaluate(raw)
    assert verdict == "allowed", (raw, normalized)


def test_末尾の括弧を落として別名を外さない(script: ModuleType) -> None:
    """**実装のバグを固定する回帰テストである。**

    `strip("()")` は `Mozilla Public License 2.0 (MPL 2.0)` の**末尾の `)`
    だけ**を落とし、`... (MPL 2.0` にしていた。別名の照合が外れて
    `unknown` になり、**実際に検査が落ちた**(それで気づいた)。
    """
    assert script._unwrap("Mozilla Public License 2.0 (MPL 2.0)") == (
        "Mozilla Public License 2.0 (MPL 2.0)"
    )
    # 包んでいるときだけ外す。
    assert script._unwrap("(MIT OR CC0-1.0)") == "MIT OR CC0-1.0"


@pytest.mark.parametrize("raw", ["GPL-3.0", "AGPL-3.0", "LGPL-3.0", "SSPL-1.0"])
def test_コピーレフトを拒否する(script: ModuleType, raw: str) -> None:
    verdict, normalized = script.evaluate(raw)
    assert verdict == "denied"
    assert normalized == raw


def test_知らないライセンスは_unknown_で許諾しない(script: ModuleType) -> None:
    """**これがこのファイルの主題である。**"""
    verdict, normalized = script.evaluate("Weird Corporate License 1.0")
    assert verdict == "unknown"
    assert normalized == ""


# ------------------------------------- 複合式(決定3)


def test_OR_はどちらか一方が許諾済みならよい(script: ModuleType) -> None:
    """**選べるので、許諾できる側を選ぶ。**"""
    verdict, normalized = script.evaluate("GPL-3.0 OR MIT")
    assert verdict == "allowed"
    assert normalized == "MIT"


def test_OR_の両方が拒否なら拒否する(script: ModuleType) -> None:
    verdict, _ = script.evaluate("GPL-3.0 OR AGPL-3.0")
    assert verdict == "denied"


def test_OR_に知らない綴りが混ざり許諾側が無ければ_unknown(script: ModuleType) -> None:
    """**片方が読めないなら「選べる」と断言できない。**"""
    verdict, _ = script.evaluate("GPL-3.0 OR Weird License")
    assert verdict == "unknown"


def test_AND_は両方が許諾済みでなければ通さない(script: ModuleType) -> None:
    """**両方に従う必要があるので、片方が拒否なら拒否である。**"""
    assert script.evaluate("Apache-2.0 AND MIT")[0] == "allowed"
    assert script.evaluate("Apache-2.0 AND GPL-3.0")[0] == "denied"


def test_AND_に知らない綴りが混ざれば_unknown(script: ModuleType) -> None:
    verdict, _ = script.evaluate("MIT AND Weird License")
    assert verdict == "unknown"


def test_OR_と_AND_を混ぜた式は人に回す(script: ModuleType) -> None:
    """**優先順位の解釈が要る。** 推測で通すより落ちるほうがよい。"""
    verdict, _ = script.evaluate("MIT AND (GPL-3.0 OR Apache-2.0)")
    assert verdict == "unknown"


def test_混在式の検査が無いと誤って許諾する形を固定する(script: ModuleType) -> None:
    """**変異テストで見つかった穴である。**

    上のテストは `MIT AND (GPL-3.0 OR Apache-2.0)` を使っていて、混在の
    検査を外しても**偶然 `unknown` になっていた**(`OR` で分割した断片が
    どちらも綴りとして読めないため)。**偶然通るテストは何も固定していない。**

    この式は `OR` で分割すると先頭が `MIT` として読め、混在の検査が無いと
    **`allowed` になる**。
    """
    verdict, _ = script.evaluate("MIT OR GPL-3.0 AND AGPL-3.0")
    assert verdict == "unknown"


# ------------------------------------- メタデータの読み取り(決定4)


def test_License_Expression_を最優先で使う(script: ModuleType) -> None:
    metadata = _Metadata(
        {"License-Expression": "Apache-2.0", "License": "MIT"},
        ["License :: OSI Approved :: BSD License"],
    )
    assert script._python_raw(metadata) == "Apache-2.0"


def test_短い_License_欄は使う(script: ModuleType) -> None:
    assert script._python_raw(_Metadata({"License": "MIT"})) == "MIT"


def test_全文が入った_License_欄は読まず分類子へ回す(script: ModuleType) -> None:
    """**実測で 3 つの配布物が全文を入れていた**(`pyshacl` は 11,491 文字)。

    全文から識別子を当てるのは推測になる。
    """
    metadata = _Metadata(
        {"License": "Copyright (c) 2020\n" + "x" * 2000},
        ["License :: OSI Approved :: Apache Software License"],
    )
    assert script._python_raw(metadata) == "Apache Software License"


def test_改行を含む短い_License_欄も読まない(script: ModuleType) -> None:
    """長さだけで判定すると、2 行の短い記述を識別子として扱ってしまう。"""
    metadata = _Metadata(
        {"License": "MIT\nsee LICENSE"}, ["License :: OSI Approved :: MIT License"]
    )
    assert script._python_raw(metadata) == "MIT License"


def test_手がかりが何も無ければ空を返す(script: ModuleType) -> None:
    """**空は `unknown` として扱われる**(呼び出し側で検査が落ちる)。"""
    assert script._python_raw(_Metadata({})) == ""


# ------------------------------------- 報告と終了コード(決定5)


def test_全部許諾済みなら_0_を返す(script: ModuleType) -> None:
    findings = [script.Finding("python", "a", "MIT", "allowed", "MIT")]
    assert script.report(findings, show_all=False) == 0


def test_拒否があれば_1_を返す(script: ModuleType) -> None:
    findings = [script.Finding("python", "bad", "GPL-3.0", "denied", "GPL-3.0")]
    assert script.report(findings, show_all=False) == 1


def test_判定できないものがあれば_1_を返す(script: ModuleType) -> None:
    """**「判定できなかった」を「問題なし」にしない。**"""
    findings = [script.Finding("python", "odd", "Weird License", "unknown")]
    assert script.report(findings, show_all=False) == 1


def test_pnpm_が無いときは_2_を返し_0_にしない(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**検査の失敗と、検査の結果は違う**(ADR-0049 決定5)。

    Node 側を検査できなかったのに `0` を返すと、**「ライセンスは全部
    確認済み」として CI が緑になる**。
    """

    def _boom(root: Any) -> list[Any]:
        raise RuntimeError("pnpm が PATH にありません")

    monkeypatch.setattr(script, "node_findings", _boom)
    monkeypatch.setattr(script, "python_findings", lambda: [])
    assert script.main([]) == 2


def test_python_only_を明示すれば_Node_を飛ばす(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**飛ばすのは明示したときだけである。** 黙って飛ばさない。"""
    called: list[str] = []

    def _should_not_run(root: Any) -> list[Any]:
        called.append("node")
        return []

    monkeypatch.setattr(script, "node_findings", _should_not_run)
    monkeypatch.setattr(script, "python_findings", lambda: [])
    assert script.main(["--python-only"]) == 0
    assert called == []


def test_ライセンス情報が無い配布物を_unknown_にする(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**変異テストで見つかった穴である。**

    実測では 83 件すべてにライセンス情報があったので、**この分岐は実物では
    通らない**。`raw = "MIT"` と書き換えても既存のテストは全部通った。

    **情報が無いものを何かに「みなす」のは、この検査がやってはいけないこと
    そのものである。**
    """
    silent = SimpleNamespace(metadata=_Metadata({"Name": "mystery"}))
    monkeypatch.setattr(script, "distributions", lambda: [silent])
    findings = script.python_findings()
    assert [(f.name, f.verdict) for f in findings] == [("mystery", "unknown")]
    assert script.report(findings, show_all=False) == 1


def test_ワークスペース自身のパッケージは検査しない(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ontology-core` などはこのリポジトリの成果物(Apache-2.0)である。"""
    own = SimpleNamespace(metadata=_Metadata({"Name": "ontology-core"}))
    monkeypatch.setattr(script, "distributions", lambda: [own])
    assert script.python_findings() == []


# ------------------------------------- 実物に対して


def test_実物の依存が全部許諾済みである(script: ModuleType) -> None:
    """**この 1 件が本番の検査そのものである。**

    Node 側は `pnpm` を要求するのでここでは Python 側だけを見る
    (両方は `just check-licenses` と CI が回す)。
    """
    findings = script.python_findings()
    assert findings, "配布物が 1 件も見つからないのは異常である"
    bad = [f for f in findings if f.verdict != "allowed"]
    assert not bad, [(f.name, f.raw[:60], f.verdict) for f in bad]


def test_許諾リストと拒否リストが重なっていない(script: ModuleType) -> None:
    """**重なると判定が実装の順序に依存する。**"""
    assert not (script.ALLOWED & script.DENIED)


def test_別名の行き先が必ずどちらかのリストにある(script: ModuleType) -> None:
    """**表には載っているが判断していない識別子を作らない。**

    `evaluate` はそれを `unknown` にするので検査は落ちるが、
    **落ちる理由が「綴りを知らない」ではなく「判断していない」**に
    なってしまい、直し方が分からなくなる。
    """
    known = script.ALLOWED | script.DENIED
    stray = sorted({v for v in script._ALIASES.values() if v not in known})
    assert not stray, stray


def test_スクリプトが_print_を使っていない(script: ModuleType) -> None:
    """`scripts/lint-shell.sh` が機械的に検査するが、ここでも見る。

    **Windows の標準出力は cp932 になる**ので、日本語を `print` すると
    シェル側の `echo`(UTF-8)と混ざる。
    """
    body = _SCRIPT.read_text(encoding="utf-8")
    assert "print(" not in body


def test_代役の形が本物と揃っている(script: ModuleType) -> None:
    """このファイルの `_Metadata` が本物の代わりになっていること。

    **本物のメタデータに対して `_python_raw` が動くことを確かめる** —
    代役だけで通すと、本物の形が変わったときに気づけない。
    """
    from importlib.metadata import distribution

    real = distribution("rdflib").metadata
    assert script._python_raw(real)
    assert isinstance(script._python_raw(SimpleNamespace()), str)
