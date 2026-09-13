"""同梱する Ontop の題材が、こちらの検査を通る形であることを固定する
(ADR-0046、`P3-01`)。

## なぜこのテストが必要なのか

`containers/ontop/testdata/mapping.ttl` は **実物の Ontop に食わせて動く**
ことを `containers/ontop/ontop-check.test.sh` が確かめている。しかし
**Ontop が受け付ける R2RML と、こちらが登録を許す R2RML は同じではない**
— こちらは `rr:sqlQuery` も `rr:graph` も `rr:parentTriplesMap` も
受け付けない(ADR-0046 決定4・8・9)。

**同梱の題材がこちらの検査を通らない形だと、ドキュメントが嘘になる。**
「この形で書いてください」と示している例が、API では 422 になる。

`test_web_contract.py` と同じ位置づけである(生成されない境界を、
こちら側で固定する)。

## 逆向きも固定する

`mapping-bad-table.ttl` は**こちらの検査を通る**(構文は正しく、
参照する表が存在しないだけ)。**構文の検査と実在の検査は別の層である**
ことをここで示しておく — 通らなければ、あのファイルは
「エラー本文が全関係を列挙する」の実測に使えない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ontology_core.r2rml import R2rmlError, data_iri_prefix, parse_mapping

_TESTDATA = Path(__file__).resolve().parents[3] / "containers" / "ontop" / "testdata"

#: `ontology.ttl` / `mapping.ttl` が使っている名前空間の `base_iri`。
_BASE_IRI = "https://e.example/vkg#"


def _read(name: str) -> str:
    path = _TESTDATA / name
    assert path.is_file(), f"{path} が見つかりません"
    return path.read_text(encoding="utf-8")


def test_同梱のマッピングはこちらの検査を通る() -> None:
    """**通らなければドキュメントが嘘になる。**

    示している例が API では 422 になる状態を作らない。
    """
    mapping = parse_mapping(_read("mapping.ttl"))
    assert mapping.triples_maps == 2
    assert mapping.tables == ("vkg.customer", "vkg.order")


def test_同梱のマッピングの主語はデータ接頭辞の下にある() -> None:
    """**サービス層の検査(不変条件5)も通ることを確かめる。**

    `parse_mapping` は接頭辞を取るだけで、封じ込めの判定はしない。
    ここで `data_iri_prefix` と突き合わせる。
    """
    prefix = data_iri_prefix(_BASE_IRI)
    mapping = parse_mapping(_read("mapping.ttl"))
    assert mapping.subject_prefixes
    for subject in mapping.subject_prefixes:
        assert subject.startswith(prefix), subject


def test_同梱のマッピングのクラスと述語は_base_iri_の下にある() -> None:
    """外部語彙を混ぜていないこと。

    **題材は「自分の語彙だけで書ける」ことを示すためにある。**
    外部語彙が許されることは `test_vkg_api.py` が別に確かめる。
    """
    mapping = parse_mapping(_read("mapping.ttl"))
    assert mapping.classes
    assert mapping.predicates
    for iri in mapping.asserted_iris:
        assert iri.startswith(_BASE_IRI), iri


def test_同梱の_TBox_が_Turtle_として読める() -> None:
    """Ontop に渡す TBox が壊れていないこと。

    **壊れていると Ontop は起動するが推論が効かない**(遅延初期化なので
    起動は通る)。シェルの検査 2 が落ちて気づくが、ここでも見る。
    """
    from rdflib import Graph

    graph = Graph()
    graph.parse(data=_read("ontology.ttl"), format="turtle")
    assert len(graph) > 0


def test_不正な題材も構文としては通る() -> None:
    """**構文の検査と実在の検査は別の層である。**

    `mapping-bad-table.ttl` が `parse_mapping` で落ちると、
    「エラー本文が全関係を列挙する」の実測に使えない
    (Ontop に届く前にこちらで弾かれてしまう)。
    """
    mapping = parse_mapping(_read("mapping-bad-table.ttl"))
    assert mapping.tables == ("vkg.nope",)


def test_db_properties_に秘密が入っていない() -> None:
    """**プロパティファイルに資格情報を書かない**(不変条件6 と同じ向き)。

    Ontop は `ONTOP_DB_PASSWORD_FILE` を受け付けるので、書く必要が無い。
    """
    body = _read("db.properties").lower()
    for banned in ("password", "user", "url"):
        assert banned not in body, f"db.properties に {banned} が書かれている"


@pytest.mark.parametrize(
    "banned",
    ["rr:sqlQuery", "rr:graph", "rr:graphMap", "rr:parentTriplesMap"],
)
def test_受け付けない構成を題材に使っていない(banned: str) -> None:
    """**例として示すものに、受け付けない構成を混ぜない。**

    `parse_mapping` が落ちるので上のテストでも捕まるが、
    **どの構成が禁止なのかを名前で残しておく**。

    **コメント行は除く。** 題材のファイルは冒頭のコメントで
    「これらは使わない」と説明しており、素の検索だとその説明自身に
    当たる(実際に落ちた)。
    """
    for name in ("mapping.ttl", "mapping-bad-table.ttl"):
        body = "\n".join(
            line for line in _read(name).splitlines() if not line.lstrip().startswith("#")
        )
        assert banned not in body, f"{name} に {banned} がある"


def test_題材のファイルが揃っている() -> None:
    """`ontop-check.test.sh` が読むファイルが全部あること。

    **欠けるとシェルの検査が「起動しませんでした」で落ちる** — 原因が
    ファイルの欠落であることが、そのメッセージからは分からない。
    """
    for name in (
        "schema.sql",
        "ontology.ttl",
        "mapping.ttl",
        "mapping-bad-table.ttl",
        "db.properties",
    ):
        assert (_TESTDATA / name).is_file(), name


def test_parse_mapping_が落ちる例も用意されていることの確認() -> None:
    """このテストファイル自身の前提。

    **`parse_mapping` は本当に落ちるのか**を、題材とは別の入力で確かめる
    (上のテストが「常に通る実装」でも成立してしまうのを防ぐ)。
    """
    with pytest.raises(R2rmlError):
        parse_mapping("@prefix rr: <http://www.w3.org/ns/r2rml#> .\n")
