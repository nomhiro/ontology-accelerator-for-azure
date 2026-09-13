"""依存関係のライセンスを機械的に検査する(ADR-0049、`P4-03`)。

使い方:
    uv run python scripts/check-licenses.py
    uv run python scripts/check-licenses.py --list   # 見つけたものを全部並べる

終了コード: 0 = 全部許諾済み / 1 = 拒否または判定できないものがある /
2 = 検査自体を実行できなかった。

## 何を守るための検査か

`docs/third-party-licenses.md` はこう書いていた。

> 今 CI が機械的に守っているのは「HermiT / JFact が入っていないこと」だけで、
> **新しいコピーレフト依存が別の名前で入ってきた場合は捕まえられません。**

**そこを埋める。** Python(uv)と Node(pnpm)の依存について、
**すべての配布物のライセンスが許諾リストに載っていること**を確かめる。

## 「判定できなかった」を「問題なし」にしない

**これがこの検査のいちばん重要な性質である。**

ライセンスの綴りは揺れる(実測で `Apache-2.0` / `Apache 2.0` /
`Apache License 2.0` / `Apache Software License` の 4 通りがあった)。
さらに **3 つの配布物は `License` 欄に全文を書いていた**(`pyshacl` は
11,491 文字)。

綴りを寄せる表(`_ALIASES`)に無いものは **`unknown` として検査を落とす**。
「知らない綴りだから通す」を 1 度でも許すと、**この検査は「緑なのに何も
検証していない」状態になる**。

許諾リストを広げるのは**人の判断**である。広げるときは
`docs/third-party-licenses.md` に理由を書くこと。

## この検査が見ていないもの

- **Java 側**(`containers/reasoner` の shade jar)。`reasoner-check.test.sh`
  が「HermiT / JFact が入っていないこと」を確かめている。**依存全体の
  ライセンスは見ていない**(`license-maven-plugin` を回す手順は
  `docs/third-party-licenses.md` にある)
- **コンテナの基盤イメージ**(Ontop / Fuseki / postgres の同梱物)。
  Ontop は `/opt/ontop/copyright/` に 27 件のライセンスを持つ(2026-09-13 に
  目視で確認。GPL 単独は無い)
- **JDBC ドライバ**。`containers/ontop/Dockerfile` が SHA-256 で固定し、
  ライセンス全文を `containers/ontop/jdbc-licenses/` に置いてある

**見ていないものを列挙するのがこの節の目的である。** 「CI が通ったから
ライセンスは全部確認済み」と読まれないため。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import distributions
from pathlib import Path

from ontology_core.console import decode_output, say, warn

#: Apache-2.0 の配布物に同梱してよいライセンス。
#:
#: **広げるのは人の判断である。** 足すときは
#: `docs/third-party-licenses.md` に理由を書くこと。
ALLOWED: frozenset[str] = frozenset(
    {
        "0BSD",
        "Apache-2.0",
        "BSD",  # 2-clause / 3-clause を分類子から判別できない場合(下の注記)
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC0-1.0",
        "CC-BY-4.0",
        "ISC",
        "MIT",
        "MIT-0",
        "MPL-2.0",
        "PSF-2.0",
        "Python-2.0",
        "Unlicense",
        "W3C-20150513",
        "Zlib",
    }
)

#: 明示的に拒否するライセンス。**`unknown` とは別のメッセージを出す。**
#:
#: 理由は [ADR-0005](../docs/adr/0005-reasoner-boundary.md) と
#: `docs/third-party-licenses.md` にある。LGPL が並ぶのは、
#: **ライブラリとして取り込むとコンテナイメージの配布に義務が絡む**ためで、
#: 別プロセスで動かす場合(HermiT)は同梱しない形で扱っている。
DENIED: frozenset[str] = frozenset(
    {
        "AGPL-3.0",
        "GPL-2.0",
        "GPL-3.0",
        "LGPL-2.1",
        "LGPL-3.0",
        "SSPL-1.0",
    }
)

#: 綴りの揺れを上の識別子へ寄せる表(**実測した綴りだけを入れる**)。
#:
#: **推測で増やさない。** 「たぶん Apache だろう」で通すと、この検査は
#: 意味を失う。実際に出てきた綴りを 1 つずつ確かめて足す。
_ALIASES: dict[str, str] = {
    "0bsd": "0BSD",
    "apache 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "bsd": "BSD",
    "bsd license": "BSD",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "cc-by-4.0": "CC-BY-4.0",
    "cc0-1.0": "CC0-1.0",
    "isc": "ISC",
    "isc license (iscl)": "ISC",
    "mit": "MIT",
    "mit license": "MIT",
    "mit-0": "MIT-0",
    "mit no attribution license (mit-0)": "MIT-0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
    "psf": "PSF-2.0",
    "psf-2.0": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "python-2.0": "Python-2.0",
    "the unlicense (unlicense)": "Unlicense",
    "unlicense": "Unlicense",
    "w3c-20150513": "W3C-20150513",
    "zlib": "Zlib",
    # ---- 拒否する側の綴り ----
    "agpl-3.0": "AGPL-3.0",
    "gnu general public license v2 (gplv2)": "GPL-2.0",
    "gnu general public license v3 (gplv3)": "GPL-3.0",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0",
    "gpl-2.0": "GPL-2.0",
    "gpl-3.0": "GPL-3.0",
    "lgpl-2.1": "LGPL-2.1",
    "lgpl-3.0": "LGPL-3.0",
    "sspl-1.0": "SSPL-1.0",
}

#: `License` 欄がこれより長いときは**全文が書かれている**と見なして読まない。
#:
#: 実測で 3 つの配布物(`html5rdf` 1,083 / `isodate` 1,608 /
#: `pyshacl` 11,491 文字)が全文を入れていた。**全文から識別子を当てるのは
#: 推測になる**ので、分類子(`Classifier: License ::`)へ回す。
MAX_LICENSE_FIELD = 60

#: `pnpm licenses list` を実行する場所。**`package.json` があるディレクトリで
#: ないと `ERR_PNPM_NO_IMPORTER_MANIFEST_FOUND` になる**(実測。リポジトリの
#: ルートには `package.json` が無い)。
#:
#: **出力の形は pnpm 9 と 10 で同じ**ことを実測した(dict: ライセンス →
#: パッケージの配列)。ローカルは 10、CI は 9 である。
NODE_PACKAGE_DIR = "apps/web"


@dataclass(frozen=True)
class Finding:
    """1 つの配布物についての判定。"""

    ecosystem: str
    name: str
    raw: str
    #: `allowed` / `denied` / `unknown`。
    verdict: str
    #: 寄せられた識別子。`unknown` のときは空。
    normalized: str = ""


def normalize(raw: str) -> str | None:
    """ライセンスの綴りを識別子へ寄せる。分からなければ `None`。

    **`None` を「許諾済み」に丸めないこと。** 呼び出し側が `unknown` として
    扱い、検査を落とす。
    """
    key = " ".join(raw.strip().lower().split())
    if not key:
        return None
    return _ALIASES.get(key)


def _unwrap(expression: str) -> str:
    """式を包んでいる丸括弧だけを外す。

    **`strip("()")` を使ってはいけない。** あれは両端の括弧を無条件に
    落とすので、`Mozilla Public License 2.0 (MPL 2.0)` の**末尾の `)` だけ**を
    削って `... (MPL 2.0` にしてしまう(実測。別名の照合が外れて
    `unknown` になった)。**包んでいるときだけ外す。**
    """
    text = expression.strip()
    while text.startswith("(") and text.endswith(")"):
        inner = text[1:-1]
        # 中で括弧が閉じていなければ「包んでいる」とは言えない。
        if inner.count("(") != inner.count(")"):
            break
        text = inner.strip()
    return text


def evaluate(expression: str) -> tuple[str, str]:
    """ライセンス式を評価して `(判定, 寄せた表記)` を返す。

    SPDX の複合式を扱う。

    | 式 | 判定 |
    |---|---|
    | `A OR B` | **どちらか一方が許諾済みなら許諾済み**(選べる) |
    | `A AND B` | **両方が許諾済みでなければ通さない**(両方に従う必要がある) |

    **`OR` と `AND` を混ぜた式は扱わない。** 優先順位の解釈が要るので、
    `unknown` として人に回す — **推測で通すより落ちるほうがよい。**
    """
    text = _unwrap(expression)
    has_or = re.search(r"\bOR\b", text, re.IGNORECASE) is not None
    has_and = re.search(r"\bAND\b", text, re.IGNORECASE) is not None

    if has_or and has_and:
        return "unknown", ""

    if has_or:
        parts = [p.strip() for p in re.split(r"\bOR\b", text, flags=re.IGNORECASE)]
        resolved = [normalize(p) for p in parts]
        if any(r in ALLOWED for r in resolved if r):
            chosen = next(r for r in resolved if r and r in ALLOWED)
            return "allowed", chosen
        if all(r is not None for r in resolved):
            return "denied", " OR ".join(r for r in resolved if r)
        return "unknown", ""

    if has_and:
        parts = [p.strip() for p in re.split(r"\bAND\b", text, flags=re.IGNORECASE)]
        resolved = [normalize(p) for p in parts]
        if any(r is None for r in resolved):
            return "unknown", ""
        joined = " AND ".join(r for r in resolved if r)
        if all(r in ALLOWED for r in resolved if r):
            return "allowed", joined
        return "denied", joined

    single = normalize(text)
    if single is None:
        return "unknown", ""
    if single in ALLOWED:
        return "allowed", single
    if single in DENIED:
        return "denied", single
    # 表には載っているが許諾も拒否もしていない = 判断していない。
    return "unknown", single


def _python_raw(metadata: object) -> str:
    """配布物のメタデータからライセンスの生の表記を取り出す。

    **優先順は `License-Expression` → `License`(短いときだけ) → 分類子**である。

    - `License-Expression` は PEP 639 の SPDX 式。最も信頼できる
    - `License` は自由記述で、**全文が入っていることがある**(実測)ので
      長いものは読まない
    - 分類子は `License :: OSI Approved :: MIT License` の形。
      **`BSD License` は 2-clause と 3-clause を区別しない** — どちらも
      Apache-2.0 と両立するので `BSD` として許諾するが、
      **この検査は両者を区別しない**ことを記録しておく
    """
    get = getattr(metadata, "get", None)
    get_all = getattr(metadata, "get_all", None)
    if get is None or get_all is None:  # pragma: no cover - 型の保険
        return ""
    expression = (get("License-Expression") or "").strip()
    if expression:
        return expression
    field = (get("License") or "").strip()
    if field and len(field) <= MAX_LICENSE_FIELD and "\n" not in field:
        return field
    classifiers = [
        c.split("::")[-1].strip()
        for c in (get_all("Classifier") or [])
        if isinstance(c, str) and c.startswith("License ::")
    ]
    return classifiers[0] if classifiers else ""


def python_findings() -> list[Finding]:
    """インストール済みの Python 配布物を判定する。

    **ワークスペース自身のパッケージは除く。** `ontology-core` などは
    このリポジトリの成果物で、ライセンスは `LICENSE`(Apache-2.0)である。
    """
    own = {"ontology-api", "ontology-core", "ontology-mcp", "ontology-accelerator"}
    findings: list[Finding] = []
    for dist in distributions():
        name = (dist.metadata["Name"] or "").strip()
        if not name or name.lower() in own:
            continue
        raw = _python_raw(dist.metadata)
        if not raw:
            findings.append(Finding("python", name, "(表記なし)", "unknown"))
            continue
        verdict, normalized = evaluate(raw)
        findings.append(Finding("python", name, raw, verdict, normalized))
    return sorted(findings, key=lambda f: f.name.lower())


def node_findings(root: Path) -> list[Finding]:
    """pnpm の依存を判定する。

    Raises:
        RuntimeError: `pnpm` を実行できなかったとき。**「Node 側は 0 件
            だった」として通さない** — 実行できなかったことを呼び出し側が
            終了コード 2 として扱う。
    """
    workdir = root / NODE_PACKAGE_DIR
    if not (workdir / "package.json").is_file():
        raise RuntimeError(f"{NODE_PACKAGE_DIR}/package.json が見つかりません")
    # **絶対パスを解決してから起動する。** 部分的な実行ファイル名を
    # `subprocess` に渡すと、`PATH` の解決が呼び出し環境に依存する
    # (静的解析ツールが S607 として指摘する)。
    #
    # **Windows では `shutil.which` が `PATHEXT` を見る**ので `pnpm.cmd` が
    # 見つかる。見つからないときは**「Node 側は 0 件だった」にしない** —
    # 実行できなかったことを例外で伝える。
    executable = shutil.which("pnpm")
    if executable is None:
        raise RuntimeError("pnpm が PATH にありません")
    try:
        # **バイト列で受けて自分で解釈する。** Windows の pnpm は警告を
        # cp932 で返すことがあり、`text=True` だと読む側が死ぬ
        # (CLAUDE.md の `az` の罠と同じ形)。
        proc = subprocess.run(  # noqa: S603 - 実行ファイルは which で解決済み
            [executable, "licenses", "list", "--json"],
            cwd=workdir,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"pnpm を実行できませんでした: {exc}") from exc
    body = decode_output(proc.stdout)
    if not body.strip():
        raise RuntimeError(
            "pnpm licenses list が何も返しませんでした: " + decode_output(proc.stderr)[:300]
        )
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"pnpm の出力を JSON として読めませんでした: {exc}") from exc
    if not isinstance(payload, dict) or "error" in payload:
        raise RuntimeError(f"pnpm がエラーを返しました: {body[:300]}")

    findings: list[Finding] = []
    for raw, packages in payload.items():
        verdict, normalized = evaluate(raw)
        names = packages if isinstance(packages, list) else []
        for package in names:
            name = package.get("name", "(不明)") if isinstance(package, dict) else str(package)
            findings.append(Finding("node", name, raw, verdict, normalized))
    return sorted(findings, key=lambda f: f.name.lower())


def report(findings: list[Finding], *, show_all: bool) -> int:
    """判定を表示して終了コードを返す。"""
    denied = [f for f in findings if f.verdict == "denied"]
    unknown = [f for f in findings if f.verdict == "unknown"]
    allowed = [f for f in findings if f.verdict == "allowed"]

    if show_all:
        for finding in findings:
            label = finding.normalized or finding.raw
            say(f"  {finding.verdict:8s} {finding.ecosystem:7s} {finding.name} ({label})")

    say("")
    say(f"検査した配布物: {len(findings)} 件(許諾済み {len(allowed)} 件)")

    if denied:
        warn("")
        warn(f"拒否するライセンスが {len(denied)} 件あります(コピーレフト等)。")
        for finding in denied:
            warn(f"  NG {finding.ecosystem}: {finding.name} = {finding.normalized}")
        warn("  docs/third-party-licenses.md を読み、依存を外すか扱いを決めてください。")

    if unknown:
        warn("")
        warn(f"ライセンスを判定できないものが {len(unknown)} 件あります。")
        warn("  **「判定できなかった」は「問題なし」ではありません。**")
        for finding in unknown:
            warn(f"  ?  {finding.ecosystem}: {finding.name} = {finding.raw[:60]!r}")
        warn("  綴りを確かめて scripts/check-licenses.py の _ALIASES に足すか、")
        warn("  許諾リスト(ALLOWED)を広げてください。**理由を")
        warn("  docs/third-party-licenses.md に書くこと。**")

    if denied or unknown:
        return 1
    say("すべて許諾済みのライセンスでした。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="依存関係のライセンスを検査する")
    parser.add_argument("--list", action="store_true", help="判定した配布物を全部並べる")
    parser.add_argument(
        "--python-only", action="store_true", help="Node 側を検査しない(pnpm が無い環境用)"
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    findings = python_findings()
    say(f"Python の配布物: {len(findings)} 件")

    if not args.python_only:
        try:
            node = node_findings(root)
        except RuntimeError as exc:
            # **「Node 側は問題なし」として通さない。** 実行できなかったことを
            # 別の終了コードで伝える(検査の失敗と、検査の結果は違う)。
            warn(f"Node 側を検査できませんでした: {exc}")
            warn("  pnpm を入れるか、--python-only を明示してください。")
            return 2
        say(f"Node の配布物: {len(node)} 件")
        findings += node

    return report(findings, show_all=args.list)


if __name__ == "__main__":
    sys.exit(main())
