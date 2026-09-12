"""モデルの呼び出しと検証ループ(ADR-0043、`P2A-02`)。

**実物のモデルはローカルに無い。** だから「検証できないから確かめない」に
しないために、**HTTP の形を `httpx.MockTransport` で固定する**。

ここで固定するのは 5 つである。

1. **`tools` を渡さない**(決定2b)。モデルに副作用を作れる口を与えない
2. **検証に落ちたら理由を添えて再試行する**(決定4)。理由は言い換えない
3. **上限に達したら断る。** 部分的に正しい Turtle を返さない
4. **何回目で通ったかを持ち帰る**(決定4)
5. **応答の本文を例外に載せない。** プロンプトにはカタログが入っている
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from ontology_api.services.proposal import (
    ModelUnavailableError,
    ProposalService,
    ProposalUnusableError,
)
from ontology_core.config import AuthMode, Settings
from ontology_core.models import ScanColumn, ScanTable

_BASE = "https://example.com/ontology/retail#"
_ENDPOINT = "https://oai-test.openai.azure.com"
_FAKE_BEARER = "fake-bearer-value"

_GOOD_TURTLE = f"""\
@prefix ex: <{_BASE}> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Product a owl:Class ;
    rdfs:label "商品" .
"""

_FOREIGN_TURTLE = """\
@prefix other: <https://example.com/ontology/finance#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .

other:Invoice a owl:Class .
"""


class _FakeAccess:
    def __init__(self) -> None:
        self.token = _FAKE_BEARER


class _FakeCredential:
    def get_token(self, *scopes: str, **kwargs: Any) -> _FakeAccess:
        return _FakeAccess()


def _settings(**kwargs: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "AUTH_MODE": AuthMode.DISABLED,
        "MODEL_ENDPOINT": _ENDPOINT,
        "MODEL_DEPLOYMENT": "ontology-proposer",
        "MODEL_NAME": "gpt-4.1",
    }
    return Settings(**{**base, **kwargs})


def _tables() -> list[ScanTable]:
    return [
        ScanTable(
            schema_name="public",
            table_name="products",
            kind="table",
            estimated_rows=10,
            columns=(
                ScanColumn(
                    column_name="id",
                    ordinal_position=1,
                    data_type="integer",
                    is_nullable=False,
                    is_primary_key=True,
                ),
            ),
        )
    ]


def _reply(content: str, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


def _service(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs: Any,
) -> ProposalService:
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", _FakeCredential)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ProposalService(settings=_settings(**kwargs), client=client)


# ------------------------------------------ 呼び出しの形(決定2b・8)


async def test_モデルの呼び出しの形(monkeypatch: pytest.MonkeyPatch) -> None:
    """**実物のモデルはローカルに無い。** HTTP の形だけを固定する。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _reply(_GOOD_TURTLE)

    service = _service(monkeypatch, handler)
    result = await service.propose(namespace="retail", base_iri=_BASE, tables=_tables())

    assert result.turtle.strip() == _GOOD_TURTLE.strip()
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/openai/deployments/ontology-proposer/chat/completions"
    assert request.url.params["api-version"] == "2024-10-21"
    assert request.headers["Authorization"] == f"Bearer {_FAKE_BEARER}"


async def test_モデルにツールを渡さない(monkeypatch: pytest.MonkeyPatch) -> None:
    """**これが決定2b である。**

    プロンプトにはカタログのコメント(顧客 DB の持ち主が書いた管理外の
    テキスト)が入る。**副作用を作れる口を一切与えない。**

    `tools` を足すと、注入の被害が「変な提案 1 件」から「モデルが選んだ
    操作の実行」に変わる。
    """
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _reply(_GOOD_TURTLE)

    service = _service(monkeypatch, handler)
    await service.propose(namespace="retail", base_iri=_BASE, tables=_tables())

    body = seen[0]
    for banned in ("tools", "functions", "tool_choice", "function_call"):
        assert banned not in body, f"モデルに '{banned}' を渡している(ADR-0043 決定2b)"
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    # **決定的にしようとはする**(再現性は主張しない。決定10)。
    assert body["temperature"] == 0


# --------------------------------- 検証ループ(決定3・4)


async def test_検証に落ちたら理由を添えて再試行する(monkeypatch: pytest.MonkeyPatch) -> None:
    """**理由は言い換えない**(ADR-0043 決定4)。

    1 回目に `base_iri` の外へ用語を作らせ、2 回目で直る様子を見る。
    """
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompts.append(body["messages"][1]["content"])
        return _reply(_FOREIGN_TURTLE if len(prompts) == 1 else _GOOD_TURTLE)

    service = _service(monkeypatch, handler)
    result = await service.propose(namespace="retail", base_iri=_BASE, tables=_tables())

    assert result.attempts == 2, "**何回目で通ったかを持ち帰っていない**"
    assert len(prompts) == 2
    # 1 回目のプロンプトには feedback が無い。
    assert "rejected by automated validation" not in prompts[0]
    # 2 回目には検証の理由がそのまま入る。
    assert "rejected by automated validation" in prompts[1]
    assert "base_iri" in prompts[1]


