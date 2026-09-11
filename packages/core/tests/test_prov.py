"""監査証跡の PROV-O 表現(ADR-0026 / ADR-0027、`P2A-07` / `P2A-15`)。

ADR-0006 決定3 は「監査証跡を PROV-O で表現する」と決め、`prov:Entity` /
`prov:Activity` / `prov:Agent` / **`prov:wasDerivedFrom`** という具体名まで
挙げていた。ここで固定するのは、**何を出し、何を出さないか**である。

1. **`prov:wasDerivedFrom` は系譜が記録されている版にだけ出す**
   (ADR-0026 決定2 / ADR-0027 決定5)。承認の順序は派生ではない。
   `prov:wasRevisionOf` は使わない(`wasDerivedFrom` より強い主張になる)
2. **「記録していない」が読み取れる**(ADR-0027 決定5)。`ont:editedFromRecorded`
   は真偽どちらでも出し、**版の行が引けなかったときは出さない**
   (3 段の「分からなさ」を区別する)
3. **主体を `prov:Person` / `prov:SoftwareAgent` に分けない**(ADR-0026 決定3)。
   人間かサービスプリンシパルかを記録していない
4. **行為の種類を落とさない**(ADR-0026 決定4)
5. **切り詰めを RDF の中に書く**(ADR-0026 決定5)

**検査は Turtle の文字列ではなくパースし直したグラフに対して行う。**
文字列一致だと接頭辞の付き方や整形の違いで落ちるし、逆に
**壊れた Turtle を「それらしい文字列が入っている」で通してしまう。**
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import PROV, RDF, RDFS

from ontology_core.graphs import NamespaceNameError
from ontology_core.models import AuditEvent, OntologyVersion, OntologyVersionStatus
from ontology_core.prov import (
    ACTIVITY_TYPES,
    AGENT_BASE,
    BUNDLE_BASE,
    ONT,
    REVISION_BASE,
    referenced_versions,
    render_provenance,
)

_NS = "retail-core"
_AT = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
_EXPORTED = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
_DIFF = '{"added": 3}'


def _event(
    *,
    id: int = 1,
    action: str = "published",
    actor: str = "actor-oid",
    subject: str = f"{_NS}@2.0.0",
    reason: str = "",
    diff: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        id=id,
        namespace=_NS,
        action=action,
        actor=actor,
        occurred_at=_AT,
        subject=subject,
        reason=reason,
        diff=diff,
    )


def _render(
    *events: AuditEvent,
    truncated: bool = False,
    namespace: str = _NS,
    versions: Sequence[OntologyVersion] = (),
) -> Graph:
    """書き出してパースし直す。**壊れた Turtle はここで例外になる。**"""
    turtle = render_provenance(
        events,
        namespace=namespace,
        truncated=truncated,
        exported_at=_EXPORTED,
        versions=versions,
    )
    graph = Graph()
    graph.parse(data=turtle, format="turtle")
    return graph


_BUNDLE = URIRef(f"{BUNDLE_BASE}{_NS}")
_REVISION = URIRef(f"{REVISION_BASE}{_NS}/2.0.0")


def _activity(id: int) -> URIRef:
    return URIRef(f"urn:ontology:activity/{id}")


# ------------------------------------------- 測っていない派生関係を主張しない


@pytest.mark.parametrize(
    "predicate",
    [PROV.wasDerivedFrom, PROV.wasRevisionOf, PROV.wasInfluencedBy, PROV.specializationOf],
)
def test_系譜が記録されていなければ派生を表す述語を出さない(predicate: URIRef) -> None:
    """**承認の順序から派生を出さない**(ADR-0026 決定2 / ADR-0027 決定5)。

    ここでは版の行を渡していない(= 系譜が引けない)。記録されているのは
    「2.0.0 は 1.0.0 の次に承認された」であって「2.0.0 は 1.0.0 から
    派生した」ではない。**承認の順序を並べて辺を作ってはいけない。**

    **相互運用性があるぶん害が大きい** — 外部の PROV ツールは
    `wasDerivedFrom` を著作の系譜として表示し、誰も疑わない。
    """
    graph = _render(
        _event(id=1, action="published", subject=f"{_NS}@1.0.0"),
        _event(id=2, action="approved", subject=f"{_NS}@1.0.0"),
        _event(id=3, action="superseded", subject=f"{_NS}@1.0.0"),
        _event(id=4, action="published", subject=f"{_NS}@2.0.0"),
        _event(id=5, action="approved", subject=f"{_NS}@2.0.0"),
    )
    assert not list(graph.triples((None, predicate, None))), (
        f"{predicate} は派生を主張する。承認の順序から出してはならない"
    )


@pytest.mark.parametrize("subclass", [PROV.Person, PROV.SoftwareAgent, PROV.Organization])
def test_主体を人間と機械に分けない(subclass: URIRef) -> None:
    """**区別できないものを区別して出さない**(ADR-0026 決定3)。

    `audit_events.actor` は Entra のオブジェクト ID だけで、人間か
    サービスプリンシパルかを持っていない。`prov:SoftwareAgent` と書けば
    外部ツールは「自動生成」と読む — 四眼原則を記録する監査証跡で
    人間の承認を自動化された行為として見せるのは最悪の誤りである。
    """
    graph = _render(_event())
    assert not list(graph.triples((None, RDF.type, subclass)))


def test_主体は_prov_Agent_として出る() -> None:
    graph = _render(_event(actor="alice-oid"))
    agent = URIRef(f"{AGENT_BASE}alice-oid")
    assert (agent, RDF.type, PROV.Agent) in graph
    # IRI から文字列を切り出さずに主体 ID を引けるようにする。
    assert (agent, ONT.principalId, Literal("alice-oid")) in graph
    assert (_activity(1), PROV.wasAssociatedWith, agent) in graph


# --------------------------------------------------------------- 版との関係


def test_公開は版を生む() -> None:
    """**`published` だけが `prov:generated` である**(ADR-0026 決定4)。"""
    graph = _render(_event(action="published"))
    assert (_activity(1), PROV.generated, _REVISION) in graph
    assert (_REVISION, RDF.type, PROV.Entity) in graph
    assert (_REVISION, ONT.version, Literal("2.0.0")) in graph


@pytest.mark.parametrize("action", ["submitted", "approved", "rejected", "superseded"])
def test_公開以外は既にある版を使う(action: str) -> None:
    """**全部 `generated` にすると「1 つの実体が 5 回生成された」になる。**"""
    graph = _render(_event(action=action))
    assert (_activity(1), PROV.used, _REVISION) in graph
    assert (_activity(1), PROV.generated, _REVISION) not in graph


@pytest.mark.parametrize(
    ("action", "subject"),
    [
        ("mapping-declared", "https://a/#X skos:closeMatch https://b/#Y"),
        ("mapping-revoked", "https://a/#X -> https://b/#Y"),
        ("questions-revised", f"{_NS}#questions@3"),
        ("access-log-purged", f"{_NS}/access-log"),
    ],
)
def test_版でない対象に_prov_Entity_を作らない(action: str, subject: str) -> None:
    """**版ではないものを版として出さない**(ADR-0026 決定4)。

    `questions-revised` の対象は `<名前空間>#questions@<改訂>` で `@` を
    含むため、素朴に `@` で分けると**改訂番号が版に化ける。**
    """
    graph = _render(_event(action=action, subject=subject))
    assert not list(graph.triples((None, RDF.type, PROV.Entity)))
    assert not list(graph.triples((_activity(1), PROV.used, None)))
    assert not list(graph.triples((_activity(1), PROV.generated, None)))
    # **対象そのものは必ず残る。**
    assert (_activity(1), ONT.subject, Literal(subject)) in graph


def test_版の形でない対象でも生の文字列は残る() -> None:
    """**推測しない。しかし落とさない**(`_subject_version` が `None` を返す経路)。

    版を表す行為の対象が想定の形でなければ `prov:Entity` は作らないが、
    記録された文字列は `ont:subject` に残る。ここを落とすと
    「情報が無い」と「形が違った」が区別できなくなる。
    """
    graph = _render(_event(action="approved", subject="other-ns@1.0.0"))
    assert not list(graph.triples((None, RDF.type, PROV.Entity)))
    assert (_activity(1), ONT.subject, Literal("other-ns@1.0.0")) in graph
    assert (_activity(1), ONT.action, Literal("approved")) in graph


def test_版として使えない文字を含む対象は版にしない() -> None:
    """`validate_version` を通らない対象を IRI に組み込まない。"""
    subject = f"{_NS}@../../etc"
    graph = _render(_event(action="published", subject=subject))
    assert not list(graph.triples((None, RDF.type, PROV.Entity)))
    assert (_activity(1), ONT.subject, Literal(subject)) in graph


# --------------------------------------------------------------- 行為の種類


@pytest.mark.parametrize(("action", "local"), sorted(ACTIVITY_TYPES.items()))
def test_行為の種類を独自の下位クラスで保つ(action: str, local: str) -> None:
    """**`prov:Activity` だけにすると「何をしたか」が消える**(決定4)。"""
    graph = _render(_event(action=action, subject=f"{_NS}@2.0.0"))
    assert (_activity(1), RDF.type, PROV.Activity) in graph
    assert (_activity(1), RDF.type, ONT[local]) in graph


def test_対応表に無い行為は種類を名乗らない() -> None:
    """**知らない行為を既知のどれかに丸めない。**

    新しい `action` が増えて対応表が追いつかなくても、`prov:Activity` として
    読めて**生の文字列は失われない**。ここで既定のクラスを当てると、
    未知の行為が既知の行為として集計される。
    """
    graph = _render(_event(action="frobnicated", subject="何か"))
    assert set(graph.objects(_activity(1), RDF.type)) == {PROV.Activity}
    assert (_activity(1), ONT.action, Literal("frobnicated")) in graph


def test_生の_action_を必ず出す() -> None:
    graph = _render(_event(action="published"))
    assert (_activity(1), ONT.action, Literal("published")) in graph


def test_起きた時刻は_prov_endedAtTime_で出る() -> None:
    graph = _render(_event())
    assert set(graph.objects(_activity(1), PROV.endedAtTime)) == {Literal(_AT)}


# --------------------------------------------------------------- 理由と差分


def test_理由は_rdfs_comment_で出る() -> None:
    graph = _render(_event(reason="型の誤りを直した"))
    assert (_activity(1), RDFS.comment, Literal("型の誤りを直した")) in graph


def test_理由が空なら_comment_を出さない() -> None:
    """空文字列のコメントを出すと「理由が書かれている」と読める。"""
    graph = _render(_event(reason=""))
    assert not list(graph.triples((_activity(1), RDFS.comment, None)))


def test_差分の要約はそのまま載せる() -> None:
    """**RDF に展開しない**(ADR-0016 決定7)。全トリプルを載せると非有界に育つ。"""
    graph = _render(_event(diff=_DIFF))
    assert (_activity(1), ONT.diffSummary, Literal(_DIFF)) in graph


def test_差分が無ければ出さない() -> None:
    graph = _render(_event(diff=None))
    assert not list(graph.triples((_activity(1), ONT.diffSummary, None)))


# ------------------------------------------------------------------- 切り詰め


def test_切り詰めたことを_RDF_の中に書く() -> None:
    """**RDF は「無い」と「返していない」を区別できない**(ADR-0026 決定5)。

    ADR-0016 決定5 / ADR-0020 決定3 / ADR-0021 決定1 / ADR-0022 決定5 /
    ADR-0025 決定3 と同じ原則の 6 例目。
    """
    graph = _render(_event(), truncated=True)
    assert (_BUNDLE, RDF.type, PROV.Bundle) in graph
    assert (_BUNDLE, ONT.truncated, Literal(True)) in graph
    assert (_BUNDLE, ONT.eventCount, Literal(1)) in graph
    assert (_BUNDLE, ONT.exportedAt, Literal(_EXPORTED)) in graph


def test_切り詰めていないことも明示的に書く() -> None:
    """**省略しない。** 省略すると「全部だ」とも「言っていない」とも読める。"""
    graph = _render(_event(), truncated=False)
    assert (_BUNDLE, ONT.truncated, Literal(False)) in graph


def test_束が全ての行為を含む() -> None:
    """**複数の書き出しを混ぜた後も、どれが切り詰められた束から来たか辿れる。**

    この結び付きが無いと `ont:truncated` が使えない(旗だけあっても、どの
    行為がその束の中身か分からない)。
    """
    graph = _render(_event(id=1), _event(id=2), _event(id=3))
    assert set(graph.objects(_BUNDLE, ONT.includes)) == {
        _activity(1),
        _activity(2),
        _activity(3),
    }


def test_空の書き出しでも束は出る() -> None:
    """**「0 件」と「書き出していない」を区別する。**

    空の Turtle を返すと、受け取った側は名前空間を間違えたのかどうかも
    分からない。
    """
    graph = _render(truncated=False)
    assert (_BUNDLE, ONT.eventCount, Literal(0)) in graph
    assert (_BUNDLE, ONT.truncated, Literal(False)) in graph
    assert not list(graph.triples((None, RDF.type, PROV.Activity)))


# --------------------------------------------------------------- 壊れた入力


@pytest.mark.parametrize(
    "actor",
    [
        "a> . <urn:x> <urn:y> <urn:z",
        "with space",
        "日本語",
        "a#b?c",
    ],
)
def test_主体の文字列で_Turtle_を壊せない(actor: str) -> None:
    """**`actor` は外部由来である。**

    `AUTH_MODE=disabled` では任意の文字列が入りうる。符号化しないと
    IRI が途中で閉じてしまい、**書き出し全体がパースできなくなる**か、
    最悪トリプルが注入される。

    `_render` がパースし直すので、壊れていればここで例外になる。
    """
    graph = _render(_event(actor=actor))
    # 生の値は literal に残る(literal は rdflib が逃がす)。
    assert (None, ONT.principalId, Literal(actor)) in graph
    # 注入されていないこと。
    assert (URIRef("urn:x"), URIRef("urn:y"), URIRef("urn:z")) not in graph


def test_名前空間名は検証する() -> None:
    """名前空間名はセキュリティ境界である(不変条件5)。"""
    with pytest.raises(NamespaceNameError):
        render_provenance((), namespace="../etc", truncated=False, exported_at=_EXPORTED)


# --------------------------------------------------------------- 版の系譜


def _version(
    *,
    version: str = "2.0.0",
    edited_from: str | None = None,
    edited_from_recorded: bool = False,
    namespace: str = _NS,
) -> OntologyVersion:
    return OntologyVersion(
        namespace=namespace,
        version=version,
        content_hash="0" * 64,
        status=OntologyVersionStatus.APPROVED,
        graph_iri=f"urn:ontology:graph/{namespace}/{version}",
        blob_path=f"approved/{namespace}/{version}.ttl",
        created_at=_AT,
        created_by="actor-oid",
        edited_from=edited_from,
        edited_from_recorded=edited_from_recorded,
    )


def test_記録されている系譜は_prov_wasDerivedFrom_で出る() -> None:
    """**測った事実なので出す**(ADR-0027 決定5)。

    `publish` に `base_version` を渡した版は「その版から編集した」ことが
    記録されている。
    """
    graph = _render(
        _event(action="published", subject=f"{_NS}@2.0.0"),
        versions=(_version(version="2.0.0", edited_from="1.0.0", edited_from_recorded=True),),
    )
    parent = URIRef(f"{REVISION_BASE}{_NS}/1.0.0")
    assert (_REVISION, PROV.wasDerivedFrom, parent) in graph
    assert (_REVISION, ONT.editedFromRecorded, Literal(True)) in graph
    # **親も実体として型付けする。** 辺の先が型無しだと読み手が版だと分からない。
    assert (parent, RDF.type, PROV.Entity) in graph
    assert (parent, ONT.version, Literal("1.0.0")) in graph


def test_先行する版が無かったことは記録として出るが辺は出ない() -> None:
    """**「根である」と「記録していない」を区別する**(ADR-0027 決定1)。

    名前空間の最初の版は `edited_from_recorded = true` かつ
    `edited_from = NULL` である。辺は出ないが、**出ない理由が分かる。**
    """
    graph = _render(
        _event(action="published", subject=f"{_NS}@2.0.0"),
        versions=(_version(version="2.0.0", edited_from=None, edited_from_recorded=True),),
    )
    assert (_REVISION, ONT.editedFromRecorded, Literal(True)) in graph
    assert not list(graph.triples((_REVISION, PROV.wasDerivedFrom, None)))


def test_記録されていない系譜は_false_として出る() -> None:
    """**無言の欠落にしない**(ADR-0027 決定5)。

    これが無いと `prov:wasDerivedFrom` の不在が「根である」とも
    「記録していない」とも読める。RDF は「無い」と「記録していない」を
    区別できない。
    """
    graph = _render(
        _event(action="published", subject=f"{_NS}@2.0.0"),
        versions=(_version(version="2.0.0", edited_from_recorded=False),),
    )
    assert (_REVISION, ONT.editedFromRecorded, Literal(False)) in graph
    assert not list(graph.triples((_REVISION, PROV.wasDerivedFrom, None)))


def test_記録なしなのに親があっても辺を出さない() -> None:
    """**旗が偽なら値は信じない**(ADR-0027 決定1 の状態表)。

    `recorded=false` かつ `edited_from` が非 NULL という組み合わせは
    正本には現れないが、ここで通すと**旗の意味が失われる**。
    """
    graph = _render(
        _event(action="published", subject=f"{_NS}@2.0.0"),
        versions=(_version(version="2.0.0", edited_from="1.0.0", edited_from_recorded=False),),
    )
    assert (_REVISION, ONT.editedFromRecorded, Literal(False)) in graph
    assert not list(graph.triples((_REVISION, PROV.wasDerivedFrom, None)))


def test_版の行が引けなければ旗すら出さない() -> None:
    """**3 段目の「分からない」**(ADR-0027 決定5)。

    `false` を出すと「行を見て、記録されていなかった」と読めるが、実際には
    **行を見られなかった**。`audit_events` には名前空間への外部キーが無いので、
    名前空間を削除して同名で作り直すと版の行が無い監査イベントが残りうる。
    """
    graph = _render(_event(action="published", subject=f"{_NS}@2.0.0"), versions=())
    assert (_REVISION, RDF.type, PROV.Entity) in graph
    assert not list(graph.triples((_REVISION, ONT.editedFromRecorded, None)))
    assert not list(graph.triples((_REVISION, PROV.wasDerivedFrom, None)))


def test_他の名前空間の版の行は系譜に使わない() -> None:
    """名前空間は隔離の境界である(不変条件5)。版の突き合わせも内側で行う。"""
    graph = _render(
        _event(action="published", subject=f"{_NS}@2.0.0"),
        versions=(
            _version(
                version="2.0.0",
                edited_from="1.0.0",
                edited_from_recorded=True,
                namespace="other-ns",
            ),
        ),
    )
    assert not list(graph.triples((_REVISION, ONT.editedFromRecorded, None)))
    assert not list(graph.triples((_REVISION, PROV.wasDerivedFrom, None)))


def test_系譜を出しても_wasRevisionOf_は出さない() -> None:
    """**`wasDerivedFrom` より強い主張はしない**(ADR-0027 決定5)。

    記録しているのは「この版を編集するとき基準にした版」であって、
    両者が改訂の関係にあるとまでは言えない。
    """
    graph = _render(
        _event(action="published", subject=f"{_NS}@2.0.0"),
        versions=(_version(version="2.0.0", edited_from="1.0.0", edited_from_recorded=True),),
    )
    assert not list(graph.triples((None, PROV.wasRevisionOf, None)))


# ------------------------------------------------ 参照されている版の抜き出し


def test_参照されている版だけを抜き出す() -> None:
    """**名前空間の全版を引かないため**(ADR-0027 決定6)。"""
    found = referenced_versions(
        (
            _event(id=1, action="published", subject=f"{_NS}@1.0.0"),
            _event(id=2, action="approved", subject=f"{_NS}@1.0.0"),
            _event(id=3, action="submitted", subject=f"{_NS}@2.0.0"),
        ),
        namespace=_NS,
    )
    assert found == {"1.0.0", "2.0.0"}


@pytest.mark.parametrize(
    ("action", "subject"),
    [
        ("questions-revised", f"{_NS}#questions@3"),
        ("mapping-declared", "https://a/#X skos:closeMatch https://b/#Y"),
        ("access-log-purged", f"{_NS}/access-log"),
        ("published", "other-ns@1.0.0"),
        ("published", f"{_NS}@../../etc"),
        # **対応表に無い行為は、対象が版の形でも拾わない。**
        # `render_provenance` は `VERSION_ACTIONS` 以外に `prov:Entity` を
        # 作らないので、ここで拾うと**使われない版の行を引くだけ**になる。
        # 2 か所の門が同じ集合を見ていることをここで固定する。
        ("frobnicated", f"{_NS}@2.0.0"),
    ],
)
def test_版でない対象は抜き出さない(action: str, subject: str) -> None:
    """**`questions-revised` の対象も `@` を含む。**

    素朴に `@` で分けると改訂番号が版として引かれる。解析規則を
    `render_provenance` と共有しているので、ここがずれると
    「実体は出るが系譜が出ない」という静かな不整合になる。
    """
    assert referenced_versions((_event(action=action, subject=subject),), namespace=_NS) == set()


def test_イベントが無ければ空集合() -> None:
    assert referenced_versions((), namespace=_NS) == set()
