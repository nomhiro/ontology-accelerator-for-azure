"""想定質問(Competency Questions)を SPARQL エンドポイントに対して実行する。

ADR-0009 決定6。オントロジーの作成者が、自分の名前空間の質問を自分の環境に
対して回せるようにするための CLI。CI で回っているのは
`packages/api/tests/test_competency_run.py`(同梱サンプル)である。

使い方:

    # ローカルの Fuseki に対して同梱サンプルの質問を回す(just check-questions)
    uv run python scripts/check-questions.py samples/retail-core.questions.yaml retail-core

    # 自分の名前空間に対して
    uv run python scripts/check-questions.py my-domain.questions.yaml my-domain

接続先は `ontology_core.config.Settings` と同じ環境変数から読む
(`SPARQL_QUERY_ENDPOINT` など。`.env` があればそれも読む)。

**落ちた質問だけでなく、通った質問も一覧で出す。** 「何件通ったか」ではなく
「どの要求を満たしているか」がオントロジーの説明になるため。
終了コードは、1 件でも落ちたら 1。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from ontology_core.competency import QuestionFileError, load_questions, run_questions
from ontology_core.config import get_settings
from ontology_core.console import say, warn
from ontology_core.sparql.client import FusekiStore


async def _run(questions_path: str, dataset: str) -> int:
    try:
        questions = load_questions(questions_path)
    except QuestionFileError as exc:
        warn(f"想定質問ファイルを読めません: {exc}")
        return 2

    settings = get_settings()
    admin_auth = (
        ("admin", settings.fuseki_admin_password) if settings.fuseki_admin_password else None
    )
    store = FusekiStore(
        query_endpoint=settings.sparql_query_endpoint,
        update_endpoint=settings.sparql_update_endpoint,
        gsp_endpoint=settings.sparql_gsp_endpoint,
        admin_endpoint=settings.fuseki_admin_endpoint,
        admin_auth=admin_auth,
        timeout_seconds=settings.sparql_query_timeout_seconds,
    )
    try:
        results = await run_questions(store, dataset, questions)
    finally:
        await store.aclose()

    say(f"想定質問: {questions_path}  対象: {dataset}")
    say("")
    for r in results:
        mark = "ok  " if r.passed else "NG  "
        say(f"{mark}{r.question.id}: {r.question.question}")
        say(f"      expect={r.question.expect.value}  {r.detail}")
    failed = [r for r in results if not r.passed]
    say("")
    say(f"{len(results) - len(failed)}/{len(results)} 件が満たされています")
    if failed:
        warn("")
        warn("満たされていない要求:")
        for r in failed:
            warn(f"  {r.question.id}: {r.question.question}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="想定質問を SPARQL で検証する")
    parser.add_argument("questions", help="想定質問ファイル (YAML)")
    parser.add_argument("dataset", help="対象のデータセット名 (= 名前空間名)")
    args = parser.parse_args()
    return asyncio.run(_run(args.questions, args.dataset))


if __name__ == "__main__":
    sys.exit(main())
