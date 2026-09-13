"""2 つの検索経路を実物の PostgreSQL に対して検査する(ADR-0050、`P3-02`)。

## フェイクでは分からない

このファイルが実物の DB を要求する理由は 3 つある。

1. **`pgvector` の `<=>` と `pg_trgm` の `word_similarity` は SQL 側の機能**で
   あり、Python 側に等価な実装が無い。フェイクで通しても何も確かめていない
2. **`similarity` では日本語の短い問いが当たらない**(実測)。これは
   閾値と 3-gram の定義に由来する挙動で、**実物に投げて初めて分かる**
3. **`vector` 型は asyncpg の素の経路では扱えない。** `CAST(... AS vector)`
   の表記が正しいかは実物でしか確かめられない

ここで固定するのは 6 つである。

1. **ベクトルの経路は cosine の類似度の降順で返る**(距離ではない)
2. **3-gram の経路は `word_similarity` で当たる。** `similarity` の既定閾値
   では「顧客ID」が 0 件になることも併せて固定する
3. **「お客様」は 3-gram では当たらない。** ベクトルの経路が必要である証拠
4. **`replace` は名前空間の行を丸ごと入れ替える**(縮めた版を反映できる)
5. **`%` を二重にしてはいけない**(asyncpg は `$1` 形式)
6. **名前空間をまたいで漏れない**(不変条件5)
"""

from __future__ import annotations

import math
import random

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ontology_api.repositories.embeddings import (
    EmbeddingRecord,
    TermEmbeddingRepository,
    format_vector,
)
from ontology_api.repositories.namespaces import NamespaceRepository
from ontology_core.embedding import EMBEDDING_DIMENSIONS

_NS = "search-ns"
_OTHER = "search-other"
_BASE = "https://e.example/s#"

#: 題材。`source_text` は `build_source_text` が作る形に揃えてある。
_TERMS = (
    ("Customer", "Customer / 顧客 / サービスを利用する個人または法人"),
    ("customerId", "customerId / 顧客ID / 顧客を一意に識別する番号"),
    ("Order", "Order / 注文 / 顧客が商品を購入する取引"),
    ("orderedAt", "orderedAt / 注文日時"),
)


def _unit(seed: int) -> tuple[float, ...]:
    """決定的な単位ベクトル。**`random.Random(seed)` で再現する。**"""
    rng = random.Random(seed)
    raw = [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIMENSIONS)]
    norm = math.sqrt(sum(value * value for value in raw))
    return tuple(value / norm for value in raw)


def _axis(index: int) -> tuple[float, ...]:
    """1 つの軸だけが立ったベクトル。**類似度を狙って作れる。**"""
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[index] = 1.0
    return tuple(values)


async def _namespace(session: AsyncSession, name: str) -> None:
    await NamespaceRepository(session).create(
        name=name,
        display_name=name,
        description="",
        base_iri=_BASE,
        created_by="tester",
    )
    await session.commit()


async def _seed(session: AsyncSession, namespace: str) -> TermEmbeddingRepository:
    """4 つの用語を、軸をずらしたベクトルで入れる。"""
    repository = TermEmbeddingRepository(session)
    records = tuple(
        EmbeddingRecord(
            term_iri=f"{_BASE}{local}",
            source_text=source,
            vector=_axis(index),
            deprecated=local == "orderedAt",
        )
        for index, (local, source) in enumerate(_TERMS)
    )
    await repository.replace(
        namespace=namespace, version="1.0.0", model="test-model", records=records
    )
    await session.commit()
    return repository


async def test_ベクトルの経路は類似度の降順で返る(session: AsyncSession) -> None:
    """**`1 - 距離` を返す。** `<=>` をそのまま返すと融合の向きが逆になる。"""
    await _namespace(session, _NS)
    repository = await _seed(session, _NS)

    rows = await repository.search_vector(namespace=_NS, vector=_axis(0), limit=4)

    assert rows[0].term_iri == f"{_BASE}Customer"
    assert rows[0].similarity == pytest.approx(1.0)
    # 直交する軸は cosine 類似度 0。**降順であることを固定する。**
    similarities = [row.similarity for row in rows]
    assert similarities == sorted(similarities, reverse=True)
    assert rows[-1].similarity == pytest.approx(0.0)


async def test_ベクトルの経路が材料と廃止の印を返す(session: AsyncSession) -> None:
    """**なぜ当たったかを読むために材料を返す。**"""
    await _namespace(session, _NS)
    repository = await _seed(session, _NS)

    rows = await repository.search_vector(namespace=_NS, vector=_axis(3), limit=1)

    assert rows[0].term_iri == f"{_BASE}orderedAt"
    assert rows[0].source_text == "orderedAt / 注文日時"
    assert rows[0].deprecated is True


