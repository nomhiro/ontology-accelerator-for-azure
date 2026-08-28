"""Core API の最小テスト。

スキャフォールドが組み立てとして成立していること(アプリが import でき、ルートが
登録され、ヘルスチェックが応答すること)を確認する。
"""

from __future__ import annotations

import os

os.environ.setdefault("AUTH_MODE", "disabled")

from fastapi.testclient import TestClient

from ontology_api.main import app

client = TestClient(app)


def test_healthz_returns_ok() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["auth_mode"] == "disabled"


def test_openapi_schema_is_generated() -> None:
    # Web 用の TypeScript 型はこのスキーマから生成するため、壊れていないことを確認する。
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/namespaces" in paths
    assert "/namespaces/{namespace}/sparql" in paths


def test_namespace_roundtrip_on_stub() -> None:
    created = client.post(
        "/namespaces",
        json={
            "name": "retail-core",
            "display_name": "小売ドメイン",
            "base_iri": "https://example.com/ontology/retail#",
        },
    )
    assert created.status_code == 201

    listed = client.get("/namespaces")
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["retail-core"]

    assert client.delete("/namespaces/retail-core").status_code == 204
    assert client.get("/namespaces/retail-core").status_code == 404


def test_update_query_is_rejected() -> None:
    response = client.post(
        "/namespaces/retail-core/sparql",
        json={"query": "DELETE WHERE { ?s ?p ?o }"},
    )

    # ストアへ到達する前にガードで弾かれること。
    assert response.status_code == 400
