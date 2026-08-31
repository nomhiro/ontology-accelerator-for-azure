"""グラフ IRI とデータセット名の組み立て。

名前空間名は次の 3 か所で使われる。
  - Fuseki のデータセット名(名前空間の物理的な隔離単位)
  - Blob のパス (`approved/<namespace>/<version>.ttl`)
  - 名前付きグラフ IRI

いずれもパストラバーサルや予期しない文字が入ると隔離が崩れるため、
**このモジュールを通してのみ**組み立てる。文字列連結を各所に散らさない。
"""

from __future__ import annotations

import re

__all__ = [
    "RESERVED_DATASET_NAMES",
    "NamespaceNameError",
    "dataset_name",
    "validate_namespace_name",
    "validate_version",
    "version_graph_iri",
]

# `$` は re.MULTILINE を指定しなくても「文字列末尾の直前の改行」にマッチしてしまい、
# 末尾に改行を付けるだけで検証をすり抜けられる(例: "ds\n" が予約名チェックも通過する)。
# `^`/`$` によるアンカーではなくこの落とし穴が構造的に存在しないため、
# パターン自体にはアンカーを付けず、呼び出し側で `fullmatch` を使う。
_NAMESPACE_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,62}")
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}")

# `ds` は射影の作業用に config.ttl が定義する固定データセット。
# 名前空間名として使わせない。
RESERVED_DATASET_NAMES = frozenset({"ds"})


class NamespaceNameError(ValueError):
    """名前空間名またはバージョン文字列が使えないことを表す。"""


def validate_namespace_name(namespace: str) -> str:
    """名前空間名を検証して返す。

    Raises:
        NamespaceNameError: 形式が不正、または予約名のとき。
    """
    if not _NAMESPACE_PATTERN.fullmatch(namespace):
        raise NamespaceNameError(
            f"名前空間名 '{namespace}' は使えません。"
            "小文字英数字とハイフンのみ、2〜63 文字、先頭は英数字です"
        )
    if namespace in RESERVED_DATASET_NAMES:
        raise NamespaceNameError(f"名前空間名 '{namespace}' は予約されています")
    return namespace


def validate_version(version: str) -> str:
    """バージョン文字列を検証して返す。

    Raises:
        NamespaceNameError: IRI やパスに使えない文字を含むとき。
    """
    if not _VERSION_PATTERN.fullmatch(version):
        raise NamespaceNameError(f"バージョン '{version}' は使えません")
    return version


def dataset_name(namespace: str) -> str:
    """名前空間に対応する Fuseki のデータセット名を返す。"""
    return validate_namespace_name(namespace)


def version_graph_iri(base: str, namespace: str, version: str) -> str:
    """バージョンごとの名前付きグラフ IRI を返す。"""
    validate_namespace_name(namespace)
    validate_version(version)
    return f"{base.rstrip('/')}/{namespace}/{version}"
