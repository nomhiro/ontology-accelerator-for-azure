"""名前空間名をパスパラメータとして受け取るエンドポイントの入口検証。

名前空間名は Fuseki のデータセット名・グラフ IRI に使うセキュリティ境界である
(`packages/api/tests/test_isolation.py` が実物の Fuseki で検証している境界)。
`FusekiStore._resolve` は `{dataset}` を `str.format` で置き換えるだけで、
呼び出し側が渡す値をそれ以上検証しない。`namespace` をパスパラメータで受け取る
エンドポイントが `ontology_core.graphs.validate_namespace_name` を呼ばずに
ストアへ渡すと、`../ds` のような値が URL の `..` セグメントとして正規化され、
予約データセット `ds` や別の名前空間へ到達できてしまう(実測: `httpx` は
`http://host/{dataset}/sparql`.format(dataset="../ds") ==
"http://host/../ds/sparql" を送信時に "http://host/ds/sparql" へ正規化する)。

このテストは「不正な namespace は 400 で弾かれる」ことだけを確認する。
StoreDep / SessionDep / BlobDep はいずれも遅延接続(コンストラクトの時点では
ネットワークに出ない)なので、検証が本体処理より先に走ることを確認するのに
実物の Fuseki / PostgreSQL は不要。
"""

from __future__ import annotations

import os

os.environ.setdefault("AUTH_MODE", "disabled")
# `publish_version` は `BlobDep` を無条件に要求する。`BlobServiceClient.__init__`
# は URL の構文をローカルで検証するため(ネットワークには出ない)。既定値の
# 空文字列だと検証以前に ValueError で落ちる。また `AUTH_MODE=disabled` でも
# `DefaultAzureCredential`(トークン資格情報)を渡すため scheme は https で
# なければならない。この検証テストにとってはどの値でも良いので、構文的に
# 妥当な値を与えて依存関係の解決自体は素通りさせる(実際に接続はしない)。
os.environ.setdefault("AZURE_STORAGE_ACCOUNT_URL", "https://devstoreaccount1.blob.core.windows.net")

import pytest
from fastapi.testclient import TestClient

from ontology_api.main import app
from ontology_core.config import get_settings

# `get_settings()` は `@lru_cache(maxsize=1)` でプロセス内に 1 つだけ保持される。
# 他のテストモジュールが先に import されて `get_settings()` を呼んでいると、
# 上の `os.environ.setdefault` が効く前の値(既定の空文字列)が固定されてしまう
# ため、明示的にキャッシュを破棄してこのモジュールの環境変数を確実に反映させる。
get_settings.cache_clear()

client = TestClient(app)

# `validate_namespace_name` が拒否するはずの値。
#   - "ds": 予約名(ontology_core.graphs.RESERVED_DATASET_NAMES)
#   - "A": 大文字
#   - "a": 1 文字(2 文字未満)
#   - "a_b": アンダースコアは許可文字種の外
_INVALID_NAMESPACES = ["ds", "A", "a", "a_b"]


@pytest.mark.parametrize("namespace", _INVALID_NAMESPACES)
def test_sparql_query_rejects_invalid_namespace(namespace: str) -> None:
    response = client.post(
        f"/namespaces/{namespace}/sparql",
        json={"query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"},
    )

    assert response.status_code == 400


@pytest.mark.parametrize("namespace", _INVALID_NAMESPACES)
def test_publish_version_rejects_invalid_namespace(namespace: str) -> None:
    response = client.post(
        f"/namespaces/{namespace}/versions",
        json={"turtle": "@prefix ex: <https://e.example/#> .\nex:X a ex:Class .\n"},
    )

    assert response.status_code == 400


@pytest.mark.parametrize("namespace", _INVALID_NAMESPACES)
def test_list_versions_rejects_invalid_namespace(namespace: str) -> None:
    response = client.get(f"/namespaces/{namespace}/versions")

    assert response.status_code == 400


@pytest.mark.parametrize("namespace", _INVALID_NAMESPACES)
def test_delete_namespace_rejects_invalid_namespace(namespace: str) -> None:
    """final-fix-brief.md 修正5 / O-2: `delete_namespace` は他の3経路
    (`run_query` / `publish_version` / `list_versions`)と同じく入口で
    `validate_namespace_name` を呼ぶこと。修正前は検証が無く、DB に該当行が
    無いだけで(たまたま)404 になっていた非対称があった。
    """
    response = client.delete(f"/namespaces/{namespace}")

    assert response.status_code == 400


def test_path_traversal_style_namespace_does_not_reach_the_store() -> None:
    """`../ds` のようなパストラバーサル的な値を試す。

    FastAPI の既定のパスパラメータは `/` を含む値を 1 セグメントとして
    ルーティングしないため、この値が実際にどう扱われるかを固定して記録する
    (ルーティングの時点で 404 になる場合も、200/400 系の応答になる場合も、
    少なくとも 500 や実データへの到達(200 で本物の結果)にはならないことを見る)。
    """
    response = client.post(
        "/namespaces/../ds/sparql",
        json={"query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"},
    )

    # ルーティングで 404 になる("/" を含むため 1 セグメントにマッチしない)か、
    # ルーティングを通過して検証に引っかかり 400 になるかのいずれかであり、
    # 500(未処理の例外)や実データへの到達にはならないことを確認する。
    assert response.status_code in (400, 404)