async def test_上限に達したら断る(monkeypatch: pytest.MonkeyPatch) -> None:
    """**部分的に正しい Turtle を返さない**(ADR-0043 決定4)。"""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _reply(_FOREIGN_TURTLE)

    service = _service(monkeypatch, handler, PROPOSAL_MAX_ATTEMPTS=2)
    with pytest.raises(ProposalUnusableError) as exc:
        await service.propose(namespace="retail", base_iri=_BASE, tables=_tables())

    assert calls == 2, "上限回数を守っていない"
    assert exc.value.attempts == 2
    assert "base_iri" in exc.value.last_error, "最後の理由を捨てている"


async def test_1_回で通れば_1_回しか呼ばない(monkeypatch: pytest.MonkeyPatch) -> None:
    """**課金される呼び出しを無駄に増やさない。**"""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _reply(_GOOD_TURTLE)

    service = _service(monkeypatch, handler)
    result = await service.propose(namespace="retail", base_iri=_BASE, tables=_tables())
    assert calls == 1
    assert result.attempts == 1


async def test_コードフェンス付きでも通る(monkeypatch: pytest.MonkeyPatch) -> None:
    """**「Turtle だけを返せ」と指示しても付いてくることがある。**"""

    def handler(request: httpx.Request) -> httpx.Response:
        return _reply(f"```turtle\n{_GOOD_TURTLE.strip()}\n```")

    service = _service(monkeypatch, handler)
    result = await service.propose(namespace="retail", base_iri=_BASE, tables=_tables())
    assert result.turtle.startswith("@prefix")


# ------------------------------------- 届かなかったことを区別する


async def test_HTTP_が_200_でなければ本文を例外に載せない(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**プロンプトにはカタログが入っている。**

    応答にはその写しが混ざりうるので、状態コードだけを報告する。
    """
    leak = "kore-wa-katarogu-no-utsushi"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": leak}})

    service = _service(monkeypatch, handler)
    with pytest.raises(ModelUnavailableError) as exc:
        await service.propose(namespace="retail", base_iri=_BASE, tables=_tables())

    message = str(exc.value)
    assert leak not in message, "例外に応答の本文が載っている"
    assert "429" in message, "状態コードを捨てている"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "   "}}]},
    ],
)
async def test_応答の形が違えば届かなかったとして扱う(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    """**空文字を返さない**(ADR-0043 決定3 の手前)。

    空文字を返すと、この後の検証が「Turtle として解析できません」と言い、
    **モデルの設定の誤りが「モデルが変なことを言った」に見える**。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    service = _service(monkeypatch, handler)
    with pytest.raises(ModelUnavailableError):
        await service.propose(namespace="retail", base_iri=_BASE, tables=_tables())


async def test_設定が無ければ呼ばない(monkeypatch: pytest.MonkeyPatch) -> None:
    """**空なら候補の生成は使えない**(不変条件11 と同じ向き)。"""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _reply(_GOOD_TURTLE)

    service = _service(monkeypatch, handler, MODEL_ENDPOINT="", MODEL_DEPLOYMENT="")
    with pytest.raises(ModelUnavailableError, match="MODEL_ENDPOINT"):
        await service.propose(namespace="retail", base_iri=_BASE, tables=_tables())
    assert not called, "設定が無いのにモデルを呼んでいる"


# --------------------------------------------- 出自を記録する


async def test_出自に何が作ったかを書く(monkeypatch: pytest.MonkeyPatch) -> None:
    """**再現性は主張しない**(ADR-0043 決定10)。

    `temperature` を 0 にしても同じ出力になる保証はモデル側に無い。
    だから「再現できる」とは書かず、**何が作ったか**を書く。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return _reply(_GOOD_TURTLE)

    service = _service(monkeypatch, handler)
    result = await service.propose(namespace="retail", base_iri=_BASE, tables=_tables())
    provenance = result.provenance()

    assert "gpt-4.1" in provenance
    assert "ontology-proposer" in provenance
    assert "2024-10-21" in provenance
    assert "試行 1 回" in provenance
    # **人間のレビューを経ていないことを書く。**
    assert "レビューを経ていない" in provenance
    assert "再現" not in provenance, "再現性を主張している"
