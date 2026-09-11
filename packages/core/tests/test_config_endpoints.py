"""`FUSEKI_PORT` がエンドポイントの既定に届くこと(`P2A-13`)。

**ポート衝突の回避手段が CLI に届いていなかった。** CLAUDE.md は
「ポート 3030 が別プロジェクトと衝突する場合は `FUSEKI_PORT=3131` を指定する。
テストも同じ変数を読む」と案内していたが、`scripts/check-questions.py` は
`SPARQL_QUERY_ENDPOINT`(完全な URL)を読むため効かなかった。

**症状が分かりにくい。** 3030 を別プロジェクトの Fuseki が握っていると
そちらへクエリが飛んで **HTTP 405** になり、`0/11 が満たされていません` と
表示される(実測)。**質問が落ちたように見えるが、実際は違うサーバに
当たっている。**
"""

from __future__ import annotations

from typing import Any

import pytest

from ontology_core.config import AuthMode, Settings

_ENDPOINT_FIELDS = (
    "sparql_query_endpoint",
    "sparql_update_endpoint",
    "sparql_gsp_endpoint",
    "fuseki_admin_endpoint",
)

_ENV_VARS = (
    "FUSEKI_PORT",
    "SPARQL_QUERY_ENDPOINT",
    "SPARQL_UPDATE_ENDPOINT",
    "SPARQL_GSP_ENDPOINT",
    "FUSEKI_ADMIN_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """**プロセスの環境変数でテストの結果が変わらないようにする。**

    このリポジトリでは 3030 が別プロジェクトと衝突するときに
    `FUSEKI_PORT=3131` を設定して pytest を回す。それが既定値のテストを
    壊すので消す(実際に踏んだ)。
    """
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides: Any) -> Settings:
    """`.env` とプロセスの環境を無視した `Settings` を作る。

    **`.env` を読ませないのが要点。** 開発者のローカル設定でテストの結果が
    変わってはいけない。`_env_file` は `BaseSettings` の初期化引数で、
    型注釈には現れないため `mypy` を黙らせる必要がある。
    """
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        AUTH_MODE=AuthMode.DISABLED,
        **overrides,
    )


def test_既定は_3030() -> None:
    """**`_DEFAULT_FUSEKI_PORT` と既定値の文字列が揺れないように固定する。**

    置換は `:3030/` を目印にしているので、既定値だけを変えると
    `FUSEKI_PORT` が黙って効かなくなる。
    """
    settings = _settings()
    for field in _ENDPOINT_FIELDS:
        assert ":3030/" in getattr(settings, field), field


@pytest.mark.parametrize("field", _ENDPOINT_FIELDS)
def test_FUSEKI_PORT_が_4_つすべてに届く(field: str) -> None:
    """**4 つ全部を見る。** 1 つだけ直しても、admin API や GSP が旧ポートを
    向いたままだと「一部だけ動く」という分かりにくい状態になる。
    """
    settings = _settings(FUSEKI_PORT=3131)
    value = getattr(settings, field)
    assert ":3131/" in value, value
    assert ":3030/" not in value, value


def test_明示したエンドポイントは書き換えない() -> None:
    """**デプロイ環境では Bicep が注入した値が常に勝つ。**

    ここが書き換わると、`FUSEKI_PORT` が残っている環境で**内部 ingress の
    FQDN が壊れる**。
    """
    settings = _settings(
        FUSEKI_PORT=3131,
        SPARQL_QUERY_ENDPOINT="http://fuseki.internal:3030/{dataset}/sparql",
    )
    assert settings.sparql_query_endpoint == "http://fuseki.internal:3030/{dataset}/sparql"
    # 明示していない方は書き換わる。
    assert ":3131/" in settings.sparql_update_endpoint


def test_dataset_のプレースホルダは保たれる() -> None:
    """置換で `{dataset}` を壊すと、名前空間の隔離が静かに壊れる。"""
    settings = _settings(FUSEKI_PORT=3131)
    assert settings.sparql_query_endpoint == "http://localhost:3131/{dataset}/sparql"
    assert settings.sparql_gsp_endpoint == "http://localhost:3131/{dataset}/data"
    assert settings.fuseki_admin_endpoint == "http://localhost:3131/$/"


def test_3030_を明示的に渡しても壊れない() -> None:
    """既定値と同じポートを渡す経路(冪等)。"""
    settings = _settings(FUSEKI_PORT=3030)
    assert settings.sparql_query_endpoint == "http://localhost:3030/{dataset}/sparql"
