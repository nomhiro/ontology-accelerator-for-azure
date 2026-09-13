"""識別子の並び順を環境に依存させない(`P3-12`)。

## 何が起きたか

ローカルの PostgreSQL のイメージを `postgres:16-alpine` から
`pgvector/pgvector:pg16` へ替えた(`P3-02`)ところ、**触っていない
`test_term_owners.py` の並び順のテストが落ちた**。

原因は照合順序である。

| どこ | `lc_collate` |
|---|---|
| `postgres:16-alpine`(musl) | **`C`**(バイト順) |
| `pgvector/pgvector:pg16`(Debian) | `en_US.utf8` |
| **Azure Database for PostgreSQL** | **`en_US.utf8`**(読み取り専用。他を選べない) |
| `infra/modules/postgres.bicep` の宣言 | `en_US.utf8` |

**つまりデプロイ環境はずっと `en_US.utf8` で、ローカルと CI だけが `C`
だった。** 落ちたテストは**本番では成り立たない順序を主張していた**。
イメージの差し替えは、既にあった食い違いを見えるようにしただけである。

## 実測した差

`en_US.utf8` は第一水準で句読点を無視するので、`://` の位置が効かない。

```
en_US: https://e.example/#Customer , https://e.example/#Product , http://www.w3.org/…#Concept
C    : http://www.w3.org/…#Concept , https://e.example/#Customer , https://e.example/#Product
```

**IRI だけの問題ではない。** ハイフンを含む名前でも変わる
(名前空間名は `[a-z0-9-]` なので該当する)。

```
en_US: aa , a-b , ab , a-c
C    : a-b , a-c , aa , ab
```

## なぜ `C` に寄せるのか

**識別子は言語ではない。** IRI・名前空間名・プリンシパル ID は機械が
作る文字列で、**人間向けの辞書順に並べる理由が無い**。一方で

- **クライアントが自分で並べ替えたときに一致してほしい**
  (Python の `sorted()`、JavaScript の `Array.sort()` はどちらもコード
  ポイント順である)
- **環境をまたいで同じ順序でなければ、ページングの境界が壊れる**
  (いまは識別子でページングしていないが、`id` を使っている理由と同じ向き)

だから**列の照合順序を変えるのではなく、並べるときに `C` を指定する**。
列を `COLLATE "C"` で宣言する案は採らなかった — **既存のデプロイを
マイグレーションで触る必要があり、索引も作り直しになる**。並べ替えは
読み取りの都合なので、読むときに指定するほうが影響が小さい。

**人間が読むためのテキスト(ラベル・説明)には使わない。** そちらを
バイト順に並べると日本語の並びが壊れる。この道具は**識別子専用**である。
"""

from __future__ import annotations

from sqlalchemy import ColumnElement
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import CollationClause

__all__ = ["IDENTIFIER_COLLATION", "by_identifier"]

#: 識別子を並べるときの照合順序。**バイト順**である。
#:
#: `"C"` は SQL の識別子として渡るので、PostgreSQL 側では `COLLATE "C"` に
#: なる。**`POSIX` ではなく `C` を使う** — 同じ意味だが、PostgreSQL の
#: ドキュメントが既定の例に使っているのは `C` である。
IDENTIFIER_COLLATION = "C"


def by_identifier(column: InstrumentedAttribute[str] | ColumnElement[str]) -> CollationClause:
    """識別子の列を、環境に依存しないバイト順で並べる式を返す。

    **IRI・名前空間名・プリンシパル ID など、機械が作る文字列に使う。**
    人間が読むためのラベルや説明には使わない(日本語の並びが壊れる)。

    Example:
        ```python
        select(TermOwnerRow).order_by(by_identifier(TermOwnerRow.term_iri))
        ```

    Args:
        column: 並べ替えの対象になるテキスト列。

    Returns:
        `COLLATE "C"` を付けた式。`order_by` にそのまま渡せる。
    """
    return column.collate(IDENTIFIER_COLLATION)
