"""カタログからオントロジー候補を生成する(ADR-0043、`P2A-02`)。

## モデルの出力は検証を通っても `draft` にしか入らない

**この製品の前提は「LLM の出力が人間の承認を経ずに正本へ入ることはない」**
である。これまでその前提は**LLM を呼ぶ経路が 1 つも無かったから**成り立って
いた。ここで初めて、前提を強制するコードになる(ADR-0043 決定5)。

このサービスは `submit` も `approve` も呼ばない。**呼び出し元も呼んでは
ならない。**

## モデルにツールを与えない

送るのは `messages` だけで、`tools` も `functions` も渡さない
(ADR-0043 決定2b)。**出力は Turtle の文字列としてしか扱わない。**
プロンプトにはカタログのコメント(顧客 DB の持ち主が書いた管理外のテキスト)が
入るので、**副作用を作れる口を一切与えない**。

## 検証に落ちたら理由を添えて再試行する

`PROPOSAL_MAX_ATTEMPTS` まで。上限に達したら**部分的に正しい Turtle を
返さずに断る**(決定4)。**何回目で通ったかを記録する** — 1 回で通った候補と
3 回目でやっと通った候補は、レビュアにとって別物である。

## REST を直接叩く

`openai` SDK を依存に足さない(ADR-0043 決定8。ADR-0041 決定4 と同じ判断)。
**HTTP の形はテストで固定する** — 実物のモデルはローカルに無いので、
「検証できないから確かめない」にしない。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ontology_core.config import Settings
from ontology_core.models import ScanTable
from ontology_core.proposal import (
    PROPOSAL_SYSTEM_PROMPT,
    ProposalValidationError,
    build_user_prompt,
    extract_turtle,
    validate_proposal,
)

__all__ = [
    "ModelUnavailableError",
    "ProposalResult",
    "ProposalService",
    "ProposalUnusableError",
]

logger = logging.getLogger(__name__)

#: Azure OpenAI の推論で使うトークンのスコープ。
_MODEL_SCOPE = "https://cognitiveservices.azure.com/.default"


class ModelUnavailableError(Exception):
    """モデルに届かなかった、または設定が無い。

    **「モデルが変なことを言った」とは区別する。** こちらは外部の依存に
    届かなかったことで、502 に対応する。
    """


class ProposalUnusableError(Exception):
    """上限回数まで試しても検証を通る候補が得られなかった(ADR-0043 決定4)。

    **部分的に正しい Turtle を返さない。** 最後の理由を持つ。
    """

    def __init__(self, message: str, *, attempts: int, last_error: str) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


@dataclass(frozen=True)
class ProposalResult:
    """検証を通った候補。

    Attributes:
        turtle: 検証を通った Turtle。
        attempts: **何回目で通ったか**(ADR-0043 決定4)。1 回で通った候補と
            3 回目でやっと通った候補は、レビュアにとって別物である。
        model: 使ったモデル名。
        deployment: 使ったデプロイ名。
        api_version: 使った API バージョン。
    """

    turtle: str
    attempts: int
    model: str
    deployment: str
    api_version: str

    def provenance(self) -> str:
        """`audit_events.reason` に書く出自(ADR-0043 決定10)。

        **再現性は主張しない。** `temperature` を 0 にしても同じ出力になる
        保証はモデル側に無い。だから「再現できる」とは書かず、
        **何が作ったか**を書く。
        """
        return (
            f"LLM による生成(model={self.model}、deployment={self.deployment}、"
            f"api-version={self.api_version}、試行 {self.attempts} 回)。"
            "**人間のレビューを経ていない候補である**"
        )


class ProposalService:
    """カタログから候補を生成する。

    **`httpx.AsyncClient` を引数で受け取れる**のはテストのためである
    (`MockTransport` を差し込む)。
    """

    def __init__(self, *, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    async def propose(
        self,
        *,
        namespace: str,
        base_iri: str,
        tables: list[ScanTable],
    ) -> ProposalResult:
        """候補を生成して返す。検証を通ったものしか返さない。

        Raises:
            ModelUnavailableError: 設定が無い、またはモデルに届かないとき。
            ProposalUnusableError: 上限回数まで検証を通らなかったとき。
        """
        settings = self._settings
        if not settings.model_endpoint or not settings.model_deployment:
            raise ModelUnavailableError(
                "MODEL_ENDPOINT と MODEL_DEPLOYMENT が設定されていないため、"
                "オントロジー候補を生成できません"
            )

        feedback: str | None = None
        last_error = ""
        for attempt in range(1, settings.proposal_max_attempts + 1):
            content = await self._complete(
                build_user_prompt(
                    namespace=namespace, base_iri=base_iri, tables=tables, feedback=feedback
                )
            )
            turtle = extract_turtle(content)
            try:
                validate_proposal(turtle, base_iri=base_iri)
            except ProposalValidationError as exc:
                last_error = str(exc)
                feedback = last_error
                logger.info(
                    "候補が検証に通りませんでした(%s/%s 回目): %s",
                    attempt,
                    settings.proposal_max_attempts,
                    last_error,
                )
                continue
            return ProposalResult(
                turtle=turtle,
                attempts=attempt,
                model=settings.model_name,
                deployment=settings.model_deployment,
                api_version=settings.model_api_version,
            )

        # **部分的に正しい Turtle を返さない**(ADR-0043 決定4)。
        raise ProposalUnusableError(
            f"{settings.proposal_max_attempts} 回試しましたが、検証を通る候補が"
            f"得られませんでした。最後の理由: {last_error}",
            attempts=settings.proposal_max_attempts,
            last_error=last_error,
        )

    async def _complete(self, user_prompt: str) -> str:
        """チャット補完を 1 回呼んで本文を返す。

        **`tools` を渡さない**(ADR-0043 決定2b)。モデルに副作用を作れる口を
        与えない。

        **応答の本文を例外に載せない。** プロンプトにはカタログが含まれ、
        応答にはその写しが混ざりうる。状態コードだけで運用者は原因を
        切り分けられる。
        """
        settings = self._settings
        url = (
            f"{settings.model_endpoint.rstrip('/')}/openai/deployments/"
            f"{settings.model_deployment}/chat/completions"
        )
        payload = {
            "messages": [
                {"role": "system", "content": PROPOSAL_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            # **決定的にしようとはするが、再現性は主張しない**(決定10)。
            "temperature": 0,
            "max_tokens": settings.proposal_max_output_tokens,
        }
        headers = {"Authorization": f"Bearer {await self._bearer()}"}
        params = {"api-version": settings.model_api_version}

        client = self._client
        if client is None:
            async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as owned:
                response = await owned.post(url, json=payload, headers=headers, params=params)
        else:
            response = await client.post(url, json=payload, headers=headers, params=params)

        if response.status_code != httpx.codes.OK:
            raise ModelUnavailableError(
                f"モデル '{settings.model_deployment}' の呼び出しに失敗しました"
                f"(HTTP {response.status_code})"
            )
        return _first_message(response.json())

    async def _bearer(self) -> str:
        """マネージド ID のトークンを返す(ADR-0043 決定9)。

        **API キーを使わない。** アカウント側でローカル認証を無効にしてある。
        """
        from azure.identity import DefaultAzureCredential

        return str(DefaultAzureCredential().get_token(_MODEL_SCOPE).token)


def _first_message(body: object) -> str:
    """応答から本文を取り出す。

    **形が違えば「届かなかった」として扱う**(ADR-0043 決定3 の手前)。
    空文字を返すと、この後の検証が「Turtle として解析できません」と言い、
    **モデルの設定の誤りが「モデルが変なことを言った」に見える**。
    """
    if not isinstance(body, dict):
        raise ModelUnavailableError("モデルの応答が JSON オブジェクトではありません")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelUnavailableError("モデルの応答に choices がありません")
    first = choices[0]
    if not isinstance(first, dict):
        raise ModelUnavailableError("モデルの応答の choices[0] がオブジェクトではありません")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ModelUnavailableError("モデルの応答に message がありません")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ModelUnavailableError("モデルの応答の本文が空です")
    return content
