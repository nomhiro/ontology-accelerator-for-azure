"""定義と実データの乖離を測る(ADR-0047、`P3-05`)。

## 3 つの正本を突き合わせる

| 何を | どこから |
|---|---|
| どんな形を定めたか | 承認済み版の TTL(Blob) |
| どの用語を実データに繋いだか | 有効な R2RML マッピング(PostgreSQL) |
| 実データがどうなっているか | 仮想グラフ(Ontop) |

**3 つのどれが欠けても「乖離なし」とは言わない。** 版が無ければ測る基準が
無く、マッピングが無ければ入口が無く、仮想グラフに届かなければ実データを
見ていない。どれも `unknown` か、呼び出し元での 404 / 503 になる。

## 実データはこちらに来ない

探りが返すのは `ASK` の真偽と `COUNT` の 1 行だけである。**行の中身を
取らない** — [ADR-0046](../../../../../docs/adr/0046-virtual-knowledge-graph.md)
が守っている「実データを実体化しない」を、検査のために壊さない。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.versions import VersionRepository
from ontology_api.repositories.vkg import VkgMappingRepository
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.divergence import (
    ClassDivergence,
    DivergenceReport,
    DivergenceStatus,
    ProbeOutcome,
    boolean_of,
    build_class_divergence,
    class_probe,
    count_of,
    count_planned_probes,
    missing_property_probe,
    plan_probes,
    shape_targets,
    total_probes,
    unmapped_report,
)
from ontology_core.models import OntologyVersionStatus, VkgMapping
from ontology_core.r2rml import R2rmlError, parse_mapping
from ontology_core.vkg import VirtualGraphClient, VirtualGraphError

__all__ = ["DivergenceUnavailableError", "divergence_messages", "measure_divergence"]

_UNMAPPED_CLASS = (
    "マッピングがこのクラスを作っていません。**乖離ではありません** — "
    "実データへの入口がそもそもありません"
)
_UNMAPPED_PATH = (
    "マッピングがこの述語を作っていません。**乖離ではありません** — "
    "実データへの入口がそもそもありません"
)


class DivergenceUnavailableError(Exception):
    """乖離を測れないことを表す。

    **「乖離なし」とは別物である。** 呼び出し元は 404 / 503 として返し、
    空の報告を返さない(ADR-0047 決定6)。
    """


@dataclass(frozen=True)
class _Inputs:
    version: str
    turtle: str
    mapping: VkgMapping


async def _collect(
    session: AsyncSession,
    *,
    blob: OntologyBlobStore,
    namespace: str,
    source: str,
    source_id: int,
) -> _Inputs:
    """3 つの正本を集める。

    Raises:
        DivergenceUnavailableError: どれかが欠けているとき。
    """
    current = next(
        (
            row
            for row in await VersionRepository(session).list_for(namespace)
            if row.status is OntologyVersionStatus.APPROVED
        ),
        None,
    )
    if current is None:
        raise DivergenceUnavailableError(
            f"名前空間 '{namespace}' に承認済みの版がありません。"
            "測る基準が無いので、乖離の有無は判定できません"
        )

    mapping = await VkgMappingRepository(session).active(source_id=source_id, source=source)
    if mapping is None:
        raise DivergenceUnavailableError(
            f"ソース '{source}' に R2RML マッピングが登録されていません。"
            "実データへの入口が無いので、乖離の有無は判定できません"
        )

    try:
        turtle = await blob.get_version(current.blob_path)
    except BlobStoreError as exc:
        # **「読めなかった」を「形が無かった」にしない。**
        raise DivergenceUnavailableError(
            f"承認済み版 '{current.version}' の正本を読めませんでした: {exc}"
        ) from exc

    return _Inputs(version=current.version, turtle=turtle, mapping=mapping)


async def measure_divergence(
    session: AsyncSession,
    *,
    blob: OntologyBlobStore,
    client: VirtualGraphClient,
    endpoint: str,
    namespace: str,
    source: str,
    source_id: int,
    limit: int,
) -> DivergenceReport:
    """定義と実データの乖離を測る(ADR-0047)。

    **状態は変えない。** 読むだけである(SHACL の `.../validate` や
    想定質問の `.../run` と同じ位置づけ)。

    Raises:
        DivergenceUnavailableError: 承認済み版・マッピング・正本のどれかが
            欠けているとき。**空の報告を返さない。**
    """
    inputs = await _collect(
        session, blob=blob, namespace=namespace, source=source, source_id=source_id
    )

    try:
        targets = shape_targets(inputs.turtle)
    except ValueError as exc:
        raise DivergenceUnavailableError(
            f"承認済み版 '{inputs.version}' の TTL を解析できませんでした: {exc}"
        ) from exc

    if not targets:
        # **「形が無かった」を「乖離が無かった」と混ぜない**(決定6)。
        return DivergenceReport(
            version=inputs.version,
            mapping_revision=inputs.mapping.revision,
            no_shapes=True,
        )

    try:
        parsed = parse_mapping(inputs.mapping.content)
    except R2rmlError as exc:
        # 登録時に検査しているのでここには来ないが、来たら**測らない**。
        raise DivergenceUnavailableError(
            f"マッピング(改訂 {inputs.mapping.revision})を解析できませんでした: {exc}"
        ) from exc

    mapped_classes = frozenset(parsed.classes)
    mapped_predicates = frozenset(parsed.predicates)

    plan = plan_probes(
        targets,
        mapped_classes=mapped_classes,
        mapped_predicates=mapped_predicates,
        limit=limit,
    )
    planned = {target.shape for target, _ in plan}
    needed = total_probes(
        targets, mapped_classes=mapped_classes, mapped_predicates=mapped_predicates
    )
    issued = count_planned_probes(plan)

    results: list[ClassDivergence] = []
    for target, paths in plan:
        class_outcome = await _ask(
            client, endpoint=endpoint, query=class_probe(target.target_class)
        )
        property_outcomes: dict[str, ProbeOutcome] = {}
        if class_outcome.value is True:
            for path in paths:
                property_outcomes[path] = await _count(
                    client,
                    endpoint=endpoint,
                    query=missing_property_probe(target.target_class, path),
                )
        else:
            # **クラスに実データが無い(または読めなかった)なら、プロパティの
            # 探りは投げない。** 投げても意味のある答えにならないうえ、
            # 顧客 DB への無駄なクエリになる。**投げなかった分は
            # `issued` から引く。**
            issued -= len(paths)
        unmapped_paths = tuple(p for p in target.required_paths if p not in mapped_predicates)
        results.append(
            build_class_divergence(
                target,
                class_outcome=class_outcome,
                property_outcomes=property_outcomes,
                unmapped_paths=unmapped_paths,
                unmapped_note=_UNMAPPED_PATH,
            )
        )

    # **探りを投げなかった形も報告に入れる**(決定3・5)。欠落させると、
    # 呼び出し側が「載っていないものは問題なし」と読む。
    for target in targets:
        if target.shape in planned:
            continue
        if target.target_class not in mapped_classes:
            results.append(
                unmapped_report(target, mapped_predicates=mapped_predicates, note=_UNMAPPED_CLASS)
            )
            continue
        results.append(
            build_class_divergence(
                target,
                class_outcome=ProbeOutcome(
                    value=None,
                    note=f"1 回の報告で投げる探りの上限({limit})に達したため調べていません",
                    failed=True,
                ),
            )
        )

    classes = tuple(sorted(results, key=lambda item: (item.target_class, item.shape)))
    return DivergenceReport(
        classes=classes,
        skipped_probes=max(0, needed - count_planned_probes(plan)),
        issued_probes=issued,
        version=inputs.version,
        mapping_revision=inputs.mapping.revision,
    )


async def _ask(client: VirtualGraphClient, *, endpoint: str, query: str) -> ProbeOutcome:
    """`ASK` の探りを 1 本投げる。

    **失敗を `False` にしない。** 「実データが無い」と「聞けなかった」は
    対処が違う。
    """
    try:
        payload = await client.query(query, endpoint=endpoint)
    except VirtualGraphError as exc:
        return ProbeOutcome(value=None, note=f"仮想グラフに聞けませんでした: {exc}", failed=True)
    value = boolean_of(payload)
    if value is None:
        return ProbeOutcome(
            value=None, note="仮想グラフの応答から真偽を読めませんでした", failed=True
        )
    return ProbeOutcome(value=value)


async def _count(client: VirtualGraphClient, *, endpoint: str, query: str) -> ProbeOutcome:
    """`COUNT` の探りを 1 本投げる。

    **失敗を `0` にしない。** 「欠けていない」と「数えられなかった」は違う。
    """
    try:
        payload = await client.query(query, endpoint=endpoint)
    except VirtualGraphError as exc:
        return ProbeOutcome(value=None, note=f"仮想グラフに聞けませんでした: {exc}", failed=True)
    value = count_of(payload)
    if value is None:
        return ProbeOutcome(
            value=None, note="仮想グラフの応答から件数を読めませんでした", failed=True
        )
    return ProbeOutcome(value=value)


def divergence_messages(report: DivergenceReport) -> list[str]:
    """報告を人が読める行にする。

    **`unknown` を落とさない。** 乖離だけを並べると、調べられなかったものが
    「問題なし」に見える。
    """
    lines: list[str] = []
    for item in report.diverged:
        if item.status is DivergenceStatus.EMPTY:
            lines.append(f"{item.target_class}: 実データが 1 件もありません")
        for prop in item.properties:
            if prop.missing_count:
                lines.append(
                    f"{item.target_class} / {prop.path}: "
                    f"{prop.missing_count} 件で必須のプロパティが欠けています"
                )
    for item in report.unknown:
        lines.append(f"{item.target_class}: 調べられませんでした({item.note})")
    for item in report.classes:
        for prop in item.properties:
            if prop.status is DivergenceStatus.UNKNOWN:
                lines.append(
                    f"{item.target_class} / {prop.path}: 調べられませんでした({prop.note})"
                )
    if report.skipped_probes:
        lines.append(f"探り {report.skipped_probes} 本を上限に達したため投げていません")
    return lines
