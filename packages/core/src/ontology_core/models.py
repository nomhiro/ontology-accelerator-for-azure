"""ドメインモデル。

正本(PostgreSQL / Blob)に保存する概念をここで定義する。
永続化のためのテーブル定義は Phase 1 で `ontology_api` 側に追加する。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class NamespaceRole(StrEnum):
    """名前空間ごとに割り当てるロール。

    Entra ID のアプリロールは粗粒度(プラットフォーム管理者か一般利用者か)に留め、
    名前空間との組み合わせは正本の PostgreSQL で管理して API 層で強制する。
    """

    OWNER = "owner"
    MAINTAINER = "maintainer"
    DATA_STEWARD = "data-steward"
    DATA_ANALYST = "data-analyst"

    @property
    def rank(self) -> int:
        """順序。**上位は下位のすべてを含む**(ADR-0014 決定2)。

        ロールを増やすほど「誰が何をできるか」を人が把握できなくなるため、
        4 段の順序付きに保つ。順序があれば「上位は下位を含む」の一言で
        説明できる。
        """
        return _NAMESPACE_ROLE_RANK[self]

    def covers(self, required: NamespaceRole) -> bool:
        """このロールが `required` を満たすか。"""
        return self.rank >= required.rank


_NAMESPACE_ROLE_RANK: dict[NamespaceRole, int] = {
    NamespaceRole.DATA_ANALYST: 0,
    NamespaceRole.DATA_STEWARD: 1,
    NamespaceRole.MAINTAINER: 2,
    NamespaceRole.OWNER: 3,
}


class PlatformRole(StrEnum):
    """テナント全体に対するロール。"""

    PLATFORM_ADMIN = "platform-admin"
    PLATFORM_VIEWER = "platform-viewer"


class OntologyVersionStatus(StrEnum):
    """オントロジーのバージョンの状態。"""

    DRAFT = "draft"
    IN_REVIEW = "in-review"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class NamespaceRoleAssignment(BaseModel):
    """名前空間へのロール付与(ADR-0014)。"""

    model_config = ConfigDict(frozen=True)

    namespace: str
    principal_id: str = Field(description="Entra のオブジェクト ID")
    role: NamespaceRole
    granted_at: datetime
    granted_by: str


class TermOwner(BaseModel):
    """用語単位の責任者(ADR-0015 決定1)。

    **`namespace_roles` の `owner` ロールとは別の概念である。** ロールは
    「何ができるか」(権限)、責任者は「誰が説明責任を負うか」である。
    名前空間単位の責任者は `owner` ロールが担う(ADR-0014)。用語単位こそが、
    権限では表現できない粒度である。
    """

    model_config = ConfigDict(frozen=True)

    namespace: str
    term_iri: str = Field(description="責任の対象となる用語の絶対 IRI")
    principal_id: str = Field(description="Entra のオブジェクト ID")
    assigned_at: datetime
    assigned_by: str


class OwnerResolutionSource(StrEnum):
    """問い合わせ先をどこから解決したか(ADR-0015 決定2)。

    **代替で解決したことを必ず見せる。** 見えなければ `P2B-06` の
    健全性指標が「責任者が未設定の用語」を数えられない。
    """

    TERM_OWNER = "term-owner"
    NAMESPACE_OWNERS = "namespace-owners"
    UNRESOLVED = "unresolved"


class OwnerResolution(BaseModel):
    """「この用語は誰に聞けばよいか」の答え(ADR-0015 決定2)。"""

    model_config = ConfigDict(frozen=True)

    namespace: str
    term_iri: str
    source: OwnerResolutionSource
    principal_ids: tuple[str, ...] = Field(
        default=(),
        description="問い合わせ先の Entra オブジェクト ID。"
        "`term-owner` なら 1 件、`namespace-owners` なら 1 件以上、"
        "`unresolved` なら 0 件",
    )


class AccessEvent(BaseModel):
    """コンテキストのアクセスログの 1 件(ADR-0018 決定1)。

    **`AuditEvent` とは別物である。** あちらは人の決定(誰が承認したか)、
    こちらは機械の参照(エージェントに何を渡したか)を記録する。ADR-0006 は
    後者を「監査の最後のピース」と呼んでいる — オントロジーの履歴が完全でも、
    実際に何が渡ったかが分からなければ判断の説明は完結しない。
    """

    model_config = ConfigDict(frozen=True)

    namespace: str
    actor: str = Field(description="Entra のオブジェクト ID")
    occurred_at: datetime
    query_text: str = Field(description="クエリ本文(長い場合は切り詰められる)")
    query_hash: str = Field(description="**全文**の SHA-256。同じクエリをまとめるのに使う")
    query_truncated: bool = Field(description="`query_text` が切り詰められているか")
    default_graph_version: str | None = Field(
        default=None,
        description="既定グラフの版(承認済みの現行版)。"
        "**`used_graph_clause` が真なら、これは読んだ版の全体ではない**",
    )
    used_graph_clause: bool = Field(
        description="クエリが `GRAPH` 句を含むか。真なら版の記録が不完全である"
    )
    returned_row_count: int
    returned_term_count: int = Field(
        description="返した用語のうち、その名前空間が発行した IRI の数"
    )


class AccessPage(BaseModel):
    """アクセスログの 1 ページ。`AuditPage` と同じ形。"""

    model_config = ConfigDict(frozen=True)

    events: tuple[AccessEvent, ...]
    next_cursor: int | None = Field(
        default=None,
        description="次のページを取るときに `cursor` へ渡す値。`None` なら最後のページ",
    )


class TermAccess(BaseModel):
    """用語ごとの参照の集約(ADR-0018 決定1)。

    **イベントより長生きする。** 保持期間を過ぎたイベントを消しても
    「最後にいつ参照されたか」は残らなければならない。
    """

    model_config = ConfigDict(frozen=True)

    namespace: str
    term_iri: str
    last_accessed_at: datetime
    access_count: int


class Namespace(BaseModel):
    """オントロジーを隔離する単位。

    Fuseki 側では名前空間ごとに独立したデータセットを割り当てる。任意の SPARQL を
    名前付きグラフへ書き換えて閉じ込める実装は `GRAPH` / `SERVICE` 句で回避され得るため
    採らない(`docs/adr/0001-rdf-store-selection.md`)。
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$", description="スラッグ形式の識別子")
    display_name: str
    description: str = ""
    base_iri: str = Field(description="このオントロジーが発行する IRI の接頭辞")
    created_at: datetime
    created_by: str
    # 四眼原則(ADR-0014 決定4)。既定は有効。
    require_two_person_approval: bool = True


