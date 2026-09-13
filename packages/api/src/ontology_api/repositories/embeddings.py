"""用語の埋め込みの読み書きと、2 つの検索経路(ADR-0050、`P3-02`)。

**この表は射影である。** 正本は Blob の TTL なので、`replace` は
名前空間の行を**丸ごと入れ替える**。差分で更新しない — 用語が消えた版
(廃止して縮めた版)を反映するときに、消えた行が残ると
**「検索に出るが版には無い用語」**ができてしまう。

## 2 つの経路の SQL

| 経路 | 演算子 | 索引 |
|---|---|---|
| ベクトル | `<=>`(cosine 距離) | `hnsw (embedding vector_cosine_ops)` |
| 3-gram | `<%`(`word_similarity`) | `gin (source_text gin_trgm_ops)` |

**`similarity` ではなく `word_similarity` を使う**(ADR-0050 決定6)。
実測: `pg_trgm` の既定閾値 0.3 の `similarity` は「顧客ID」で**1 件も
当たらない**(`source_text` は `customerId / 顧客ID / 顧客を一意に識別する番号`
のように長く、短い問いは全体の類似度を上げない)。`word_similarity` は
同じ行に 1.000 で当たる。

**引数の順序が意味を持つ。** `word_similarity(問い, 対象)` である
(逆にすると「対象の中の語が問いにどれだけ含まれるか」になり、
長い `source_text` では常に低くなる)。

## SQLAlchemy の `text()` で `%` を二重にしてはいけない

asyncpg のプレースホルダは `$1` なので、**`%` はエスケープされない**。
`%%` と書くと `operator does not exist: text %% text` で落ちる(実測)。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_core.db import TermEmbeddingRow
from ontology_core.search import RankedRow

__all__ = ["EmbeddingRecord", "TermEmbeddingRepository", "format_vector"]


@dataclass(frozen=True)
class EmbeddingRecord:
    """書き込む 1 行。

    Attributes:
        term_iri: 用語の IRI。
        source_text: 埋め込みの材料。**検索の結果に返す。**
        vector: 埋め込み。
        deprecated: 廃止済みか。**除外せず印を付ける。**
    """

    term_iri: str
    source_text: str
    vector: tuple[float, ...]
    deprecated: bool


def format_vector(values: Sequence[float]) -> str:
    """`vector` 型のリテラル表記にする。

    **`pgvector` の SQLAlchemy 型を通さず自分で書く。** 生の `text()` に
    渡すので、コデックではなく `CAST(:q AS vector)` で解釈させる
    (ORM の列と生の SQL の両方から同じ表を触るため)。
    """
    return "[" + ",".join(repr(float(value)) for value in values) + "]"


class TermEmbeddingRepository:
    """用語の埋め込み(名前空間ごとの射影)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace(
        self,
        *,
        namespace: str,
        version: str,
        model: str,
        records: Sequence[EmbeddingRecord],
    ) -> int:
        """名前空間の埋め込みを丸ごと入れ替える。書いた件数を返す。

        **差分更新にしない。** 縮めた版(用語を廃止して消した版)を
        反映したときに、消えた用語の行が残ると**検索に出るが版には無い
        用語**ができる。射影なので作り直すのが正しい。

        **commit はしない。** 呼び出し側がトランザクションの境界を持つ
        (このリポジトリ群の共通の約束)。
        """
        await self._session.execute(
            delete(TermEmbeddingRow).where(TermEmbeddingRow.namespace == namespace)
        )
        for record in records:
            # **生の SQL で書く。** ORM の `Vector` 型は pgvector の
            # コデックを要求するが、検索側は `text()` で引くので
            # 両方を同じ表記(`CAST(... AS vector)`)に寄せる。
            await self._session.execute(
                text(
                    "INSERT INTO term_embeddings "
                    "(namespace, term_iri, source_text, embedding, "
                    "built_from_version, model, deprecated) "
                    "VALUES (:ns, :iri, :src, CAST(:emb AS vector), :ver, :model, :dep)"
                ),
                {
                    "ns": namespace,
                    "iri": record.term_iri,
                    "src": record.source_text,
                    "emb": format_vector(record.vector),
                    "ver": version,
                    "model": model,
                    "dep": record.deprecated,
                },
            )
        return len(records)

    async def count(self, *, namespace: str) -> int:
        """その名前空間で埋め込みを持つ用語の数。

        **`0` は「作っていない」である**(「該当なし」ではない)。
        検索の応答が `vector_available` を決めるのに使う。
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(TermEmbeddingRow)
            .where(TermEmbeddingRow.namespace == namespace)
        )
        return int(result.scalar() or 0)

    async def built_state(self, *, namespace: str) -> tuple[str, str] | None:
        """どの版・どのモデルから作ったか。無ければ `None`。

        **1 行だけ読む。** `replace` が丸ごと入れ替えるので、
        名前空間の中で版とモデルは揃っている。
        """
        result = await self._session.execute(
            select(TermEmbeddingRow.built_from_version, TermEmbeddingRow.model)
            .where(TermEmbeddingRow.namespace == namespace)
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None
        return str(row[0]), str(row[1])

    async def search_vector(
        self, *, namespace: str, vector: Sequence[float], limit: int
    ) -> tuple[RankedRow, ...]:
        """ベクトルの経路。cosine の類似度の降順で返す。

        **`1 - 距離` を類似度として返す。** `<=>` は距離(小さいほど近い)
        なので、そのまま返すと融合の入力の向きが逆になる。
        """
        result = await self._session.execute(
            text(
                "SELECT term_iri, source_text, deprecated, "
                "1 - (embedding <=> CAST(:q AS vector)) AS sim "
                "FROM term_embeddings WHERE namespace = :ns "
                "ORDER BY embedding <=> CAST(:q AS vector), term_iri LIMIT :lim"
            ),
            {"ns": namespace, "q": format_vector(vector), "lim": limit},
        )
        return tuple(
            RankedRow(
                term_iri=str(row[0]),
                similarity=float(row[3]),
                source_text=str(row[1]),
                deprecated=bool(row[2]),
            )
            for row in result.all()
        )

    async def search_trigram(
        self, *, namespace: str, query: str, limit: int, threshold: float
    ) -> tuple[RankedRow, ...]:
        """3-gram の経路。`word_similarity` の降順で返す。

        **閾値を SQL 側で比較する**(`<%` 演算子に頼らない)。`<%` は
        セッション変数 `pg_trgm.word_similarity_threshold` を見るので、
        **設定した閾値が効いているかを応答から確かめられない**。
        明示的な比較なら渡した値がそのまま効く。

        **同点は IRI の昇順。** 実行ごとに順序が変わらないようにする。
        """
        result = await self._session.execute(
            text(
                "SELECT term_iri, source_text, deprecated, "
                "word_similarity(:q, source_text) AS sim "
                "FROM term_embeddings WHERE namespace = :ns "
                "AND word_similarity(:q, source_text) >= :threshold "
                "ORDER BY sim DESC, term_iri LIMIT :lim"
            ),
            {"ns": namespace, "q": query, "lim": limit, "threshold": threshold},
        )
        return tuple(
            RankedRow(
                term_iri=str(row[0]),
                similarity=float(row[3]),
                source_text=str(row[1]),
                deprecated=bool(row[2]),
            )
            for row in result.all()
        )
