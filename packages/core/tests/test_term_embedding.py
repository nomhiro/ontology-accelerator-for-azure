"""用語の埋め込みの材料づくりと、埋め込みの呼び出し(ADR-0050、`P3-02`)。

ここで固定するのは 6 つである。

1. **埋め込むのはクラスとプロパティだけ。** `owl:Ontology` と `sh:NodeShape` は
   用語ではない
2. **言語タグで絞らない**(決定7)。日本語と英語のラベルを両方つなぐ —
   片方だけ埋め込むと**もう片方の言葉で聞いたときに出てこない**
3. **切り詰めたことを見せる。** 黙って切ると「説明を入れたのに検索に
   出ない」が起きる
4. **廃止済みの用語も埋め込む。** 除外すると「なぜ使えないのか」に答えられない
5. **解析できない TTL を「用語 0 件」にしない。** `ValueError` で断る
6. **応答は `index` で並べ直す。** 応答の順序に頼ると、**用語と埋め込みが
   入れ替わっても気づけない**
"""

from __future__ import annotations

import httpx
import pytest

from ontology_core.embedding import (
    EMBEDDING_DIMENSIONS,
    MAX_SOURCE_TEXT_CHARS,
    EmbeddingClient,
    EmbeddingError,
    build_source_text,
    local_name,
    term_texts,
)

_PREFIXES = (
    "@prefix v: <https://e.example/v#> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n"
    "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
)


def _vector(seed: float = 0.5) -> list[float]:
    return [seed] * EMBEDDING_DIMENSIONS


def test_局所名を取り出す() -> None:
    assert local_name("https://e.example/v#Customer") == "Customer"
    assert local_name("https://e.example/v/Customer") == "Customer"
    # 取れない形は IRI そのものを返す(**空文字列にしない**)。
    assert local_name("urn:") == "urn:"


def test_局所名を材料の先頭に置く() -> None:
    """**ラベルが無い用語でも当てられるようにする。**

    実際にラベルを付けていないオントロジーはある。そのとき材料が空になると
    検索に一切出てこない。
    """
    text, truncated = build_source_text(
        iri="https://e.example/v#GoldCustomer", labels=(), comments=()
    )
    assert text == "GoldCustomer"
    assert truncated is False


def test_日本語と英語のラベルを両方つなぐ() -> None:
    """**言語タグで絞らない**(決定7)。

    `ja` だけを埋め込むと「customer」で聞いたときに出てこない。
    """
    text, _ = build_source_text(
        iri="https://e.example/v#Customer",
        labels=("顧客", "Customer"),
        comments=("サービスを利用する主体",),
    )
    assert "顧客" in text
    assert "Customer" in text
    assert "サービスを利用する主体" in text


def test_上限で切り詰めたことを見せる() -> None:
    """**黙って切らない。**"""
    text, truncated = build_source_text(
        iri="https://e.example/v#Long", labels=(), comments=("あ" * (MAX_SOURCE_TEXT_CHARS + 10),)
    )
    assert truncated is True
    assert len(text) == MAX_SOURCE_TEXT_CHARS


def test_ちょうど上限なら切り詰めない() -> None:
    """**境界で 1 文字ずれていないことを固定する。**"""
    padding = MAX_SOURCE_TEXT_CHARS - len("X") - len(" / ")
    text, truncated = build_source_text(
        iri="https://e.example/v#X", labels=("あ" * padding,), comments=()
    )
    assert len(text) == MAX_SOURCE_TEXT_CHARS
    assert truncated is False


def test_クラスとプロパティを取り出す() -> None:
    turtle = (
        _PREFIXES
        + 'v:Customer a owl:Class ; rdfs:label "顧客"@ja , "Customer"@en ;\n'
        + '    rdfs:comment "サービスを利用する主体" .\n'
        + "v:hasOrder a owl:ObjectProperty .\n"
        + "v:customerId a owl:DatatypeProperty .\n"
        + "v:note a owl:AnnotationProperty .\n"
    )
    texts = term_texts(turtle)
    assert {item.iri for item in texts} == {
        "https://e.example/v#Customer",
        "https://e.example/v#hasOrder",
        "https://e.example/v#customerId",
        "https://e.example/v#note",
    }