async def test_3_gram_の経路は日本語の部分一致で当たる(session: AsyncSession) -> None:
    """**`word_similarity` を使う理由の実証。**

    `source_text` は `customerId / 顧客ID / 顧客を一意に識別する番号` と
    長いが、「顧客ID」で当たる。
    """
    await _namespace(session, _NS)
    repository = await _seed(session, _NS)

    rows = await repository.search_trigram(namespace=_NS, query="顧客ID", limit=5, threshold=0.3)

    assert rows[0].term_iri == f"{_BASE}customerId"
    assert rows[0].similarity == pytest.approx(1.0)


async def test_similarity_の既定閾値では日本語の短い問いが当たらない(
    session: AsyncSession,
) -> None:
    """**`word_similarity` を選んだ理由をここで固定する**(ADR-0050 決定6)。

    このテストは**実装ではなく PostgreSQL の挙動**を固定している。
    `similarity` に変えたくなったら、まずここが「当たらない」と言う。
    """
    await _namespace(session, _NS)
    await _seed(session, _NS)

    # **`%` を二重にしない。** asyncpg は `$1` 形式なので `%%` は
    # `operator does not exist: text %% text` になる(実測)。
    result = await session.execute(
        text("SELECT count(*) FROM term_embeddings WHERE namespace = :ns AND source_text % :q"),
        {"ns": _NS, "q": "顧客ID"},
    )
    assert result.scalar() == 0, "similarity の既定閾値で当たるなら word_similarity は不要である"

    # **既定の閾値(0.6)で、目的の用語だけが当たる。**
    word = await session.execute(
        text(
            "SELECT term_iri FROM term_embeddings "
            "WHERE namespace = :ns AND word_similarity(:q, source_text) >= 0.6"
        ),
        {"ns": _NS, "q": "顧客ID"},
    )
    assert [str(row[0]) for row in word.all()] == [f"{_BASE}customerId"]


async def test_pg_trgm_の既定の閾値を固定する(session: AsyncSession) -> None:
    """**設定の既定値の根拠を実測で固定する**(ADR-0050 決定6)。

    `Settings.search_trigram_threshold` の既定 0.6 は、**PostgreSQL 自身が
    `<%` に対して選んだ値**である。**この値が変わったら設定の既定も
    見直す**必要がある(そのときここが落ちる)。

    **`SHOW` を使うには同じセッションで pg_trgm の関数を呼んでおく必要が
    ある。** 呼ばずに `SHOW` すると
    `unrecognized configuration parameter` になる(実測)。
    """
    await session.execute(text("SELECT similarity('a', 'b')"))
    thresholds = {}
    for name in ("similarity_threshold", "word_similarity_threshold"):
        result = await session.execute(text(f"SHOW pg_trgm.{name}"))
        thresholds[name] = float(str(result.scalar()))
    assert thresholds == {"similarity_threshold": 0.3, "word_similarity_threshold": 0.6}


async def test_設定の既定が_pg_trgm_の既定と揃っている() -> None:
    """**PostgreSQL が選んだ値から勝手に緩めない。**

    0.3 に緩めると「顧客ID」で `Customer` まで当たる(実測 0.400)。
    それは**概念の隣**であってベクトルの経路の担当である。
    """
    from ontology_core.config import Settings

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.search_trigram_threshold == 0.6


async def test_言い換えは_3_gram_では当たらない(session: AsyncSession) -> None:
    """**ベクトルの経路が「あれば良いもの」ではない証拠。**

    「お客様」は `Customer / 顧客 / ...` と 3-gram を 1 つも共有しない。
    **閾値を 0 にしても当たらない**ので、これは調整の問題ではない。
    """
    await _namespace(session, _NS)
    repository = await _seed(session, _NS)

    rows = await repository.search_trigram(namespace=_NS, query="お客様", limit=5, threshold=0.0)

    assert all(row.similarity == pytest.approx(0.0) for row in rows), (
        "3-gram で「お客様」に類似度が付くなら、ADR-0050 決定1 の根拠が変わる"
    )


async def test_閾値を上げると絞られる(session: AsyncSession) -> None:
    """**閾値が実際に効いていることを固定する。**

    `<%` 演算子はセッション変数を見るので、**渡した値が効いたかを応答から
    確かめられない**。明示的な比較にしてあるのはこのためである。
    """
    await _namespace(session, _NS)
    repository = await _seed(session, _NS)

    loose = await repository.search_trigram(namespace=_NS, query="注文", limit=5, threshold=0.1)
    strict = await repository.search_trigram(namespace=_NS, query="注文", limit=5, threshold=0.99)

    assert len(loose) > len(strict)