class OntologyVersion(BaseModel):
    """不変のリビジョン。

    承認済みの成果物は Blob 上のバージョン付き TTL が正本であり、トリプルストアには
    バージョンごとの名前付きグラフとして射影する。エージェントはバージョンを固定して
    参照できる。
    """

    model_config = ConfigDict(frozen=True)

    namespace: str
    version: str = Field(description="semver。例: 1.4.0")
    content_hash: str = Field(description="TTL 本体の SHA-256。同一性の判定に使う")
    status: OntologyVersionStatus
    graph_iri: str = Field(description="射影先の名前付きグラフ IRI")
    blob_path: str = Field(description="正本 TTL の Blob 上のパス")
    created_at: datetime
    created_by: str
    approved_at: datetime | None = None
    approved_by: str | None = None
    # 射影(Fuseki への反映)が完了した時刻。NULL なら未射影で reconcile の対象。
    projected_at: datetime | None = None
    # 何から編集したか(ADR-0027、`P2A-15`)。**2 つの欄で 3 状態を表す。**
    #
    # | `edited_from_recorded` | `edited_from` | 意味 |
    # |---|---|---|
    # | `False` | `None` | **分からない**(宣言されなかった) |
    # | `True` | `None` | この名前空間に先行する版が無かった(最初の版) |
    # | `True` | `"1.0.0"` | 1.0.0 から編集した |
    edited_from: str | None = Field(
        default=None,
        description="編集の基準にした版。`edited_from_recorded` が false のときは常に null "
        "(「分からない」の意味であり、「派生していない」ではない)",
    )
    edited_from_recorded: bool = Field(
        default=False,
        description="系譜が記録されているか。**false は「分からない」である。** "
        "true かつ `edited_from` が null なら「この名前空間に先行する版が無かった」",
    )


