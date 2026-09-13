"""埋め込みの次元を 4 か所で一致させる(ADR-0050、`P3-02`)。

## なぜ機械的に突き合わせるのか

次元は**4 か所**に書かれている。

| どこ | 何のため |
|---|---|
| `ontology_core.embedding.EMBEDDING_DIMENSIONS` | 応答の検査とテーブル定義 |
| `Settings.embedding_dimensions` の既定 | 実行時の設定 |
| `alembic/versions/0014_term_embeddings.py` | **列の次元**(過去の履歴なので不変) |
| `infra/modules/model.bicep` の `embeddingDimensions` | デプロイ環境への注入 |

**どこも import で繋げられない。** マイグレーションは「過去の履歴が現在の
コードに依存してはいけない」ので import しない。Bicep は Python ではない。
`Settings` は `import ontology_core` のたびに rdflib を読み込ませたくない。

**食い違うと実行時まで分からない。** 列は 1536 次元なのに 3072 次元の
埋め込みを保存しようとして初めて落ちる。だから**このテストが唯一の綱**である。

## 検査は AST を通す

**本文(docstring とコメント)を検査の対象にしない。** 説明のために書いた語が
そのまま検査に引っかかって落ちたことがある(`test_受け付けない構成を題材に
使っていない` で踏んだ形)。**実行する文と import だけを見る。**

## 上限にも意味がある

pgvector の `hnsw` 索引は **2000 次元まで**である(実測。2001 以上で
`column cannot have more than 2000 dimensions for hnsw index`)。
`text-embedding-3-large`(3072)を選べないのはこれが理由である。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from ontology_core.config import Settings
from ontology_core.embedding import EMBEDDING_DIMENSIONS

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION = _ROOT / "packages/api/alembic/versions/0014_term_embeddings.py"
_MODEL_BICEP = _ROOT / "infra/modules/model.bicep"

#: pgvector の `hnsw` 索引の上限(実測)。
_HNSW_MAX_DIMENSIONS = 2000


def _migration_tree() -> ast.Module:
    return ast.parse(_MIGRATION.read_text(encoding="utf-8"))


def _migration_dimensions() -> int:
    text = _MIGRATION.read_text(encoding="utf-8")
    match = re.search(r"^EMBEDDING_DIMENSIONS\s*=\s*(\d+)$", text, re.MULTILINE)
    assert match is not None, f"{_MIGRATION.name} に EMBEDDING_DIMENSIONS がありません"
    return int(match.group(1))


def _executed_sql() -> str:
    """マイグレーションが実際に呼び出しへ渡す文字列だけを集める。

    docstring とコメントは AST に残らないので、**説明のために書いた語が
    検査に引っかかることがない**。
    """
    found: list[str] = []
    for node in ast.walk(_migration_tree()):
        if not isinstance(node, ast.Call):
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                found.append(argument.value)
    return " || ".join(found)


def _imported_modules() -> set[str]:
    """マイグレーションが import しているモジュール名。"""
    names: set[str] = set()
    for node in ast.walk(_migration_tree()):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def _bicep_dimensions() -> int:
    text = _MODEL_BICEP.read_text(encoding="utf-8")
    match = re.search(r"^output embeddingDimensions int = (\d+)$", text, re.MULTILINE)
    assert match is not None, "model.bicep に embeddingDimensions の出力がありません"
    return int(match.group(1))


def test_4_か所の次元が一致している() -> None:
    """**食い違うと実行時まで分からない。** ここが唯一の綱である。"""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    values = {
        "ontology_core.embedding": EMBEDDING_DIMENSIONS,
        "Settings の既定": settings.embedding_dimensions,
        "マイグレーション 0014": _migration_dimensions(),
        "model.bicep の出力": _bicep_dimensions(),
    }
    assert len(set(values.values())) == 1, f"埋め込みの次元が食い違っている: {values}"


def test_次元が_hnsw_の上限を超えていない() -> None:
    """**2000 次元を超えると索引が作れない**(実測)。

    超える値に変えたくなったら、`halfvec` へのキャストか、索引を
    `ivfflat` に変えるかの判断が必要になる。**黙って超えられないようにする。**
    """
    assert EMBEDDING_DIMENSIONS <= _HNSW_MAX_DIMENSIONS


def test_設定の上限も_hnsw_に合わせてある() -> None:
    """`EMBEDDING_DIMENSIONS` を環境変数で 3072 にできてはいけない。"""
    field = Settings.model_fields["embedding_dimensions"]
    limits = [getattr(item, "le", None) for item in field.metadata]
    assert _HNSW_MAX_DIMENSIONS in limits, (
        "Settings.embedding_dimensions に hnsw の上限(2000)が掛かっていない"
    )


def test_マイグレーションがコードの定数を_import_していない() -> None:
    """**過去の履歴が現在のコードに依存してはいけない。**

    import すると、定数を変えた瞬間に**過去のマイグレーションの意味が変わる**
    (既に 1536 で作った列が、履歴の上では別の次元になる)。
    """
    assert "ontology_core.embedding" not in _imported_modules()


def test_マイグレーションが拡張を作っていない() -> None:
    """**alembic は `azure_pg_admin` ではない。**

    `env.py` は `SET ROLE ontology_owner`(NOLOGIN)で走るので、拡張の作成が
    できない。書いてあると**デプロイの途中で落ちる**。作るのは
    `scripts/bootstrap-db.py` / `just up` / `conftest.py` である。
    """
    assert "CREATE EXTENSION" not in _executed_sql().upper()


def test_2_つの索引を作っている() -> None:
    """**片方だけでは検索が成立しない**(ADR-0050 決定6)。

    実測で、「お客様」は 3-gram では全件 0.000 であり、「顧客ID」は
    `similarity` の既定閾値では 0 件である。**両方の経路が必要である。**
    """
    sql = _executed_sql()
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
    assert "USING gin (source_text gin_trgm_ops)" in sql