def test_オントロジーそのものと形は埋め込まない() -> None:
    """**`owl:Ontology` は版の記述、`sh:NodeShape` は制約である。**

    検索で当てたいのは用語である。入れると「何を検索したのか」が曖昧になる。
    """
    turtle = (
        _PREFIXES
        + "<https://e.example/v> a owl:Ontology .\n"
        + "v:CustomerShape a sh:NodeShape ; sh:targetClass v:Customer .\n"
        + "v:Customer a owl:Class .\n"
    )
    assert {item.iri for item in term_texts(turtle)} == {"https://e.example/v#Customer"}


def test_廃止済みの用語も埋め込んで印を付ける() -> None:
    """**除外しない**(ADR-0017 決定3 と同じ向き)。"""
    turtle = _PREFIXES + "v:Legacy a owl:Class ; owl:deprecated true .\nv:New a owl:Class .\n"
    states = {item.iri: item.deprecated for item in term_texts(turtle)}
    assert states == {
        "https://e.example/v#Legacy": True,
        "https://e.example/v#New": False,
    }


def test_空白ノードの用語は飛ばす() -> None:
    """IRI を持たないので検索の結果にできない。"""
    turtle = _PREFIXES + "[] a owl:Class .\nv:Real a owl:Class .\n"
    assert {item.iri for item in term_texts(turtle)} == {"https://e.example/v#Real"}


def test_同じ用語を二重に出さない() -> None:
    """クラスとプロパティの両方で宣言された用語(実際にある)。"""
    turtle = _PREFIXES + "v:Odd a owl:Class , owl:DatatypeProperty .\n"
    assert len(term_texts(turtle)) == 1


def test_並び順が決定的である() -> None:
    """**同じ TTL から同じ順序が出る。** 作り直しの判定に使えるようにする。"""
    turtle = _PREFIXES + "v:B a owl:Class .\nv:A a owl:Class .\nv:C a owl:Class .\n"
    assert [item.iri for item in term_texts(turtle)] == [
        "https://e.example/v#A",
        "https://e.example/v#B",
        "https://e.example/v#C",
    ]


def test_ラベルの並び順も決定的である() -> None:
    """rdflib のトリプルの並びは保証されないので、材料側で揃える。"""
    turtle = _PREFIXES + 'v:X a owl:Class ; rdfs:label "zzz" , "aaa" , "mmm" .\n'
    assert term_texts(turtle)[0].source_text == "X / aaa / mmm / zzz"


def test_skos_の語彙も材料に入れる() -> None:
    """`prefLabel` / `definition` を使うオントロジーがある。"""
    turtle = _PREFIXES + 'v:X a owl:Class ; skos:prefLabel "優良顧客" ; skos:definition "定義" .\n'
    text = term_texts(turtle)[0].source_text
    assert "優良顧客" in text
    assert "定義" in text


def test_解析できない_TTL_は用語_0_件にしない() -> None:
    """**「読めなかった」を「用語が無かった」にしない。**"""
    with pytest.raises(ValueError, match="解析できません"):
        term_texts("これは Turtle ではない {{{")


def test_用語が無い_TTL_は空のタプルを返す() -> None:
    """**解析できた上で 0 件なのは事実である。** 例外にしない。"""
    assert term_texts("@prefix v: <https://e.example/v#> .\n") == ()


