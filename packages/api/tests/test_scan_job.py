"""登録済みの全ソースを掃引する(ADR-0042、`P2A-19`)。

**Bicep は課金なしに検証できないが、掃引の中身はできる。** ACA Job で
確かめるしかないのは「ジョブが起動するか」「マネージド ID が Key Vault に
届くか」だけである。**何を掃引し、失敗したときどうなるかは、実物の
PostgreSQL に対してここで確かめる。**

ここで固定するのは 5 つである。

1. **全名前空間のソースを掃引する**(決定2)
2. **1 つのソースの失敗が残りを止めない**(決定4)。想定外の例外でも止まらない
3. **失敗の理由を捨てない。** `total = succeeded + failed` が必ず成り立つ
4. **allowlist を迂回しない**(ADR-0041 決定5)
5. **主体はジョブである。** 人間の ID を借りない
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_api.repositories.scan import ScanRepository
from ontology_api.scan_job import FALLBACK_ACTOR, _actor_of, sweep_sources
from ontology_core.config import AuthMode, Settings
from ontology_core.db import ScanSourceRow

_HOST = os.environ.get("POSTGRES_HOST", "localhost")
_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
_DATABASE = os.environ.get("POSTGRES_DATABASE", "ontology")
_USER = os.environ.get("POSTGRES_USER", "ontology")
_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "localdev")

_ACTOR = "job-identity-client-id"


def _settings(*, allowed: str | None = None) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        AUTH_MODE=AuthMode.DISABLED,
        SCAN_ALLOWED_HOSTS=_HOST if allowed is None else allowed,
    )


async def _local_password(source: ScanSourceRow, settings: Settings) -> str | None:
    return _PASSWORD


async def _add_source(session: AsyncSession, *, namespace: str, name: str, **kwargs: Any) -> int:
    """名前空間とソースを 1 組作り、**ソースの id を返す**。

    行そのものを返さないのは、`sweep_sources` が失敗時に `rollback` する
    ためである。巻き戻すと ORM のオブジェクトは期限切れになり、後から
    `source.id` を読むと**同期の文脈で IO が起きて `MissingGreenlet` に
    なる**(実測で踏んだ)。**素の int で持てばこの罠が消える。**
    """
    if await NamespaceRepository(session).get(namespace) is None:
        await NamespaceRepository(session).create(
            name=namespace,
            display_name=namespace,
            description="",
            base_iri=f"https://e.example/{namespace}#",
            created_by="admin-oid",
        )
    fields: dict[str, Any] = {
        "driver": "postgresql",
        "host": _HOST,
        "port": _PORT,
        "database": _DATABASE,
        "username": _USER,
        "auth_mode": "entra",
        "vault_secret_name": None,
        "created_by": "owner-oid",
    }
    await ScanRepository(session).create_source(
        namespace=namespace, name=name, **{**fields, **kwargs}
    )
    await session.commit()
    row = await ScanRepository(session).get_source(namespace=namespace, name=name)
    assert row is not None
    return row.id


# ------------------------------------------- 全名前空間を掃引する(決定2)


@pytest.mark.integration
async def test_全名前空間のソースを掃引する(session: AsyncSession) -> None:
    """**名前空間で絞らない**(ADR-0042 決定2)。

    `reconcile` と同じ**システムの保守処理**の位置にある。API のハンドラから
    この経路を呼んではいけない(そちらは名前空間で絞る)。
    """
    await _add_source(session, namespace="job-ns-a", name="self")
    await _add_source(session, namespace="job-ns-b", name="self")

    report = await sweep_sources(
        session=session,
        settings=_settings(),
        actor=_ACTOR,
        secret_resolver=_local_password,
    )

    assert report.total == 2
    assert report.succeeded == 2
    assert report.failures == []

    for namespace in ("job-ns-a", "job-ns-b"):
        source = await ScanRepository(session).get_source(namespace=namespace, name="self")
        assert source is not None
        runs = await ScanRepository(session).list_runs(source_id=source.id)
        assert [r.status for r in runs] == ["succeeded"]
        assert runs[0].table_count is not None and runs[0].table_count > 0


@pytest.mark.integration
async def test_ソースが無ければ何もしない(session: AsyncSession) -> None:
    """**0 件は失敗ではない。** ただし呼び出し元が区別できるように数を返す。"""
    report = await sweep_sources(
        session=session, settings=_settings(), actor=_ACTOR, secret_resolver=_local_password
    )
    assert report.total == 0
    assert report.succeeded == 0
    assert report.failures == []


# --------------------- 1 つの失敗が残りを止めない(決定4)


@pytest.mark.integration
async def test_接続に失敗しても残りを掃引する(session: AsyncSession) -> None:
    """**ADR-0042 決定4 の本体。**

    `list_all_source_rows` は名前空間名・ソース名の順に返すので、
    **先に失敗するほうを置いて「その後も続いた」ことを示す。**
    """
    await _add_source(session, namespace="job-ns-a", name="broken")
    await _add_source(session, namespace="job-ns-b", name="healthy")

    async def _selective(source: ScanSourceRow, settings: Settings) -> str | None:
        return "wrong-value" if source.name == "broken" else _PASSWORD

    report = await sweep_sources(
        session=session, settings=_settings(), actor=_ACTOR, secret_resolver=_selective
    )

    assert report.total == 2
    assert report.succeeded == 1
    assert [(f.namespace, f.name) for f in report.failures] == [("job-ns-a", "broken")]
    assert report.failures[0].reason, "理由を捨てている"

    repo = ScanRepository(session)
    healthy = await repo.get_source(namespace="job-ns-b", name="healthy")
    assert healthy is not None
    assert [r.status for r in await repo.list_runs(source_id=healthy.id)] == ["succeeded"]

    broken = await repo.get_source(namespace="job-ns-a", name="broken")
    assert broken is not None
    broken_runs = await repo.list_runs(source_id=broken.id)
    assert [r.status for r in broken_runs] == ["failed"]
    assert broken_runs[0].table_count is None, "失敗した run に件数を書いている"


@pytest.mark.integration
async def test_想定外の例外でも残りを掃引する(session: AsyncSession) -> None:
    """**セッションを巻き戻してから次へ進む**(ADR-0042 決定4)。

    `SourceConnectionError` 以外の例外(マネージド ID が使えないときの
    `CredentialUnavailableError` など)は `ScanService.scan` の中で
    `fail_run` を通らない。**巻き戻さないと半端なトランザクションが残り、
    残り全部が失敗になって症状が原因を指さなくなる。**

    このとき最初のソースには `running` の run が残る — それが
    ADR-0041 決定7 の意図である(スキャンを試みた事実が消えない)。
    """
    await _add_source(session, namespace="job-ns-a", name="explodes")
    await _add_source(session, namespace="job-ns-b", name="healthy")

    async def _selective(source: ScanSourceRow, settings: Settings) -> str | None:
        if source.name == "explodes":
            raise RuntimeError("マネージド ID が使えない")
        return _PASSWORD

    report = await sweep_sources(
        session=session, settings=_settings(), actor=_ACTOR, secret_resolver=_selective
    )

    assert report.total == 2
    assert report.succeeded == 1
    assert [f.name for f in report.failures] == ["explodes"]
    assert "マネージド ID" in report.failures[0].reason

    repo = ScanRepository(session)
    healthy = await repo.get_source(namespace="job-ns-b", name="healthy")
    assert healthy is not None
    assert [r.status for r in await repo.list_runs(source_id=healthy.id)] == ["succeeded"], (
        "1 つ目の想定外の例外で掃引が止まっている"
    )

    exploded = await repo.get_source(namespace="job-ns-a", name="explodes")
    assert exploded is not None
    # **スキャンを試みた事実が残る**(ADR-0041 決定7)。
    assert [r.status for r in await repo.list_runs(source_id=exploded.id)] == ["running"]


@pytest.mark.integration
async def test_成功と失敗の和が総数になる(session: AsyncSession) -> None:
    """**「数えられなかった」を作らない。**

    どのソースも必ず成功か失敗のどちらかに入る。
    """
    for index in range(3):
        await _add_source(session, namespace="job-ns-a", name=f"s{index}")

    async def _half(source: ScanSourceRow, settings: Settings) -> str | None:
        return _PASSWORD if source.name == "s1" else "wrong-value"

    report = await sweep_sources(
        session=session, settings=_settings(), actor=_ACTOR, secret_resolver=_half
    )
    assert report.total == 3
    assert report.succeeded + report.failed == report.total
    assert report.succeeded == 1


# ------------------------------- allowlist を迂回しない(ADR-0041 決定5)


@pytest.mark.integration
async def test_allowlist_を迂回しない(session: AsyncSession) -> None:
    """**ジョブは API と同じ `ScanService` を通る**(ADR-0042 決定5)。

    allowlist の検査は `scan()` の最初にあるので、**ジョブから呼んでも
    効く**。効かなければ「定期実行なら任意のホストへ接続できる」抜け道に
    なる。

    **run の行すら作られない** — 接続してから断るのでは、到達性を調べる
    手段を与えたことになる(ADR-0041 決定5)。
    """
    source_id = await _add_source(session, namespace="job-ns-a", name="self")

    report = await sweep_sources(
        session=session,
        settings=_settings(allowed="db.example.internal"),
        actor=_ACTOR,
        secret_resolver=_local_password,
    )

    assert report.total == 1
    assert report.succeeded == 0
    assert [f.name for f in report.failures] == ["self"]
    assert "許可されていません" in report.failures[0].reason

    runs = await ScanRepository(session).list_runs(source_id=source_id)
    assert runs == [], "allowlist で断る前に run を作っている"


@pytest.mark.integration
async def test_allowlist_が空なら何も掃引できない(session: AsyncSession) -> None:
    """**既定は空である**(ADR-0041 決定5、不変条件11)。"""
    await _add_source(session, namespace="job-ns-a", name="self")
    report = await sweep_sources(
        session=session,
        settings=_settings(allowed=""),
        actor=_ACTOR,
        secret_resolver=_local_password,
    )
    assert report.succeeded == 0
    assert report.failed == 1


# --------------------------------------------- 主体はジョブである


@pytest.mark.integration
async def test_run_の主体にジョブの_ID_が入る(session: AsyncSession) -> None:
    """**人間が起動していないことが記録から読める**(ADR-0042 決定2)。"""
    source_id = await _add_source(session, namespace="job-ns-a", name="self")
    await sweep_sources(
        session=session, settings=_settings(), actor=_ACTOR, secret_resolver=_local_password
    )
    runs = await ScanRepository(session).list_runs(source_id=source_id)
    assert runs[0].started_by == _ACTOR


def test_主体はマネージド_ID_の_client_id_である() -> None:
    settings = Settings(_env_file=None, AUTH_MODE=AuthMode.DISABLED, AZURE_CLIENT_ID="abc-123")  # type: ignore[call-arg]
    assert _actor_of(settings) == "abc-123"


def test_client_id_が無ければ人間の_ID_を借りない() -> None:
    """**分からないことを分かったように書かない**(ADR-0035 と同じ態度)。"""
    settings = Settings(_env_file=None, AUTH_MODE=AuthMode.DISABLED)  # type: ignore[call-arg]
    assert _actor_of(settings) == FALLBACK_ACTOR