async def test_作り直しは行を丸ごと入れ替える(session: AsyncSession) -> None:
    """**縮めた版を反映できる**(不変条件8)。

    差分更新にすると、廃止して消した用語の行が残り、**検索に出るが版には
    無い用語**ができる。
    """
    await _namespace(session, _NS)
    repository = await _seed(session, _NS)
    assert await repository.count(namespace=_NS) == len(_TERMS)

    await repository.replace(
        namespace=_NS,
        version="2.0.0",
        model="test-model-2",
        records=(
            EmbeddingRecord(
                term_iri=f"{_BASE}Customer",
                source_text="Customer / 顧客",
                vector=_axis(0),
                deprecated=False,
            ),
        ),
    )
    await session.commit()

    assert await repository.count(namespace=_NS) == 1
    assert await repository.built_state(namespace=_NS) == ("2.0.0", "test-model-2")


async def test_どの版とモデルから作ったかは名前空間ごとに返る(session: AsyncSession) -> None:
    """**`built_state` も名前空間で絞る**(不変条件5)。

    絞りが抜けると**他の名前空間の版とモデルを返す**。作り直しが必要かの
    判定にこの 2 つを使うので、混ざると**「作り直した」と誤認する**。

    **両方を検査する。** 片方だけだと、絞りが抜けていても**たまたま
    その行が返って**通ってしまう(`LIMIT 1` に並び順が無い)。
    """
    await _namespace(session, _NS)
    await _namespace(session, _OTHER)
    repository = TermEmbeddingRepository(session)
    for namespace, version, model in ((_NS, "1.0.0", "model-a"), (_OTHER, "2.0.0", "model-b")):
        await repository.replace(
            namespace=namespace,
            version=version,
            model=model,
            records=(
                EmbeddingRecord(
                    term_iri=f"{_BASE}Only",
                    source_text="Only",
                    vector=_axis(0),
                    deprecated=False,
                ),
            ),
        )
    await session.commit()

    assert await repository.built_state(namespace=_NS) == ("1.0.0", "model-a")
    assert await repository.built_state(namespace=_OTHER) == ("2.0.0", "model-b")


async def test_埋め込みが無い名前空間は_0_件を返す(session: AsyncSession) -> None:
    """**`0` は「作っていない」であって「該当なし」ではない。**

    この区別を応答で見せるのが `vector_available` の役目である。
    """
    await _namespace(session, _NS)
    repository = TermEmbeddingRepository(session)
    assert await repository.count(namespace=_NS) == 0
    assert await repository.built_state(namespace=_NS) is None


async def test_名前空間をまたいで漏れない(session: AsyncSession) -> None:
    """**不変条件5。** 名前空間はセキュリティ境界である。"""
    await _namespace(session, _NS)
    await _namespace(session, _OTHER)
    repository = await _seed(session, _NS)

    assert await repository.count(namespace=_OTHER) == 0
    assert await repository.search_vector(namespace=_OTHER, vector=_axis(0), limit=5) == ()
    assert (
        await repository.search_trigram(namespace=_OTHER, query="顧客", limit=5, threshold=0.0)
        == ()
    )


async def test_片方の名前空間を作り直しても他方は残る(session: AsyncSession) -> None:
    """`replace` の `DELETE` が名前空間で絞れていることを固定する。

    絞りが抜けると**他の名前空間の索引が消える**(気づけるのは検索が
    0 件になったときで、原因が遠い)。
    """
    await _namespace(session, _NS)
    await _namespace(session, _OTHER)
    repository = await _seed(session, _NS)
    await _seed(session, _OTHER)

    await repository.replace(namespace=_NS, version="2.0.0", model="m", records=())
    await session.commit()

    assert await repository.count(namespace=_NS) == 0
    assert await repository.count(namespace=_OTHER) == len(_TERMS)


async def test_名前空間を消すと埋め込みも消える(session: AsyncSession) -> None:
    """`ON DELETE CASCADE`。**射影が孤児として残らない。**"""
    await _namespace(session, _NS)
    repository = await _seed(session, _NS)

    await session.execute(text("DELETE FROM namespaces WHERE name = :ns"), {"ns": _NS})
    await session.commit()

    assert await repository.count(namespace=_NS) == 0


async def test_ベクトルの表記が実物に受け付けられる(session: AsyncSession) -> None:
    """`format_vector` が `vector` 型のリテラルとして通ることを固定する。

    **ここを間違えると保存の時点で落ちる**が、単体テストでは文字列の形しか
    確かめられない。
    """
    await _namespace(session, _NS)
    result = await session.execute(
        text("SELECT CAST(:v AS vector)"), {"v": format_vector(_unit(7))}
    )
    assert result.scalar() is not None


async def test_索引が_5_つ作られている(session: AsyncSession) -> None:
    """**2 つの経路の索引が実際に存在することを固定する。**

    **`EXPLAIN` では確かめられない。** 実測で、212 行では planner が
    どちらの索引も選ばず Seq Scan になる(行数が少ないため)。
    「索引が使われる」ではなく「**索引がある**」を固定する。
    """
    result = await session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'term_embeddings'")
    )
    names = {str(row[0]) for row in result.all()}
    assert {"ix_term_embeddings_vector", "ix_term_embeddings_trgm"} <= names
