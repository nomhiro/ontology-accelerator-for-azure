"""グラフ IRI とデータセット名の組み立て。

名前空間名は Fuseki のデータセット名と Blob のパスに使うため、
境界で必ず検証する。ここが崩れると隔離が崩れる。
"""

from __future__ import annotations

import pytest

from ontology_core.graphs import (
    NamespaceNameError,
    dataset_name,
    validate_namespace_name,
    validate_version,
    version_graph_iri,
)

BASE = "urn:ontology:graph"


def test_dataset_name_is_the_namespace_itself() -> None:
    assert dataset_name("retail-core") == "retail-core"


def test_version_graph_iri_includes_namespace_and_version() -> None:
    assert version_graph_iri(BASE, "retail-core", "1.0.0") == (
        "urn:ontology:graph/retail-core/1.0.0"
    )


def test_base_trailing_slash_is_normalised() -> None:
    assert version_graph_iri(BASE + "/", "retail-core", "1.0.0") == (
        "urn:ontology:graph/retail-core/1.0.0"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "A",  # 大文字
        "-leading",  # 先頭がハイフン
        "has_underscore",
        "has space",
        "../escape",  # パストラバーサル
        "a/b",  # スラッシュ
        "x" * 64,  # 長すぎる
        "ds",  # Fuseki の予約データセット名(射影の作業用)
    ],
)
def test_invalid_namespace_names_are_rejected(bad: str) -> None:
    with pytest.raises(NamespaceNameError):
        validate_namespace_name(bad)


def test_valid_namespace_name_is_returned() -> None:
    assert validate_namespace_name("retail-core") == "retail-core"


def test_dataset_name_validates() -> None:
    with pytest.raises(NamespaceNameError):
        dataset_name("../escape")


def test_version_graph_iri_validates_namespace() -> None:
    with pytest.raises(NamespaceNameError):
        version_graph_iri(BASE, "../escape", "1.0.0")


@pytest.mark.parametrize("bad_version", ["", "../1.0.0", "1 0", "a/b"])
def test_invalid_versions_are_rejected(bad_version: str) -> None:
    with pytest.raises(NamespaceNameError):
        version_graph_iri(BASE, "retail-core", bad_version)


@pytest.mark.parametrize(
    "bad",
    [
        "retail-core\n",  # 末尾改行 ($ アンカーの落とし穴)
        "ds\n",  # 予約名 + 末尾改行でチェックをすり抜けないこと
        "retail\ncore",  # 途中の改行
        "retail-core\r\n",
        "retail-core\t",
        " retail-core",  # 先頭空白
        "retail-core ",  # 末尾空白
    ],
)
def test_whitespace_and_newline_are_rejected(bad: str) -> None:
    with pytest.raises(NamespaceNameError):
        validate_namespace_name(bad)


@pytest.mark.parametrize("bad", ["1.0.0\n", "1.0.0\r\n", "1.0\n.0"])
def test_version_rejects_newline(bad: str) -> None:
    with pytest.raises(NamespaceNameError):
        validate_version(bad)
