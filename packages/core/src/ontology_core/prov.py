"""監査証跡を W3C PROV-O の Turtle として書き出す(ADR-0026、`P2A-07`)。

[ADR-0006](../../../../docs/adr/0006-ontology-versioning-and-audit.md) 決定3 は
「監査証跡を PostgreSQL に記録し、**PROV-O で表現する**」と決めていたが、
表現の側が未実装だった。記録は独自スキーマのままで、**W3C 標準忠実を掲げる
プロジェクトが自身のメタデータだけ独自スキーマで出している**状態だった。

## 派生は記録されているときだけ出す

**`prov:wasDerivedFrom` は `edited_from` が記録されている版にだけ出す**
(ADR-0026 決定2 / [ADR-0027](../../../../docs/adr/0027-revision-lineage.md)
決定5)。

承認の順序は派生ではない。`publish` に `base_version` を渡さなかった版は
**何から編集されたか分からない**ので、辺を出さない。そこで「当時の最新版」を
親として出すのは、測っていないことを標準語彙で主張することである。しかも
**相互運用性があるぶん害が大きい** — 外部の PROV ツールは `wasDerivedFrom` を
著作の系譜として表示し、誰も疑わない。

**`ont:editedFromRecorded` を真偽どちらでも出す。** これが無いと、
`prov:wasDerivedFrom` が無いことが「根である」とも「記録していない」とも
読める。**版の行が引けなかったときはこの旗も出さない** — それは
「行を見て、記録されていなかった」ではなく「**行を見られなかった**」であり、
`false` と混ぜない(ADR-0027 決定5)。

**`prov:wasRevisionOf` は使わない。** `wasDerivedFrom` の下位で、より強く
「改訂である」と主張する。記録しているのは「この版を編集するとき基準にした版」
であって、両者が改訂の関係にあるとまでは言えない。

## 主体のクラスは矛盾しないときだけ出す

ADR-0026 決定3 は主体を `prov:Agent` のままにしていた(種別を記録して
いなかったため)。[ADR-0035](../../../../docs/adr/0035-actor-type.md) が
`audit_events.actor_type` を足したので、**条件付きで
`prov:Person` / `prov:SoftwareAgent` を出す**。

**種別は行為ごとに `ont:actorType` として出す**(決定4)。主体の IRI は
行為ごとではなく主体ごとなので、種別を主体に付けると 1 つの IRI に複数の
値がぶら下がり**「この主体は user でも unknown でもある」**と読める
(`idtyp` を設定する前と後の行為が混ざると実際に起きる)。

**主体のクラスは、その書き出しに含まれるその主体の行為がすべて一致して
いるときだけ出す。** 行為ごとの記録は測った事実だが、主体のクラスは
**主体についての主張**であり、主張には一致が要る。食い違いをどちらかに
丸めると「測っていないことを標準語彙で主張する」形になる(決定2 と同じ
論点)。`prov:SoftwareAgent` と書けば外部ツールは「自動生成」と読む —
四眼原則を記録する監査証跡でそれは最悪の誤りである。

**`actor_type` が `None` の行は `ont:actorType` を出さない**(ADR-0035
決定5)。`"unknown"` を出すと「問うて、分からなかった」と読めるが、実際には
**問うていない**(この機能より前に書かれた行である)。

## 行為の種類は落とさない

PROV-O は「誰が・いつ・何に」を標準化するが、**行為の種類はドメインの語彙**
である。`prov:Activity` だけにすると「何をしたか」が消えるので、
`ont:Publish` のような下位クラス(`rdfs:subClassOf prov:Activity`)を併記する
(決定4)。純粋な PROV-O だけを解する相手にも `prov:Activity` として読める。

**`ont:action` に生の文字列も必ず書く。** 対応表(`ACTIVITY_TYPES`)が新しい
`action` に追いつかなくても、記録された値そのものは失われない。

## 切り詰めは RDF の中に書く

**RDF は「無い」と「返していない」を区別できない。** 書き出しは
`prov:Bundle` のノードを 1 つ持ち、`ont:truncated` を**真偽どちらでも明示的に**
書く(決定5)。省略すると「この名前空間ではこれだけしか起きていない」と
読まれる。`ont:includes` で各行為を束ねるのは、複数の書き出しを混ぜた後でも
**どの行為が切り詰められた書き出しから来たか**を辿れるようにするため
(それが無いと旗が使えない)。

なお、この束自身の記述を同じ文書に含めている。厳密な PROV の束の意味論では
束の記述は別の束に属するが、**自己記述にしないと切り詰めが伝わらない**ので
こちらを採る。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from urllib.parse import quote

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import PROV, RDF, RDFS, XSD

from ontology_core.graphs import NamespaceNameError, validate_namespace_name, validate_version
from ontology_core.models import ActorType, AuditEvent, OntologyVersion

__all__ = [
    "ACTIVITY_TYPES",
    "AGENT_BASE",
    "AGENT_CLASSES",
    "BUNDLE_BASE",
    "GENERATING_ACTIONS",
    "ONT",
    "REVISION_BASE",
    "VERSION_ACTIONS",
    "referenced_versions",
    "render_provenance",
]

#: 独自語彙の名前空間(ADR-0026 決定7)。
ONT = Namespace("urn:ontology:prov#")

#: 版(`prov:Entity`)の IRI の基底。
#:
#: **版のグラフ IRI(`urn:ontology:graph/...`)を流用しない**(決定7)。
#: あれは「その版のトリプルが載るグラフ」の識別子で、**版そのものではない**。
#: 同じ IRI にすると「グラフに対する操作」と「版に対する操作」が混ざる。
REVISION_BASE = "urn:ontology:revision/"

#: 行為(`prov:Activity`)の IRI の基底。監査イベントの `id` を付ける。
ACTIVITY_BASE = "urn:ontology:activity/"

#: 主体(`prov:Agent`)の IRI の基底。
AGENT_BASE = "urn:ontology:agent/"

#: 書き出し(`prov:Bundle`)の IRI の基底。
BUNDLE_BASE = "urn:ontology:provenance/"

#: 監査の `action` から独自の下位クラス名への対応(ADR-0026 決定4)。
#:
#: **ここに無い `action` は `prov:Activity` のままにする。** 知らない行為を
#: 既知のどれかに丸めるより、種類を名乗らないほうが正しい。生の文字列は
#: `ont:action` に必ず出る。
ACTIVITY_TYPES: Mapping[str, str] = {
    "access-log-purged": "PurgeAccessLog",
    "approved": "Approve",
    "mapping-declared": "DeclareMapping",
    "mapping-revoked": "RevokeMapping",
    "published": "Publish",
    "questions-revised": "ReviseQuestions",
    "rejected": "Reject",
    "submitted": "Submit",
    "superseded": "Supersede",
}

#: 対象が版である行為。`subject` を `prov:Entity` に写す(ADR-0026 決定4)。
VERSION_ACTIONS = frozenset(
    {"approved", "published", "rejected", "submitted", "superseded"},
)

#: 主体の種別から PROV-O のクラスへの対応(ADR-0035 決定4)。
#:
#: **`UNKNOWN` は入っていない。** 分からない主体に下位クラスを名乗らせない。
#: `prov:Agent` は別に必ず出すので、ここに無い種別は「主体である」までしか
#: 主張しない。
AGENT_CLASSES: Mapping[ActorType, URIRef] = {
    ActorType.USER: PROV.Person,
    ActorType.SERVICE_PRINCIPAL: PROV.SoftwareAgent,
}

#: 版を**生む**行為。`prov:generated` になる。
#:
#: **公開だけである。** ここを広げると「1 つの実体が 5 回生成された」という
#: 読めない記録になる(承認や却下は既にある版に対する行為である)。
GENERATING_ACTIONS = frozenset({"published"})


def _safe_segment(value: str) -> str:
    """IRI の 1 セグメントとして安全な文字列に直す。

    **`actor` は外部由来である。** `AUTH_MODE=disabled` では任意の文字列が
    入りうるし、空白や `>` が混ざると**生成した Turtle が壊れる**
    (`<urn:ontology:agent/a> .>` のような形になる)。
    百分率符号化しておけば GUID はそのまま読め、危険な文字だけが逃げる。
    """
    return quote(value, safe="")


def _subject_version(namespace: str, subject: str) -> str | None:
    """`<名前空間>@<バージョン>` 形の対象からバージョンを取り出す。

    形が違えば `None` を返す。**推測しない** — 版でないものを版として
    出すほうが、版の情報が出ないより悪い。呼び出し側は `None` でも
    `ont:subject` に生の文字列を残す。

    `questions-revised` の対象は `<名前空間>#questions@<改訂>` で `@` を
    含むため、**素朴に `@` で分けると改訂番号が版に化ける。** 前置きを
    `<名前空間>@` で固定しているのはそのためである。
    """
    prefix = f"{namespace}@"
    if not subject.startswith(prefix):
        return None
    version = subject[len(prefix) :]
    try:
        validate_version(version)
    except NamespaceNameError:
        return None
    return version


def _revision_iri(namespace: str, version: str) -> URIRef:
    """版の IRI を組み立てる。"""
    return URIRef(f"{REVISION_BASE}{_safe_segment(namespace)}/{_safe_segment(version)}")


def referenced_versions(events: Sequence[AuditEvent], *, namespace: str) -> set[str]:
    """監査イベントが参照している版を返す(ADR-0027 決定6)。

    ルータはこの集合だけを引いて `render_provenance` に渡す。
    **名前空間の全版を引かない。**

    **対象の解析規則をこのモジュールに閉じるためでもある。**
    `<名前空間>@<バージョン>` の読み方が呼び出し側にも書かれていると、
    片方だけ直したときに「実体は出るが系譜が出ない」という静かな不整合に
    なる。
    """
    found: set[str] = set()
    for event in events:
        if event.action not in VERSION_ACTIONS:
            continue
        if (version := _subject_version(namespace, event.subject)) is not None:
            found.add(version)
    return found


def _add_lineage(
    graph: Graph,
    *,
    namespace: str,
    revision: URIRef,
    row: OntologyVersion | None,
) -> None:
    """版の実体に系譜を足す(ADR-0027 決定5)。

    3 段の「分からなさ」を区別する。

    | 状況 | 出力 |
    |---|---|
    | 行が引けた・記録あり・親あり | `ont:editedFromRecorded true` + `prov:wasDerivedFrom` |
    | 行が引けた・記録あり・親なし | `ont:editedFromRecorded true` のみ(この名前空間の根) |
    | 行が引けた・記録なし | `ont:editedFromRecorded false` |
    | **行が引けなかった** | **何も出さない** |

    最後の行が本質である。`false` を出すと「行を見て、記録されていなかった」
    と読めるが、実際には**行を見られなかった**。`audit_events` には
    名前空間への外部キーが無いので、名前空間を削除して同名で作り直すと
    版の行が無い監査イベントが残りうる。
    """
    if row is None:
        return
    # **真偽どちらでも書く。** これが無いと `prov:wasDerivedFrom` の不在が
    # 「根である」とも「記録していない」とも読める。
    graph.add((revision, ONT.editedFromRecorded, Literal(row.edited_from_recorded)))
    if not row.edited_from_recorded or row.edited_from is None:
        return
    parent = _revision_iri(namespace, row.edited_from)
    graph.add((parent, RDF.type, PROV.Entity))
    graph.add((parent, ONT.namespace, Literal(namespace)))
    graph.add((parent, ONT.version, Literal(row.edited_from)))
    graph.add((revision, PROV.wasDerivedFrom, parent))


def _agent_classes(events: Sequence[AuditEvent]) -> dict[str, URIRef]:
    """主体ごとに出せる PROV-O の下位クラスを決める(ADR-0035 決定4)。

    **一致しているときだけ返す。** 主体の IRI は行為ごとではなく主体ごと
    なので、種別を主体に付けると 1 つの IRI に複数の値がぶら下がる。
    `idtyp` を任意クレームとして設定する前と後の行為が同じ書き出しに混ざると
    実際に起きる。

    | その主体の行為の記録 | 返す値 |
    |---|---|
    | すべて `user` | `prov:Person` |
    | すべて `service-principal` | `prov:SoftwareAgent` |
    | `unknown` を含む / 食い違う / `None` を含む | **含めない**(`prov:Agent` のみ) |

    行為ごとの記録は**測った事実**だが、主体のクラスは**主体についての主張**
    である。主張には一致が要る。
    """
    seen: dict[str, set[ActorType | None]] = {}
    for event in events:
        seen.setdefault(event.actor, set()).add(event.actor_type)
    classes: dict[str, URIRef] = {}
    for actor, types in seen.items():
        if len(types) != 1:
            continue
        only = next(iter(types))
        if only is None:
            continue
        if (cls := AGENT_CLASSES.get(only)) is not None:
            classes[actor] = cls
    return classes


def render_provenance(
    events: Sequence[AuditEvent],
    *,
    namespace: str,
    truncated: bool,
    exported_at: datetime,
    versions: Sequence[OntologyVersion] = (),
) -> str:
    """監査イベントを PROV-O の Turtle にする。

    Args:
        events: 書き出す監査イベント。並び順は結果に影響しない(RDF は順序を
            持たない)。**主体のクラスはこの集合の全体で決まる**(ADR-0035
            決定4)ので、期間で切った書き出しと全件の書き出しで
            `prov:Person` が付くかどうかは変わりうる。行為ごとの
            `ont:actorType` は変わらない。
        namespace: 書き出し対象の名前空間。`subject` を版として読むときの
            前置きにも使う。
        truncated: 上限で切り詰めたか。**偽でも `ont:truncated false` として
            明示的に出る**(ADR-0026 決定5)。
        exported_at: 書き出した時刻。タイムゾーン付きで渡すこと。
        versions: 系譜を引くための版の行(`referenced_versions` で絞った
            ものを渡す)。**渡されなかった版には `ont:editedFromRecorded` を
            出さない** — それは「記録されていなかった」ではなく
            「**行を見られなかった**」であり、`false` と混ぜない
            (ADR-0027 決定5)。

    Returns:
        Turtle。

    Raises:
        NamespaceNameError: `namespace` が名前空間名として使えないとき。
    """
    validate_namespace_name(namespace)

    lineage = {row.version: row for row in versions if row.namespace == namespace}
    # **書き出し全体を先に見る**(ADR-0035 決定4)。主体のクラスは、その主体の
    # すべての行為が一致しているときだけ出す。
    agent_classes = _agent_classes(events)

    graph = Graph()
    graph.bind("prov", PROV)
    graph.bind("ont", ONT)
    graph.bind("rdfs", RDFS)

    bundle = URIRef(f"{BUNDLE_BASE}{_safe_segment(namespace)}")
    graph.add((bundle, RDF.type, PROV.Bundle))
    graph.add((bundle, ONT.namespace, Literal(namespace)))
    graph.add((bundle, ONT.eventCount, Literal(len(events))))
    # **真偽どちらでも書く。** 省略すると「切り詰めていない」と読まれる。
    graph.add((bundle, ONT.truncated, Literal(truncated)))
    graph.add((bundle, ONT.exportedAt, Literal(exported_at, datatype=XSD.dateTime)))

    for event in events:
        activity = URIRef(f"{ACTIVITY_BASE}{event.id}")
        graph.add((bundle, ONT.includes, activity))
        graph.add((activity, RDF.type, PROV.Activity))
        if (local := ACTIVITY_TYPES.get(event.action)) is not None:
            graph.add((activity, RDF.type, ONT[local]))
        # 生の値も必ず残す(対応表が追いつかなくても失わない)。
        graph.add((activity, ONT.action, Literal(event.action)))
        graph.add((activity, ONT.subject, Literal(event.subject)))
        graph.add((activity, PROV.endedAtTime, Literal(event.occurred_at, datatype=XSD.dateTime)))

        agent = URIRef(f"{AGENT_BASE}{_safe_segment(event.actor)}")
        # **`prov:Agent` は種別に関わらず必ず出す**(ADR-0026 決定4 と同じ形)。
        # 下位クラスの推論をしない相手にも主体として読める。
        graph.add((agent, RDF.type, PROV.Agent))
        graph.add((agent, ONT.principalId, Literal(event.actor)))
        graph.add((activity, PROV.wasAssociatedWith, agent))
        if (agent_class := agent_classes.get(event.actor)) is not None:
            graph.add((agent, RDF.type, agent_class))
        # **種別は行為に付ける**(ADR-0035 決定4)。主体に付けると同じ IRI に
        # 複数の値がぶら下がる。**`None` のときは出さない**(決定5)——
        # それは「問うて、分からなかった」ではなく「問うていない」である。
        if event.actor_type is not None:
            graph.add((activity, ONT.actorType, Literal(event.actor_type.value)))

        if event.reason:
            graph.add((activity, RDFS.comment, Literal(event.reason)))
        if event.diff is not None:
            # 意味的差分の**要約**の JSON(ADR-0016 決定7)。RDF に展開しない
            # — 全トリプルを載せると書き出しが非有界に育つ。
            graph.add((activity, ONT.diffSummary, Literal(event.diff)))

        if event.action in VERSION_ACTIONS:
            version = _subject_version(namespace, event.subject)
            if version is not None:
                revision = _revision_iri(namespace, version)
                graph.add((revision, RDF.type, PROV.Entity))
                graph.add((revision, ONT.namespace, Literal(namespace)))
                graph.add((revision, ONT.version, Literal(version)))
                predicate = PROV.generated if event.action in GENERATING_ACTIONS else PROV.used
                graph.add((activity, predicate, revision))
                _add_lineage(
                    graph, namespace=namespace, revision=revision, row=lineage.get(version)
                )

    serialized = graph.serialize(format="turtle")
    return serialized if isinstance(serialized, str) else serialized.decode("utf-8")
