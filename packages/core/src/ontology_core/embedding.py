"""用語の埋め込み(ADR-0050、`P3-02`)。

## 何を埋め込むのか

**承認済み版の TTL にある用語**である。クラスとプロパティを取り出し、
**IRI の局所名・ラベル・説明**をつないだ 1 本のテキストを作って埋め込む。

**文書は対象にしない。** Blob 文書の取り込み自体が未実装で、
取り込む前に検索の受け皿を作っても確かめようがない。

## なぜ Azure AI Search ではないのか

[ADR-0050](../../../../docs/adr/0050-vector-search-in-postgres.md) に全部
書いたが、要点は 3 つである。

1. **AI Search Basic は月 $97 の固定費で、スケールゼロが無い。** この製品は
   「minimal で評価でき、`azd down` で完全に消える」を掲げている
2. **Free tier はマネージド ID による Entra 認証に非対応**(実測)。
   使うと**API キー運用に戻る**
3. **用語は 1 名前空間あたり数百〜数千である。** 既に払っている
   PostgreSQL(B1ms、$23/月)に pgvector を載せれば +$0 で足りる

## 埋め込みは正本ではない

**再構築可能な射影である**(不変条件1 と同じ形)。正本は Blob の TTL で、
埋め込みはそこから作り直せる。だから

- **作成に失敗しても承認は止めない**(不変条件3 と同じ向き)
- **どの版・どのモデルから作ったかを行に持つ** — モデルを変えたときに
  作り直す判断ができる
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import TracebackType
from typing import Self

import httpx
from rdflib import Graph, URIRef
from rdflib.term import Literal

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "MAX_SOURCE_TEXT_CHARS",
    "EmbeddingClient",
    "EmbeddingError",
    "TermText",
    "build_source_text",
    "local_name",
    "term_texts",
]

#: 埋め込みの次元。**1536 に固定する**(ADR-0050 決定5)。
#:
#: **pgvector の `hnsw` 索引は 2000 次元までである**(実測。2001 以上で
#: `column cannot have more than 2000 dimensions for hnsw index`)。
#: `text-embedding-3-large`(3072)は `halfvec` へのキャストが要るので採らない。
#:
#: **Bicep も同じ値を持つ**(`infra/modules/model.bicep` の
#: `embeddingDimensions`)。食い違うと列の次元と埋め込みの次元が合わず、
#: **実行時まで分からない**。
EMBEDDING_DIMENSIONS = 1536

#: 1 つの用語について埋め込みに渡すテキストの上限。
#:
#: **切り詰めたことを呼び出し側に見せる**(`TermText.truncated`)。
#: 黙って切ると「説明を入れたのに検索に出ない」が起きる。
MAX_SOURCE_TEXT_CHARS = 4000

_OWL = "http://www.w3.org/2002/07/owl#"
_RDFS = "http://www.w3.org/2000/01/rdf-schema#"
_SKOS = "http://www.w3.org/2004/02/skos/core#"

_RDF_TYPE = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
_LABEL = URIRef(f"{_RDFS}label")
_COMMENT = URIRef(f"{_RDFS}comment")
_SKOS_DEFINITION = URIRef(f"{_SKOS}definition")
_SKOS_PREF_LABEL = URIRef(f"{_SKOS}prefLabel")
_DEPRECATED = URIRef(f"{_OWL}deprecated")

#: 埋め込みの対象にする型。
#:
#: **`owl:Ontology` は入れない** — 版そのものの記述であって用語ではない。
#: **`sh:NodeShape` も入れない** — 形は用語の制約であって語彙ではない
#: (検索で当てたいのは用語である)。
_TERM_TYPES = (
    URIRef(f"{_OWL}Class"),
    URIRef(f"{_OWL}ObjectProperty"),
    URIRef(f"{_OWL}DatatypeProperty"),
    URIRef(f"{_OWL}AnnotationProperty"),
    URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#Property"),
)

#: IRI の末尾(フラグメントか最後のパス要素)を取り出す。
_LOCAL_NAME = re.compile(r"[^#/]+$")


class EmbeddingError(RuntimeError):
    """埋め込みの生成が失敗したことを表す。

    **応答の本文を載せない**(ADR-0043 決定10 と同じ)。用語のラベルや説明が
    含まれうる。
    """


@dataclass(frozen=True)
class TermText:
    """1 つの用語について、埋め込みに渡すテキスト。

    Attributes:
        iri: 用語の IRI。
        source_text: 埋め込みに渡す文字列。**何から作ったかを保存する** —
            モデルを変えて作り直すときの根拠になる。
        truncated: 上限で切り詰めたか。**黙って切らないための印。**
        deprecated: 廃止済みか。**埋め込みは作る** — 廃止された用語を
            検索で見つけられないと「なぜ使えないのか」に答えられない。
            呼び出し側が結果に警告を添える。
    """

    iri: str
    source_text: str
    truncated: bool = False
    deprecated: bool = False


def local_name(iri: str) -> str:
    """IRI の局所名を返す。取れなければ IRI そのもの。

    **埋め込みのテキストに入れる。** ラベルが無い用語(実際にある)でも
    `GoldCustomer` のような名前で当てられるようにするため。
    """
    match = _LOCAL_NAME.search(iri)
    return match.group(0) if match else iri


def build_source_text(
    *, iri: str, labels: tuple[str, ...], comments: tuple[str, ...]
) -> tuple[str, bool]:
    """埋め込みに渡すテキストと、切り詰めたかを返す。

    **局所名を先頭に置く。** ラベルが無い用語でも当てられるようにする。

    **言語タグで絞らない**(ADR-0050 決定7)。日本語と英語のラベルが両方
    あるオントロジーで、片方だけを埋め込むと**もう片方の言葉で聞いたときに
    出てこない**。両方つなぐ。
    """
    parts = [local_name(iri), *labels, *comments]
    text = " / ".join(part.strip() for part in parts if part and part.strip())
    if len(text) <= MAX_SOURCE_TEXT_CHARS:
        return text, False
    return text[:MAX_SOURCE_TEXT_CHARS], True


def _literals(graph: Graph, subject: URIRef, *predicates: URIRef) -> tuple[str, ...]:
    """述語の値のうちリテラルを、重複を除いて並べる。

    **並び順を決定的にする。** 同じ TTL から同じテキストが出なければ、
    「埋め込みを作り直す必要があるか」を内容のハッシュで判定できない。
    """
    found: list[str] = []
    for predicate in predicates:
        for value in graph.objects(subject, predicate):
            if isinstance(value, Literal):
                text = str(value)
                if text not in found:
                    found.append(text)
    return tuple(sorted(found))


def term_texts(turtle: str) -> tuple[TermText, ...]:
    """承認済み版の TTL から、埋め込みの対象になる用語を取り出す。

    Raises:
        ValueError: Turtle として解析できないとき。**空のタプルを返さない** —
            「用語が無かった」と「読めなかった」を混ぜない
            (`ontology_core.divergence.shape_targets` と同じ判断)。
    """
    graph = Graph()
    try:
        graph.parse(data=turtle, format="turtle")
    except Exception as exc:  # rdflib は解析の失敗を複数の型で投げる
        raise ValueError(f"Turtle として解析できません: {exc}") from exc

    deprecated = {
        str(subject)
        for subject in graph.subjects(_DEPRECATED, Literal(True))
        if isinstance(subject, URIRef)
    }

    seen: set[str] = set()
    results: list[TermText] = []
    for term_type in _TERM_TYPES:
        for subject in graph.subjects(_RDF_TYPE, term_type):
            if not isinstance(subject, URIRef):
                # 空白ノードの用語は IRI を持たないので検索の結果にできない。
                continue
            iri = str(subject)
            if iri in seen:
                continue
            seen.add(iri)
            labels = _literals(graph, subject, _LABEL, _SKOS_PREF_LABEL)
            comments = _literals(graph, subject, _COMMENT, _SKOS_DEFINITION)
            text, truncated = build_source_text(iri=iri, labels=labels, comments=comments)
            results.append(
                TermText(
                    iri=iri,
                    source_text=text,
                    truncated=truncated,
                    deprecated=iri in deprecated,
                )
            )
    return tuple(sorted(results, key=lambda t: t.iri))


class EmbeddingClient:
    """Azure OpenAI の埋め込みを呼ぶ(ADR-0050 決定3)。

    **`azure_ai` 拡張(SQL の中から Azure OpenAI を呼ぶ)を使わない。**
    あれは Azure Database for PostgreSQL 固有で、**ローカルの docker では
    再現できない**。この製品は「ローカルで検証できることを増やす」を
    優先してきたので、埋め込みの生成はアプリ側に置く。

    **API キーを使わない**(ADR-0043 決定9 と同じ)。アカウント側で
    ローカル認証を無効にしてあるので、マネージド ID でしか呼べない。

    **HTTP クライアントを 1 本持って使い回す**(`VirtualGraphClient` と
    同じ形)。作り直しの経路は 64 件ずつ何十回も呼ぶので、**呼び出しごとに
    生成すると TLS の握りが毎回起きる**。

    Example:
        ```python
        async with EmbeddingClient(
            endpoint="https://x.openai.azure.com/",
            deployment="ontology-embedder",
            api_version="2024-10-21",
        ) as client:
            vectors = await client.embed(("顧客", "注文"))
        ```
    """

    #: 埋め込みのトークンを取るスコープ。チャット補完と同じである。
    SCOPE = "https://cognitiveservices.azure.com/.default"

    #: 1 回の呼び出しに渡す入力の数の上限。
    #:
    #: **上界が無いものに上限を置く。** 用語の数だけ入力が増えるので、
    #: 1 回の要求が大きくなりすぎる。呼び出し側が分割する。
    MAX_BATCH = 64

    def __init__(
        self,
        *,
        endpoint: str,
        deployment: str,
        api_version: str,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._deployment = deployment
        self._api_version = api_version
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """自前で生成した HTTP クライアントを閉じる。

        **渡されたクライアントは閉じない。** 呼び出し側の持ち物である
        (`VirtualGraphClient.aclose` と同じ約束)。
        """
        if self._owns_client:
            await self._client.aclose()

    async def embed(self, inputs: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """テキストを埋め込んで、入力と同じ順序で返す。

        **順序を保つことが契約である。** 返ってきた配列の `index` で並べ直す
        — 応答の順序に頼ると、**用語と埋め込みが入れ替わっても気づけない**。

        Raises:
            EmbeddingError: 入力が空、上限超え、呼び出しの失敗、
                次元の食い違い、または応答の形が想定と違うとき。
        """
        if not inputs:
            raise EmbeddingError("埋め込む入力が空です")
        if len(inputs) > self.MAX_BATCH:
            raise EmbeddingError(
                f"1 回に渡せる入力は {self.MAX_BATCH} 件までです(受領: {len(inputs)} 件)"
            )

        url = f"{self._endpoint}/openai/deployments/{self._deployment}/embeddings"
        payload = {"input": list(inputs)}
        headers = {"Authorization": f"Bearer {self._bearer()}"}
        params = {"api-version": self._api_version}

        response = await self._client.post(url, json=payload, headers=headers, params=params)

        if response.status_code != httpx.codes.OK:
            # **応答の本文を載せない。** 入力に用語のラベルと説明が入っている。
            raise EmbeddingError(
                f"埋め込みのデプロイ '{self._deployment}' の呼び出しに失敗しました"
                f"(HTTP {response.status_code})"
            )
        return _vectors_from(response.json(), expected=len(inputs))

    def _bearer(self) -> str:
        from azure.identity import DefaultAzureCredential

        return str(DefaultAzureCredential().get_token(self.SCOPE).token)


def _vectors_from(body: object, *, expected: int) -> tuple[tuple[float, ...], ...]:
    """埋め込みの応答を、入力の順序に並べ直して返す。

    **`index` で並べ直す**(応答の順序に頼らない)。

    Raises:
        EmbeddingError: 形が想定と違う、件数が合わない、`index` が欠けている、
            または次元が `EMBEDDING_DIMENSIONS` と違うとき。
    """
    if not isinstance(body, dict):
        raise EmbeddingError("埋め込みの応答が JSON のオブジェクトではありません")
    data = body.get("data")
    if not isinstance(data, list) or len(data) != expected:
        raise EmbeddingError(f"埋め込みの応答の件数が合いません(期待 {expected} 件)")

    slots: list[tuple[float, ...] | None] = [None] * expected
    for item in data:
        if not isinstance(item, dict):
            raise EmbeddingError("埋め込みの応答の要素がオブジェクトではありません")
        index = item.get("index")
        vector = item.get("embedding")
        if not isinstance(index, int) or not 0 <= index < expected:
            raise EmbeddingError("埋め込みの応答に妥当な index がありません")
        if not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSIONS:
            raise EmbeddingError(
                f"埋め込みの次元が {EMBEDDING_DIMENSIONS} ではありません"
                f"(受領: {len(vector) if isinstance(vector, list) else '不明'})"
            )
        slots[index] = tuple(float(value) for value in vector)

    if any(slot is None for slot in slots):
        # **同じ index が 2 回来た**ときにここへ来る。黙って詰めない。
        raise EmbeddingError("埋め込みの応答に欠けている index があります")
    return tuple(slot for slot in slots if slot is not None)
