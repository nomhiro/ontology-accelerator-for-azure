"""ジョブの定義が渡すもの・渡さないものを固定する(ADR-0042 決定6、`P2A-19`)。

## なぜテキストで検査するのか

**Bicep の動作は課金なしに検証できない**(ADR-0041 決定10)。`az bicep build`
が通ることは「デプロイできる」を意味しない。

それでも**テキストとして確かめられる性質がある**。そのうち価値があるのは
**「渡していないこと」**である。

`scan-job` は **Core API と同じイメージ**で動く(決定5)。つまり
**渡せば使えてしまう**。Fuseki の管理パスワードを env に足す変更は 4 行で
書けてしまい、レビューでも見落としやすい。**足した瞬間にここが落ちる。**

環境変数名は Python のコードがそのまま読む契約でもあるので、
**綴りの一致も併せて固定する**(デプロイ環境でしか気づけない種類の間違い)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_INFRA = Path(__file__).resolve().parents[3] / "infra"
_JOB = _INFRA / "modules" / "scan-job.bicep"
_API = _INFRA / "modules" / "api.bicep"


def _strip_comments(body: str) -> str:
    """`//` で始まる行を落とす。

    **コメントで言及することは「渡している」ではない。** ジョブの定義は
    「なぜ渡さないか」をコメントに書いているので、そこを検査に含めると
    **説明を書いた分だけテストが落ちる**(実際に踏んだ)。
    """
    kept = [line for line in body.splitlines() if not line.lstrip().startswith("//")]
    return chr(10).join(kept)


def _job() -> str:
    return _strip_comments(_JOB.read_text(encoding="utf-8"))


# --------------------------------- 渡さないもの(ADR-0042 決定6)


@pytest.mark.parametrize(
    "forbidden",
    [
        # スキャンは Fuseki を触らない。**イメージは API と同じなので、
        # 渡せば使えてしまう。**
        "FUSEKI_ADMIN_PASSWORD",
        "FUSEKI_ADMIN_USER",
        "fusekiPasswordSecretUri",
        # スキャンは正本 TTL を触らない。
        "AZURE_STORAGE_ACCOUNT_URL",
        "ONTOLOGY_BLOB_CONTAINER",
        # ジョブは HTTP を受け付けない。
        "ENTRA_API_AUDIENCE",
        "ingress",
    ],
)
def test_ジョブに要らないものを渡さない(forbidden: str) -> None:
    """**必要のないものを渡さない**(ADR-0042 決定6)。

    落ちたら、それを渡す必要が本当にあるのか確かめること。**スキャンが
    触るのは PostgreSQL(カタログ)とソース DB だけである。**
    """
    assert forbidden not in _job(), (
        f"scan-job に '{forbidden}' が入っている。"
        "スキャンが触るのは PostgreSQL とソース DB だけである(ADR-0042 決定6)"
    )


# ------------------------------------------- 渡すもの(綴りが契約)


@pytest.mark.parametrize(
    "required",
    [
        # 接続先の allowlist(ADR-0041 決定5)。これが無いとジョブは何も
        # 掃引できない — **無くても静かに 0 件で終わるので気づけない。**
        "SCAN_ALLOWED_HOSTS",
        # 秘密の在り処(値ではない。ADR-0041 決定4)。
        "SCAN_VAULT_URL",
        # マネージド ID。`scan_runs.started_by` にも入る(ADR-0042 決定2)。
        "AZURE_CLIENT_ID",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DATABASE",
        "POSTGRES_USER",
    ],
)
def test_ジョブに必要なものを渡している(required: str) -> None:
    """**環境変数名は Python のコードが読む契約である。**

    綴りを間違えると、ジョブは例外ではなく**「ソースが 0 件」で静かに
    成功する**(`SCAN_ALLOWED_HOSTS` を落とした場合)。デプロイ環境でしか
    気づけない種類の間違いなので、ここで固定する。
    """
    assert required in _job(), f"scan-job に '{required}' が無い"


def test_ジョブは掃引のエントリポイントを起動する() -> None:
    """**API と同じイメージを別のコマンドで動かす**(ADR-0042 決定5)。

    `command` を落とすと、イメージの既定の起動コマンド(uvicorn)が走り、
    **HTTP を待ち続けてタイムアウトする**。
    """
    body = _job()
    assert "'ontology_api.scan_job'" in body
    assert "'-m'" in body


def test_再試行しない() -> None:
    """**届かなかったことは `scan_runs` に残る**(ADR-0042 決定3)。

    再試行は健全なソースを再度叩くだけである。
    """
    assert "replicaRetryLimit: 0" in _job()


def test_実行に上限がある() -> None:
    """**終わらない掃引に課金が続かないようにする**(ADR-0042 決定8)。"""
    assert "replicaTimeout: replicaTimeoutSeconds" in _job()
    assert "param replicaTimeoutSeconds int = 1800" in _job()


def test_cron_が空なら_Manual_になる() -> None:
    """**既定で顧客 DB へ手を伸ばし始めない**(ADR-0042 決定1)。

    ここが `Schedule` 固定に変わると、**allowlist を設定した瞬間に毎晩の
    接続が始まる**。
    """
    body = _job()
    assert "var useSchedule = !empty(scanJobCron)" in body
    assert "triggerType: useSchedule ? 'Schedule' : 'Manual'" in body
    assert "param scanJobCron string = ''" in body, "cron の既定が空でない"


# ------------------------- API 側の配線(`P2A-01` が残していた穴)


@pytest.mark.parametrize("required", ["SCAN_ALLOWED_HOSTS", "SCAN_VAULT_URL"])
def test_API_にもスキャンの設定が届く(required: str) -> None:
    """**`P2A-01` はこれを配線していなかった**(ADR-0042 で気づいた)。

    実装もテストも揃っていたのに、**デプロイ環境では allowlist が空のまま
    で、スキャンの登録が必ず 403 になる**状態だった。ローカルの `.env` では
    設定できていたので、ローカルのテストでは気づけない。
    """
    body = _strip_comments(_API.read_text(encoding="utf-8"))
    assert required in body, f"api.bicep に '{required}' が無い"
