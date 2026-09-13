"""用語の検索と、埋め込みの作り直し(ADR-0050、`P3-02`)。

## 埋め込みは承認済み版から作る

**提案中の版からは作らない。** 検索に出た用語は「使ってよい語彙」として
エージェントに渡る。未承認の版の用語が出ると、**四眼原則を通っていない
語彙が事実上流通する**(不変条件12 が守ろうとしているものと同じ向き)。

## 作成に失敗しても承認は止めない

埋め込みは射影である(不変条件1・3 と同じ形)。だから `approve` の経路から
呼ばない — **別の口(`POST .../search/rebuild`)にしてある**。承認の中で
呼ぶと、埋め込みモデルの停止が承認を止める。

## 「作っていない」を「該当なし」にしない

モデルが未設定、または埋め込みが 0 件の名前空間で検索すると、ベクトルの
経路は使えない。**3-gram の経路だけで結果を返し、`vector_available` を
偽にして理由を必ず添える**(ADR-0050 決定8)。空を返して「該当する用語が
ありません」と読ませない。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.embeddings import EmbeddingRecord, TermEmbeddingRepository
from ontology_api.repositories.versions import VersionRepository
from ontology_core.blob import BlobStoreError, OntologyBlobStore
from ontology_core.config import Settings
from ontology_core.embedding import EmbeddingClient, EmbeddingError, term_texts
from ontology_core.models import OntologyVersionStatus
from ontology_core.search import RankedRow, SearchOutcome, clamp_limit, fuse

__all__ = [
    "RebuildOutcome",
    "SearchUnavailableError",
    "rebuild_embeddings",
    "search_terms",
]

#: ベクトルの経路が使えない理由。**空にしない**(ADR-0050 決定8)。
_NO_MODEL = (
    "埋め込みモデルが設定されていません(EMBEDDING_DEPLOYMENT が空)。"
    "表記の部分一致(3-gram)だけで検索しました。"
    "**言い換えでは当たりません** — 結果が空でも「該当なし」とは限りません"
)
_NOT_BUILT = (
    "この名前空間の埋め込みがまだ作られていません。"
    "表記の部分一致(3-gram)だけで検索しました。"
    "埋め込みの作り直しの口で作成できます"
)
_EMBED_FAILED = (
    "問いの埋め込みに失敗しました。表記の部分一致(3-gram)だけで検索しました。"
    "**ベクトルの経路が落ちたことは「該当なし」ではありません**"
)


class SearchUnavailableError(Exception):
    """検索そのものを実行できないことを表す。

    **「該当なし」とは別物である。** 呼び出し元は 404 / 503 として返し、
    空の結果を返さない(`DivergenceUnavailableError` と同じ判断)。
    """


@dataclass(frozen=True)
class RebuildOutcome:
    """埋め込みの作り直しの結果。

    Attributes:
        namespace: 対象の名前空間。
        version: どの承認済み版から作ったか。
        model: どのモデルで作ったか。
        term_count: 書いた用語の数。
        truncated_terms: 上限で切り詰めた用語の IRI。**黙って切らない。**
        deprecated_terms: 廃止済みとして印を付けた用語の IRI。
    """

    namespace: str
    version: str
    model: str
    term_count: int
    truncated_terms: tuple[str, ...]
    deprecated_terms: tuple[str, ...]


async def _approved_version(session: AsyncSession, *, namespace: str) -> tuple[str, str]:
    """承認済み版の版番号と Blob のパスを返す。

    Raises:
        SearchUnavailableError: 承認済みの版が無いとき。
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
        raise SearchUnavailableError(
            f"名前空間 {namespace} に承認済みの版がありません。"
            "**提案中の版からは埋め込みを作りません** — "
            "四眼原則を通っていない語彙を検索に出さないためです"
        )
    return current.version, current.blob_path


async def _embed_all(
    client: EmbeddingClient, inputs: tuple[str, ...]
) -> tuple[tuple[float, ...], ...]:
    """上限で分割して埋め込む。**入力と同じ順序で返す。**

    `EmbeddingClient.embed` は 1 回の上限(`MAX_BATCH`)を超えると断るので、
    分割はここで行う。**順序は連結で保たれる** — バッチごとに `embed` が
    応答の `index` で並べ直している。
    """
    vectors: list[tuple[float, ...]] = []
    batch = EmbeddingClient.MAX_BATCH
    for start in range(0, len(inputs), batch):
        vectors.extend(await client.embed(inputs[start : start + batch]))
    return tuple(vectors)