class AuditEvent(BaseModel):
    """監査証跡の 1 件。

    「誰が・いつ・何を・なぜ」を記録する。表現には W3C PROV-O を用いて
    W3C 標準忠実の方針と揃える(`docs/adr/0006-ontology-versioning-and-audit.md`)。
    PROV-O への写しは `ontology_core.prov`(ADR-0026)。
    """

    model_config = ConfigDict(frozen=True)

    id: int = Field(
        description="追記専用テーブルの単調増加する主キー。"
        "並び順とページングの鍵であり(`occurred_at` は `now()` = "
        "トランザクション開始時刻なので同時刻が並ぶ)、PROV-O の "
        "`prov:Activity` の IRI にもこの値を使う"
    )
    namespace: str
    action: str = Field(description="proposed / approved / rejected / published など")
    actor: str = Field(description="Entra ID のオブジェクト ID もしくはサービスプリンシパル")
    occurred_at: datetime
    subject: str = Field(description="対象。オントロジーのバージョンやマッピングの識別子")
    reason: str = ""
    diff: str | None = Field(default=None, description="前バージョンとの差分")


class AuditPage(BaseModel):
    """監査照会の 1 ページ(`P2B-11`)。

    **`next_cursor` が `None` なら、それが最後のページである。** 「返った件数が
    `limit` より少ないから最後」という判定に頼らせない — 境界ちょうどのときに
    余分な 1 往復が要るだけでなく、**呼び出し側が判定を間違える**。
    """

    model_config = ConfigDict(frozen=True)

    events: tuple[AuditEvent, ...]
    next_cursor: int | None = Field(
        default=None,
        description="次のページを取るときに `cursor` へ渡す値。`None` なら最後のページ",
    )


class QuestionSetSummary(BaseModel):
    """想定質問の集合の 1 改訂(本文を含まない)(ADR-0022 決定1)。

    **本文を含まないのは、改訂の一覧が肥大しないようにするため**である。
    本文は `GET /namespaces/{ns}/questions` で有効な改訂だけを返す。
    """

    model_config = ConfigDict(frozen=True)

    namespace: str
    revision: int = Field(description="名前空間ごとに 1 から増える連番。有効なのは最大の改訂")
    content_hash: str = Field(description="本文の SHA-256")
    question_count: int
    created_at: datetime
    created_by: str
    reason: str = Field(description="この改訂を入れた理由。**必須である**(ADR-0022 決定6)")


class QuestionSet(BaseModel):
    """想定質問の集合の 1 改訂(本文を含む)。"""

    model_config = ConfigDict(frozen=True)

    namespace: str
    revision: int
    content: str = Field(description="質問ファイル(YAML)の本文")
    content_hash: str
    question_count: int
    created_at: datetime
    created_by: str
    reason: str

    def summary(self) -> QuestionSetSummary:
        return QuestionSetSummary(
            namespace=self.namespace,
            revision=self.revision,
            content_hash=self.content_hash,
            question_count=self.question_count,
            created_at=self.created_at,
            created_by=self.created_by,
            reason=self.reason,
        )


class CompetencyQuestionOutcome(BaseModel):
    """想定質問 1 件の評価結果。"""

    model_config = ConfigDict(frozen=True)

    id: str
    question: str
    expect: str
    passed: bool
    detail: str = Field(description="判定の根拠。行数は数えていないので「1 行以上」と出る")


