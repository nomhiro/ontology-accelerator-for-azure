"""登録済みの全ソースを 1 度だけスキャンする(ADR-0042、`P2A-19`)。

`python -m ontology_api.scan_job` で走る。ACA Job のエントリポイントである。

## 何を掃引するのか

**登録済みの全ソースである。名前空間で絞らない**(ADR-0042 決定2)。
`POST /admin/reconcile` と同じ**システムの保守処理**の位置にある。

**トリガに引数が無いのは意図である。** 実行ごとに対象を渡せる形にすると、
ジョブを起動できる主体が対象を選べるようになり、「誰がどの DB を
スキャンできるか」が名前空間の権限の外に出る(ADR-0041 決定8 が避けたこと)。

## 終了コードが何を意味するか

| 何が起きたか | 終了コード |
|---|---|
| 掃引が走った(一部のソースが `failed` でも) | **0** |
| 掃引そのものが走れなかった | **1** |

**「0」は「全ソースに届いた」ではない**(ADR-0042 決定3)。1 つの顧客 DB が
落ちていることをジョブの失敗にすると、ACA の再試行が**健全なソースを
何度も叩く**。届かなかったことは `scan_runs` に `failed` として残る
(ADR-0041 決定7)。

**だから失敗が 1 件でもあれば警告を出し、読むべき場所を言う。**
終了コードは掃引の実行についてしか語れない。

## 実データは 1 行も読まない

呼ぶのは `ScanService` そのもので、API の同期エンドポイントと**同じコード
パス**である(ADR-0042 決定5)。`SCAN_ALLOWED_HOSTS` の allowlist も
`validate_host` を通るので**ジョブは迂回しない**。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ontology_api.repositories.scan import ScanRepository
from ontology_api.services.scan import ScanService, SecretResolver, default_secret_resolver
from ontology_core.config import Settings, get_settings
from ontology_core.db import create_engine_and_factory

__all__ = ["ScanFailure", "SweepReport", "main", "sweep_sources"]

logger = logging.getLogger(__name__)
# `migrate.py` と同じ理由で明示的にレベルを設定する(他の設定で root の
# レベルが上がっても要約のログが消えないようにする)。
logger.setLevel(logging.INFO)

#: `scan_runs.started_by` に書く値。マネージド ID が分からないときに使う。
#:
#: **人間の ID を借りない。** 分からないときは「ジョブが起動した」と書く
#: (ADR-0035 と同じ態度 — 分からないことを分かったように書かない)。
FALLBACK_ACTOR = "scan-job"


@dataclass(frozen=True)
class ScanFailure:
    """届かなかったソース 1 件。"""

    namespace: str
    name: str
    reason: str


@dataclass
class SweepReport:
    """1 回の掃引の結果。

    **`succeeded` と `failed` の和が `total` になる。** 「数えられなかった」を
    作らないため、例外で抜けたソースも必ずどちらかに入る。
    """

    total: int = 0
    succeeded: int = 0
    failures: list[ScanFailure] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return len(self.failures)

    def summary(self) -> str:
        return (
            f"ソース {self.total} 件を掃引しました"
            f"(成功 {self.succeeded} 件 / 失敗 {self.failed} 件)"
        )


async def sweep_sources(
    *,
    session: AsyncSession,
    settings: Settings,
    actor: str,
    secret_resolver: SecretResolver = default_secret_resolver,
) -> SweepReport:
    """登録済みの全ソースを 1 度ずつスキャンして結果を返す。

    **1 つのソースの失敗が残りを止めない**(ADR-0042 決定4)。想定外の例外が
    出たときは**セッションを巻き戻してから次へ進む** — 巻き戻さないと
    半端なトランザクションが残り、**残り全部が失敗になって症状が原因を
    指さなくなる**。

    **例外を握り潰さない。** 理由は `SweepReport.failures` に入り、
    WARNING でログに出る。
    """
    repo = ScanRepository(session)
    sources = await repo.list_all_source_rows()
    report = SweepReport(total=len(sources))

    for source in sources:
        service = ScanService(session=session, settings=settings, secret_resolver=secret_resolver)
        try:
            run_id, table_count = await service.scan(source=source, actor=actor)
        except Exception as exc:
            # **broad にしているのは掃引を止めないためである。** 想定して
            # いない例外(マネージド ID が使えない等)でも、残りのソースは
            # スキャンできる。理由は必ず残す。
            report.failures.append(
                ScanFailure(namespace=source.namespace, name=source.name, reason=str(exc))
            )
            logger.warning(
                "ソース %s/%s のスキャンに失敗しました: %s", source.namespace, source.name, exc
            )
            # **巻き戻してから次へ進む**(決定4)。
            await session.rollback()
            continue
        report.succeeded += 1
        logger.info(
            "ソース %s/%s をスキャンしました(run=%s、テーブル %s 件)",
            source.namespace,
            source.name,
            run_id,
            table_count,
        )

    return report


def _actor_of(settings: Settings) -> str:
    """`scan_runs.started_by` に書く主体を決める(ADR-0042 決定2)。

    マネージド ID のクライアント ID を使う。**人間が起動していないことが
    記録から読める。**
    """
    return settings.azure_client_id or FALLBACK_ACTOR


async def _run(factory: async_sessionmaker[AsyncSession], settings: Settings) -> SweepReport:
    async with factory() as session:
        return await sweep_sources(session=session, settings=settings, actor=_actor_of(settings))


async def _main() -> int:
    settings = get_settings()
    engine, factory = create_engine_and_factory(settings)
    try:
        report = await _run(factory, settings)
    finally:
        await engine.dispose()

    if report.total == 0:
        # **「0 件だった」を成功として静かに終えない。** 設定漏れ
        # (`SCAN_ALLOWED_HOSTS` が空でソースを登録できていない)と
        # 「本当に 1 件も登録されていない」は運用者にとって同じ見た目に
        # なるので、読むべき場所を言う。
        logger.info(
            "スキャン対象のソースが登録されていません。"
            "POST /namespaces/{ns}/scan-sources で登録してください"
            "(SCAN_ALLOWED_HOSTS に接続先が入っている必要があります)"
        )
        return 0

    if report.failures:
        logger.warning(
            "%s。**終了コードは 0 だが、全ソースに届いたわけではない** — "
            "どこまで読めたかは scan_runs を見ること",
            report.summary(),
        )
    else:
        logger.info(report.summary())
    # **掃引が走ったかどうかだけを終了コードにする**(ADR-0042 決定3)。
    return 0


def main() -> None:
    """`python -m ontology_api.scan_job` のエントリポイント。"""
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