async def rebuild_embeddings(
    session: AsyncSession,
    *,
    blob: OntologyBlobStore,
    settings: Settings,
    namespace: str,
    client: EmbeddingClient,
) -> RebuildOutcome:
    """承認済み版の TTL から埋め込みを作り直す。

    **名前空間の行を丸ごと入れ替える**(`TermEmbeddingRepository.replace`)。
    縮めた版を反映したときに、消えた用語の行が残らないようにするため。

    Raises:
        SearchUnavailableError: モデルが未設定、承認済み版が無い、正本が
            読めない、TTL が解析できない、用語が 1 件も無い、または
            埋め込みの呼び出しが失敗したとき。**どれも「0 件作った」とは
            言わない。**
    """
    if not settings.embedding_deployment:
        raise SearchUnavailableError(
            "埋め込みモデルが設定されていません(EMBEDDING_DEPLOYMENT が空)。"
            "埋め込みを作るにはモデルのデプロイが必要です"
        )

    version, blob_path = await _approved_version(session, namespace=namespace)

    try:
        turtle = await blob.get_version(blob_path)
    except BlobStoreError as exc:
        raise SearchUnavailableError(
            f"承認済み版 {version} の正本を読めませんでした: {exc}"
        ) from exc

    try:
        texts = term_texts(turtle)
    except ValueError as exc:
        # **「読めなかった」を「用語が無かった」にしない。**
        raise SearchUnavailableError(
            f"承認済み版 {version} の正本を解析できませんでした: {exc}"
        ) from exc

    if not texts:
        raise SearchUnavailableError(
            f"承認済み版 {version} に埋め込みの対象になる用語がありません。"
            "クラスとプロパティのどちらも見つかりませんでした"
        )

    try:
        vectors = await _embed_all(client, tuple(item.source_text for item in texts))
    except EmbeddingError as exc:
        raise SearchUnavailableError(f"埋め込みの生成に失敗しました: {exc}") from exc

    model = settings.embedding_model_name or settings.embedding_deployment
    records = tuple(
        EmbeddingRecord(
            term_iri=item.iri,
            source_text=item.source_text,
            vector=vector,
            deprecated=item.deprecated,
        )
        for item, vector in zip(texts, vectors, strict=True)
    )
    written = await TermEmbeddingRepository(session).replace(
        namespace=namespace, version=version, model=model, records=records
    )
    return RebuildOutcome(
        namespace=namespace,
        version=version,
        model=model,
        term_count=written,
        truncated_terms=tuple(item.iri for item in texts if item.truncated),
        deprecated_terms=tuple(item.iri for item in texts if item.deprecated),
    )


async def search_terms(
    session: AsyncSession,
    *,
    settings: Settings,
    namespace: str,
    query: str,
    limit: int,
    client: EmbeddingClient,
) -> SearchOutcome:
    """用語を検索する。2 つの経路を RRF で融合する。

    **ベクトルの経路が使えなくても検索は成立する。** 3-gram の経路だけで
    結果を返し、`vector_available` を偽にして理由を添える。

    Raises:
        SearchUnavailableError: 問いが空のとき。**空の問いで全件を返さない**
            — 「検索した結果これが全部である」と読めてしまう。
    """
    if not query.strip():
        raise SearchUnavailableError(
            "検索の問いが空です。**空の問いで全件を返しません** — "
            "「検索した結果これが全部である」と読めてしまいます"
        )

    capped = clamp_limit(limit)
    repository = TermEmbeddingRepository(session)
    embedded = await repository.count(namespace=namespace)

    # 3-gram の経路は**常に引く**(PostgreSQL だけで完結する)。
    trigram = await repository.search_trigram(
        namespace=namespace,
        query=query,
        limit=capped,
        threshold=settings.search_trigram_threshold,
    )

    vector_note = ""
    vector_rows: tuple[RankedRow, ...] = ()
    if not settings.embedding_deployment:
        vector_note = _NO_MODEL
    elif embedded == 0:
        vector_note = _NOT_BUILT
    else:
        try:
            vectors = await client.embed((query,))
        except EmbeddingError:
            # **問いを載せない。** 利用者の入力である。
            #
            # **例外を伝播させない。** ベクトルの経路が落ちても 3-gram の
            # 結果は正しいので、返せるものを返して**落ちたことを見せる**
            # (不変条件3 と同じ向き)。
            vector_note = _EMBED_FAILED
        else:
            vector_rows = await repository.search_vector(
                namespace=namespace, vector=vectors[0], limit=capped
            )

    return SearchOutcome(
        hits=fuse(vector=vector_rows, trigram=trigram, limit=capped),
        vector_available=not vector_note,
        vector_note=vector_note,
        embedded_term_count=embedded,
    )