class CompetencyRunReport(BaseModel):
    """ある版に対する想定質問の評価結果(ADR-0022 決定9)。

    **`conforms` は「全件を評価できて、かつ全件が通った」ときだけ真である。**
    評価していない質問があるときに真を返してはいけない(ADR-0022 決定5)。
    """

    model_config = ConfigDict(frozen=True)

    namespace: str
    version: str
    revision: int | None = Field(
        default=None,
        description="評価に使った質問集合の改訂。**`None` は「質問集合が無い」**"
        "(基準を定めていない。基準を満たしていないのではない)",
    )
    conforms: bool
    results: tuple[CompetencyQuestionOutcome, ...] = ()
    not_evaluated: tuple[str, ...] = Field(
        default=(),
        description="予算を超えて評価しなかった質問の id。**空でなければ「確かめられなかった」**",
    )
    elapsed_seconds: float = 0.0
    criteria_self_revised: str | None = Field(
        default=None,
        description="**審査される側が基準を書き換えている**ことの説明"
        "(ADR-0029 決定2、不変条件14)。`null` ならその事実は無い。"
        "四眼原則が有効な名前空間では `approve` が 422 で止まる。"
        "**無効な名前空間でもこの欄は埋まる** — 止まらないが、"
        "何が起きたかは見えるべきである(決定6)",
    )


class TermMapping(BaseModel):
    """領域間マッピングの 1 件(ADR-0023)。

    **`reciprocal` と `disputed` を必ず見ること。**

    - `reciprocal=False` は**異常ではない**。相手がまだ宣言していないだけで、
      初期状態では片側だけが正常である(ADR-0023 決定3)
    - `disputed=True` は相互に宣言されていて**述語が食い違っている**。
      どちらも消さない(決定4) — 領域をまたぐ相違を消さないのが
      この機能の目的である
    - `target_status` は**終点の生死**である(ADR-0030)。`deprecated` なら
      `target_successor` に張り替え先が入る。**`unknown` は「調べていない・
      調べられなかった」であって「問題なし」ではない** — 理由は
      `target_status_note` に入る
    """

    model_config = ConfigDict(frozen=True)

    namespace: str = Field(description="マッピングを**張った側**の名前空間")
    source_term: str = Field(description="始点の用語の絶対 IRI")
    target_term: str = Field(description="終点の用語の絶対 IRI。外部語彙でもよい")
    predicate: str = Field(description="SKOS のマッピング述語(`exactMatch` 等)")
    predicate_iri: str = Field(description="述語の絶対 IRI")
    reason: str = Field(description="なぜ同じ(近い)と言えるのか。**必須**")
    declared_by: str
    declared_at: datetime
    reciprocal: bool = Field(
        default=False, description="相手側も同じ用語ペアを宣言しているか。**偽は異常ではない**"
    )
    disputed: bool = Field(
        default=False,
        description="相互に宣言されていて述語が食い違っているか。**どちらも消さない**",
    )
    counterpart_predicate: str | None = Field(
        default=None, description="相手側が宣言している述語。無ければ `None`"
    )
    # マッピングの先の生死(ADR-0030、`P2B-18`)。既定は `unknown` である —
    # **「調べていない」が既定であり、「生きている」ではない。**
    target_status: str = Field(
        default="unknown",
        description="終点の用語の生死。`deprecated` / `active` / `absent` / `unknown`。"
        "**`unknown` は「調べていない・調べられなかった」であって"
        "「問題なし」ではない**(ADR-0030 決定2)。`absent` は"
        "「相手の現行版にその IRI が無い」= 何も指していないマッピングである",
    )
    target_successor: str | None = Field(
        default=None,
        description="終点が廃止済みのとき、その後継(`dcterms:isReplacedBy`)。"
        "**マッピングを張り替える先である**",
    )
    target_status_note: str = Field(
        default="",
        description="`target_status` が `unknown` のときの理由。"
        "外部語彙 / 権限が無い / 承認済みの版が無い / 正本を読めなかった / 上限。"
        "**理由の無い `unknown` は「問題なし」と読まれるので必ず入る**",
    )