async def test_埋め込みの応答を_index_で並べ直す() -> None:
    """**応答の順序に頼らない。**

    順序に頼ると、**用語と埋め込みが入れ替わっても気づけない**。
    応答を逆順で返す相手に対して、入力の順序で返ることを固定する。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": _vector(0.2)},
                    {"index": 0, "embedding": _vector(0.1)},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = _client(http)
        vectors = await client.embed(("一つ目", "二つ目"))
    assert vectors[0][0] == pytest.approx(0.1)
    assert vectors[1][0] == pytest.approx(0.2)


async def test_index_が重複したら断る() -> None:
    """**黙って詰めない。** 詰めると 1 件が 2 回埋め込まれた結果になる。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": _vector(0.1)},
                    {"index": 0, "embedding": _vector(0.2)},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError, match="欠けている index"):
            await _client(http).embed(("a", "b"))


async def test_次元が違ったら断る() -> None:
    """**列の次元と合わないものを保存しない。**

    保存できてしまうと、検索の時点で初めて落ちる(原因が遠くなる)。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError, match=str(EMBEDDING_DIMENSIONS)):
            await _client(http).embed(("a",))


async def test_件数が合わなかったら断る() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": _vector()}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError, match="件数が合いません"):
            await _client(http).embed(("a", "b"))


async def test_失敗の応答の本文を載せない() -> None:
    """**入力に用語のラベルと説明が入っている**(ADR-0043 決定10 と同じ)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "顧客の秘密の説明文"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError) as caught:
            await _client(http).embed(("a",))
    assert "顧客の秘密の説明文" not in str(caught.value)
    assert "400" in str(caught.value)


async def test_上限を超える入力を断る() -> None:
    """**上界が無いものに上限を置く。** 呼び出し側が分割する。"""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 到達しない
        raise AssertionError("上限の検査より先に呼び出してはいけない")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError, match="までです"):
            await _client(http).embed(tuple(f"t{i}" for i in range(EmbeddingClient.MAX_BATCH + 1)))


async def test_空の入力を断る() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as h:
        with pytest.raises(EmbeddingError, match="空です"):
            await _client(h).embed(())


async def test_JSON_でない応答を断る() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError, match="オブジェクトではありません"):
            await _client(http).embed(("a",))


async def test_呼び出し先の_URL_と_api_version() -> None:
    """**デプロイ名で呼ぶ**(モデル名ではない)。"""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": _vector()}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await _client(http).embed(("a",))
    assert "/openai/deployments/ontology-embedder/embeddings" in seen["url"]
    assert "api-version=2024-10-21" in seen["url"]
    # **キーではなくベアラトークンで呼ぶ**(ADR-0043 決定9 と同じ)。
    assert seen["auth"].startswith("Bearer ")


def _client(http: httpx.AsyncClient) -> EmbeddingClient:
    """トークンの取得だけを差し替えたクライアント。

    **`DefaultAzureCredential` をテストで呼ばない。** 実テナントへ
    読み取りが飛ぶ(`setup-app-role.py` で一度踏んだ形)。
    """

    class _Client(EmbeddingClient):
        def _bearer(self) -> str:
            return "test-bearer"

    return _Client(
        endpoint="https://example.openai.azure.com/",
        deployment="ontology-embedder",
        api_version="2024-10-21",
        client=http,
    )


async def test_渡されたクライアントは閉じない() -> None:
    """**呼び出し側の持ち物である**(`VirtualGraphClient.aclose` と同じ約束)。

    閉じてしまうと、FastAPI の依存が使い回しているクライアントを
    1 回の検索で壊す。
    """
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as h:
        await _client(h).aclose()
        assert h.is_closed is False


async def test_自前で作ったクライアントは閉じる() -> None:
    """**作り直しの経路は 64 件ずつ何十回も呼ぶ。** 開いたままにしない。"""
    client = EmbeddingClient(
        endpoint="https://example.openai.azure.com/",
        deployment="ontology-embedder",
        api_version="2024-10-21",
    )
    inner = client._client  # 生成したものを閉じたか確かめる
    await client.aclose()
    assert inner.is_closed is True
