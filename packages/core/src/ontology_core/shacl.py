"""SHACL 検証(ADR-0005 決定1、P2A-05)。

**pyshacl で完結させる。** これにより Java 依存は Phase 4 まで発生しない
(ADR-0005)。OWL 推論とは実装・実行形態・導入時期のすべてで分離する。

## 2 段階の検証を行う

| 段階 | 何を検証するか |
|---|---|
| 1. shapes 自体 | SHACL-SHACL に対して shapes グラフを検証する。
  **どこにも当たらない shape を検出する**(下記) |
| 2. データ | shapes に対してデータグラフを検証する。ADR-0005 の本題である
  「このオントロジーやデータは、定めた形を満たしているか」 |

**段階 1 の価値は実測で確かめた。** 壊し方ごとにどちらが検出するかは次のとおり。

| 壊し方 | 段階 1 | 段階 2 |
|---|---|---|
| `sh:minCount "いち"`(型違い) | 違反として報告 | pyshacl が読み込み時に例外 |
| `sh:property` に `sh:path` が無い | 違反として報告 | pyshacl が実行時に例外 |
| **`sh:targetClass "C"`(リテラル)** | **違反として報告** | **適合(何も検出しない)** |
| `sh:minCoun 1`(制約名のタイプミス) | 適合 | 適合 |

**段階 1 だけが捕まえるのは 3 行目である。** 対象クラスがリテラルを指している
shape はどのノードにも当たらないので、データ検証は「違反ゼロ」を返す。制約を
書いたつもりが何も検査していない状態で、このリポジトリが繰り返し戦っている
「静かに間違う」型の問題そのものである。

1・2 行目は段階 2 でも気づけるが、**段階 2 では例外になる**(「違反が見つかった」
ではなく「検証できなかった」になる)。段階 1 なら違反箇所を添えて報告できる。
そのため**段階 1 が落ちたら段階 2 は実行しない**(実行しても例外になるだけで、
段階 1 の報告を失う)。

**4 行目は両方とも検出できない。** SHACL の処理系は知らない述語を単に無視する
仕様なので、制約名のタイプミスは「制約が 1 つも書かれていない shape」と
区別がつかない。これは既知の限界として受け入れる(`P2A-05` に記録)。

## 何を検証しないか

**外部の実データへの適用は Phase 3 である。** ADR-0009 の未解決事項に
「定義と実データの乖離検出(SHACL 形状を定期的に実データへ当てる案)は
Ontop 経由の連邦クエリが前提になるため Phase 3 以降」とある。ここで検証する
データグラフは**投入された TTL 自身**であり、スキーマだけの TTL なら
対象ノードが無いので適合する(それが正しい挙動である)。

**オントロジーの構造規約(「すべてのクラスにラベルがあるか」等)は想定質問の
`expect: empty` で書く**(`P2B-07`、ADR-0009 決定6)。同じ役割の仕組みを 2 つ
持たない。SHACL はデータの形、想定質問は語彙と規約を担う。
"""

from __future__ import annotations

import importlib.resources as resources
from dataclasses import dataclass
from functools import cache

from pyshacl import validate as _pyshacl_validate

__all__ = ["ShaclReport", "ShaclValidationError", "validate_turtle_with_shacl"]


class ShaclValidationError(RuntimeError):
    """SHACL 検証そのものを実行できなかったことを表す。

    「検証したら違反が見つかった」ではなく「検証ができなかった」である。
    呼び出し元はこれを違反として扱ってはならない(違反ゼロと混同しないため)。
    """


@dataclass(frozen=True)
class ShaclReport:
    """検証結果。

    `conforms` は 2 段階の**両方**を満たしたときだけ True。
    どちらの段階で落ちたかは `shapes_conform` / `data_conform` で分かる。
    """

    conforms: bool
    shapes_conform: bool
    data_conform: bool
    # 人が読める報告。pyshacl の出力をそのまま入れる(違反箇所と sh:message を含む)。
    shapes_report: str = ""
    data_report: str = ""

    def messages(self) -> list[str]:
        """違反の要約を返す。API の応答に載せる用。"""
        if not self.shapes_conform:
            # shapes が壊れていればデータ検証は実行していない。
            # 「データも違反している」とは言えない(確かめていない)。
            return ["SHACL の shapes 自体が不正です(この制約はどのデータにも当たりません)"]
        if not self.data_conform:
            return ["データが SHACL の制約に違反しています"]
        return []


@cache
def _shacl_shacl() -> str:
    """SHACL-SHACL(shapes を検証するための shapes)を返す。

    **pyshacl が同梱しているアセットを使う。** 自前で持つと SHACL の仕様更新に
    追従できなくなる。パッケージデータとして読むので、pyshacl が置き場所を
    変えたら `ShaclValidationError` で**明示的に失敗する**(黙って段階 1 を
    飛ばして「違反ゼロ」を返すと、壊れた shape を通してしまう)。
    """
    try:
        return (resources.files("pyshacl") / "assets" / "shacl-shacl.ttl").read_text(
            encoding="utf-8"
        )
    except (OSError, ModuleNotFoundError, AttributeError) as exc:
        raise ShaclValidationError(
            "pyshacl の shacl-shacl.ttl を読めません。pyshacl の同梱アセットの"
            "置き場所が変わった可能性があります"
        ) from exc


def _run(data: str, shapes: str, *, advanced: bool) -> tuple[bool, str]:
    try:
        conforms, _, text = _pyshacl_validate(
            data,
            data_graph_format="turtle",
            shacl_graph=shapes,
            shacl_graph_format="turtle",
            advanced=advanced,
        )
    except Exception as exc:  # pyshacl は多様な例外を投げるためここで一本化する
        raise ShaclValidationError(f"SHACL 検証を実行できませんでした: {exc}") from exc
    return bool(conforms), str(text)


def validate_turtle_with_shacl(turtle: str) -> ShaclReport:
    """TTL を、それ自身が含む SHACL shapes で検証する。

    **同期実行である**(ADR-0005: 承認フローの中で同期的に実行し、レビュー画面で
    違反を即座に示す)。OWL 推論のように実行時間が予測しづらい処理ではない。

    Raises:
        ShaclValidationError: 検証を実行できなかったとき。**違反として扱っては
            ならない。** 「制約を満たしている」と「検証できなかった」を
            混同すると、壊れた定義を通してしまう。
    """
    shapes_conform, shapes_report = _run(turtle, _shacl_shacl(), advanced=False)
    if not shapes_conform:
        # **段階 2 を実行しない。** shapes が壊れていると pyshacl は読み込み時
        # または実行時に例外を投げ、`ShaclValidationError`(= 検証できなかった)
        # になってしまう。段階 1 の「どこがどう悪いか」という報告を失うので、
        # ここで止めて報告する。
        return ShaclReport(
            conforms=False,
            shapes_conform=False,
            data_conform=False,
            shapes_report=shapes_report,
        )

    # 段階 2 は `advanced=True`(SPARQL ベースの制約や sh:sparql を有効にする)。
    # 段階 1 は SHACL-SHACL 自身の定義に従うので既定のままにする。
    data_conform, data_report = _run(turtle, turtle, advanced=True)
    return ShaclReport(
        conforms=data_conform,
        shapes_conform=True,
        data_conform=data_conform,
        data_report=data_report if not data_conform else "",
    )
