"""マニフェストの契約を言語の境界をまたいで固定する(`P2B-C1`)。

**書き手は Python(`_build_manifest`)、読み手はシェル
(`containers/fuseki/lib/validate.sh`)である。** 2 つの言語に分かれているので、
片方だけ直しても型検査も lint も何も言わない。

**実際に食い違った。** ADR-0019(`P2B-02`)がマニフェストの `schema` を 2 に
上げたとき、`validate_manifest_json` は `.schema == 1` のままだった。
結果として**ローダは全名前空間を「不正な形式」としてスキップしていた** —
レプリカを作り直すとストアが空になり、`reconcile` を手で回すまで
クエリが 0 行を返す状態だった(実測で確認)。

不変条件1 は「トリプルストアは再構築可能な射影である」と言う。
**再構築できていなかった。** そして誰も気づかなかった理由は単純で、
シェル側のテストが `schema 1` のマニフェストしか食わせていなかったからである。

**だからここで契約そのものを検査する。** 書き手が出す `schema` が読み手の
許可リストに入っていることを、シェルのソースを読んで確かめる。
「テストを両方書く」ではなく「**両者が同じ値を見ていること**」を固定する。
"""

from __future__ import annotations

import re
from pathlib import Path

from ontology_api.services.projection import _build_manifest

#: `containers/fuseki/lib/validate.sh`。
_VALIDATE_SH = Path(__file__).resolve().parents[3] / "containers/fuseki/lib/validate.sh"

#: ローダが受け付ける schema の一覧を宣言している行。
_SCHEMAS_PATTERN = re.compile(r'^MANIFEST_SCHEMAS="([^"]*)"', re.MULTILINE)


def _loader_schemas() -> set[int]:
    """ローダが受け付ける schema を `validate.sh` から読む。

    **値をこのテストに書き写さない。** 書き写すと、シェル側を直し忘れても
    テストだけが通る(今回の不具合とまったく同じ形になる)。
    """
    source = _VALIDATE_SH.read_text(encoding="utf-8")
    match = _SCHEMAS_PATTERN.search(source)
    assert match is not None, (
        f"{_VALIDATE_SH} に MANIFEST_SCHEMAS の宣言が見つかりません。"
        "ローダが受け付ける schema を宣言する場所を変えたなら、このテストも直すこと"
    )
    return {int(part) for part in match.group(1).split()}


def test_書き手が出す_schema_をローダが受け付ける() -> None:
    """**これが `P2B-C1` そのものである。**

    `_build_manifest` が出す `schema` が `validate.sh` の許可リストに入って
    いなければ、ローダはその名前空間を丸ごとスキップする。
    """
    manifest = _build_manifest("retail-core", [], retain_superseded=0)
    schema = manifest["schema"]
    assert isinstance(schema, int), "schema は数値で出すこと(ローダが型も検査する)"
    assert schema in _loader_schemas(), (
        f"書き手が出す schema {schema} がローダの許可リスト {_loader_schemas()} に無い。"
        "ローダは全名前空間をスキップする(= 再構築でストアが空になる)"
    )


def test_ローダは古い_schema_も受け付け続ける() -> None:
    """**入れ替えの窓のために後方互換を保つ。**

    デプロイの入れ替え中は「古いマニフェスト + 新しいローダ」が並ぶ。
    schema 1 を落とすと、`reconcile` を回すまでその名前空間が消える。
    """
    assert 1 in _loader_schemas()


def test_マニフェストの必須の欄が揃っている() -> None:
    """ローダが `jq` で読む欄を固定する。

    **欄名を変えるとローダが黙って壊れる。** `namespace` と `versions` は
    `validate_manifest_json` が型まで見ており、`current` と各版の
    `projection` は `projection_targets` が読む。
    """
    manifest = _build_manifest("retail-core", [], retain_superseded=0)
    assert set(manifest) >= {
        "schema",
        "namespace",
        "current",
        "retain_superseded",
        "versions",
        "generated_at",
    }
    assert isinstance(manifest["namespace"], str)
    assert isinstance(manifest["versions"], list)
