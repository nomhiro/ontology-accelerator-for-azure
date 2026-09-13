#!/bin/sh
# 仮想グラフ (Ontop VKG) が実物の PostgreSQL に対して成立することを確かめる
# (P3-01、ADR-0046)。
#
# 前提: docker が動いていること、uv と curl が入っていること。
# 使い方: sh containers/ontop/ontop-check.test.sh
#
# **これが `P3-01` の実機確認である。** Python 側のテスト
# (`test_r2rml.py` / `test_vkg_client.py` / `test_vkg_api.py`) は
# マッピングの検査とこちら側の HTTP の扱いを固定するが、
# **SPARQL が SQL に書き換わって実データが返ること**は実物でしか確かめられない。
#
# ここで固定するのは 6 つである。
#
# 1. **2 表をまたぐ SPARQL の結合が、実データの行として返る**
#    (仮想グラフの要点そのもの)
# 2. **TBox の推論が効く**(上位クラスで問い合わせるとインスタンスが返る)
# 3. **TBox のトリプル自体はデータとして返らない** —
#    つまり**宛先を間違えると 0 件が返る**(ADR-0046 決定11 の根拠)
# 4. **SPARQL Update を受け付けない**(構造的に読み取り専用)
# 5. **ポータルページが出ない**
# 6. **不正なマッピングのエラー本文が全関係を列挙する**
#    (ADR-0046 決定10 の根拠。列挙をやめたらここが落ちて気づける)
#
# **JSON の判定は `uv run python` で行う。** jq を要求すると Windows では
# 追加の docker が必要になり、この検査自体が docker を回すので入れ子になる。
# 素の `python` を呼ばないのは scripts/lint-shell.sh の規律に従うため。
set -eu

# Git Bash は `/` で始まる引数を Windows のパスに変換する(CLAUDE.md に記録済み)。
# 抑止しないと `-v .../input:/opt/ontop/input` の右側が壊れる。Linux では無害。
MSYS_NO_PATHCONV=1
MSYS2_ARG_CONV_EXCL='*'
export MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

IMAGE=ontology-accelerator-ontop:test
NETWORK=ontop-check-net
PG=ontop-check-pg
ONTOP=ontop-check-ontop
# **自分でポートを選ぶ。** 既存の開発環境 (3131 / 5432 / 10000) と
# 衝突しない帯にしてある。
PORT=${ONTOP_CHECK_PORT:-18099}
# ローカル専用の値。**この検査の中だけで使い捨てる。**
PG_SECRET=ontopcheck

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)

if command -v cygpath >/dev/null 2>&1; then
    HOST_HERE=$(cygpath -m "$HERE")
    HOST_WORK=$(cygpath -m "$WORK")
else
    HOST_HERE=$HERE
    HOST_WORK=$WORK
fi

# **捨て先は実ファイルにする。`/dev/null` を curl に渡してはいけない。**
# このスクリプトは docker のために `MSYS_NO_PATHCONV=1` を export して
# いるので、Windows の curl に `/dev/null` がそのまま渡る。すると
# **リクエストは成功して `%{http_code}` も出るのに、終了コードが 23
# (書き込みエラー)になる**(実測)。`set -e` の下では、成功した直後に
# スクリプトが死ぬ。**失敗の場所が原因を指さない。**
DISCARD=$HOST_WORK/discard

fail=0
note() { printf '%s\n' "$*"; }
bad() { printf 'NG: %s\n' "$*"; fail=1; }

cleanup() {
    # **必ず片付ける。** 残ったコンテナがポートを掴むと、次の実行が
    # 原因の分からない失敗になる。
    docker rm -f "$ONTOP" >/dev/null 2>&1 || true
    docker rm -f "$PG" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

note "イメージをビルドします ($IMAGE)"
docker build -q -t "$IMAGE" "$HOST_HERE" >/dev/null

note "ネットワークと PostgreSQL を用意します"
docker network create "$NETWORK" >/dev/null
docker run -d --name "$PG" --network "$NETWORK" \
    -e POSTGRES_DB=vkgcheck -e POSTGRES_USER=vkgcheck \
    -e POSTGRES_PASSWORD="$PG_SECRET" \
    postgres:16-alpine >/dev/null

# **スリープを入れて待つ。** 密ループは CPU を食い潰し、待っている当の
# コンテナごと巻き込む(CLAUDE.md に記録済み)。
#
# **`pg_isready` では足りない。** postgres の公式イメージは initdb のために
# **一時的なサーバを起動して落とす**ので、そこで `pg_isready` が通ってしまう。
# 実測で、直後の psql が
# `FATAL: the database system is shutting down` で落ちた。
# **実際にクエリが通ることを条件にする。**
i=0
until docker exec "$PG" psql -q -U vkgcheck -d vkgcheck -c 'SELECT 1' >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 30 ]; then
        note "PostgreSQL が起動しませんでした"
        docker logs "$PG" 2>&1 | tail -20
        exit 1
    fi
    sleep 2
done

note "題材のスキーマを作ります"
docker exec -i "$PG" psql -q -U vkgcheck -d vkgcheck <"$HERE/testdata/schema.sql"

# **秘密はファイルで渡す。** `ONTOP_DB_PASSWORD_FILE` が実在することは
# `ontop endpoint --help` で確認済みで、これにより
# **プロパティファイルにも argv にもパスワードが載らない**
# (不変条件6 と同じ向きの判断)。
printf '%s' "$PG_SECRET" >"$WORK/dbsecret"

start_ontop() {
    # 第 1 引数: マッピングファイル名(`testdata/` の中)。
    docker rm -f "$ONTOP" >/dev/null 2>&1 || true
    docker run -d --name "$ONTOP" --network "$NETWORK" \
        -v "$HOST_HERE/testdata:/opt/ontop/input:ro" \
        -v "$HOST_WORK/dbsecret:/run/secrets/dbsecret:ro" \
        -e "ONTOP_MAPPING_FILE=/opt/ontop/input/$1" \
        -e ONTOP_ONTOLOGY_FILE=/opt/ontop/input/ontology.ttl \
        -e ONTOP_PROPERTIES_FILE=/opt/ontop/input/db.properties \
        -e "ONTOP_DB_URL=jdbc:postgresql://$PG:5432/vkgcheck" \
        -e ONTOP_DB_USER=vkgcheck \
        -e ONTOP_DB_PASSWORD_FILE=/run/secrets/dbsecret \
        -p "$PORT:8080" "$IMAGE" >/dev/null

    _i=0
    until [ "$(curl -s -o "$DISCARD" -w '%{http_code}' \
        "http://localhost:$PORT/actuator/health" 2>/dev/null)" = "200" ]; do
        _i=$((_i + 1))
        if [ "$_i" -gt 40 ]; then
            note "Ontop が起動しませんでした"
            docker logs "$ONTOP" 2>&1 | tail -30
            exit 1
        fi
        sleep 2
    done
}

# クエリを投げ、1 行目に HTTP のコード、2 行目以降に本文を置いて返す。
#
# **`--data-binary @<ファイル>` で渡す。** `-d '...'` は Git Bash で壊れる
# (CLAUDE.md に記録済み。配列を含む JSON で実測した)。
ask() {
    printf '%s' "$1" >"$WORK/q.rq"
    # **curl に渡すパスは Windows 形式にする。** このスクリプトは docker の
    # ために `MSYS_NO_PATHCONV=1` を export しているので、`/tmp/...` が
    # そのまま Windows の curl へ渡り
    # `error encountered when reading a file` になる(実測)。
    # CLAUDE.md の `az --file` と同じ罠である。
    _code=$(curl -s -o "$HOST_WORK/a.json" -w '%{http_code}' \
        -H 'Content-Type: application/sparql-query' \
        -H 'Accept: application/sparql-results+json' \
        --data-binary @"$HOST_WORK/q.rq" "http://localhost:$PORT/sparql")
    printf '%s\n' "$_code"
    cat "$WORK/a.json"
}

# 第 3 引数は Python の式。`rows` に束縛の一覧、`code` に HTTP のコード、
# `doc` に応答全体が入る。
assert_query() {
    _label=$1
    _query=$2
    _expr=$3
    _raw=$(ask "$_query")
    _code=$(printf '%s\n' "$_raw" | head -n 1)
    _json=$(printf '%s\n' "$_raw" | tail -n +2)
    if printf '%s' "$_json" | CODE="$_code" EXPR="$_expr" \
        uv run python -c '
import json, os, sys

from ontology_core.console import say

# **標準入力を bytes で読んで自分で UTF-8 として解釈する。** Windows の
# Python は標準入出力を cp932 で扱うため、json.load(sys.stdin) だと
# 日本語を含む JSON が壊れる(CLAUDE.md の cp932 の罠の入力側)。
#
# なお、この埋め込み Python のコメントにバッククォートを書いてはいけない。
# シェルの単一引用符の中にあると SC2016 で検査が落ちる(実測)。
expr = os.environ["EXPR"]
code = os.environ["CODE"]
raw = sys.stdin.buffer.read().decode("utf-8")
doc = json.loads(raw) if code == "200" else {}
rows = doc.get("results", {}).get("bindings", [])
scope = {"doc": doc, "rows": rows, "code": code, "raw": raw}
if not eval("(" + expr + ")", {}, scope):
    say("  条件が成立しませんでした: " + " ".join(expr.split()))
    say("  code=" + code)
    say("  " + raw[:1200])
    sys.exit(1)
'; then
        note "OK: $_label"
    else
        bad "$_label"
    fi
}

note "Ontop を起動します(正しいマッピング)"
start_ontop mapping.ttl

note ""
note "--- 1. 2 表をまたぐ結合が実データとして返る"
# **仮想グラフの要点そのもの。** SPARQL の結合が SQL の結合に書き換わり、
# 実データを実体化せずに返る。
assert_query "注文 3 件が顧客名つきで返る" \
    'PREFIX ex: <https://e.example/vkg#>
     SELECT ?order ?total ?name WHERE {
       ?order a ex:Order ; ex:totalJpy ?total ; ex:placedBy ?c .
       ?c ex:fullName ?name .
     } ORDER BY ?order' \
    'code == "200" and len(rows) == 3
     and rows[0]["order"]["value"].endswith("/order/10")
     and rows[0]["name"]["value"] == "田中 太郎"
     and rows[2]["name"]["value"] == "Sato Hanako"'

note ""
note "--- 2. TBox の推論が効く"
# `Customer ⊑ Party` を TBox に書いてある。**上位クラスで問い合わせて
# インスタンスが返れば、語彙の意味は仮想グラフ側でも効く**(決定1)。
assert_query "上位クラス Party で顧客 2 件が返る" \
    'PREFIX ex: <https://e.example/vkg#>
     SELECT ?x WHERE { ?x a ex:Party } ORDER BY ?x' \
    'code == "200" and len(rows) == 2
     and rows[0]["x"]["value"].endswith("/customer/1")'

note ""
note "--- 3. TBox のトリプル自体はデータとして返らない(決定11 の根拠)"
# **これが「宛先を間違えると 0 件が返る」の実測である。** 例外ではなく
# 空の結果なので、「該当する行が無い」と区別できない。だから Core API は
# 宛先を URL で分け、マッピングが無いソースを 404 で断る。
assert_query "owl:Class を問うと 0 件(エラーではない)" \
    'SELECT ?s WHERE { ?s a <http://www.w3.org/2002/07/owl#Class> }' \
    'code == "200" and len(rows) == 0'
assert_query "rdfs:label を問うと 0 件(TBox に label はあるのに)" \
    'SELECT ?s ?l WHERE { ?s <http://www.w3.org/2000/01/rdf-schema#label> ?l }' \
    'code == "200" and len(rows) == 0'

note ""
note "--- 4. SPARQL Update を受け付けない"
printf '%s' 'INSERT DATA { <urn:a> <urn:b> <urn:c> }' >"$WORK/u.rq"
code=$(curl -s -o "$DISCARD" -w '%{http_code}' \
    -H 'Content-Type: application/sparql-update' \
    --data-binary @"$HOST_WORK/u.rq" "http://localhost:$PORT/sparql")
if [ "$code" = "415" ]; then
    note "OK: /sparql は sparql-update を 415 で拒否する"
else
    bad "/sparql が sparql-update を受け付けた(HTTP $code)"
fi
for path in /update /sparql/update /statements; do
    code=$(curl -s -o "$DISCARD" -w '%{http_code}' -X POST \
        -H 'Content-Type: application/sparql-update' \
        --data-binary @"$HOST_WORK/u.rq" "http://localhost:$PORT$path")
    if [ "$code" = "404" ]; then
        note "OK: $path は存在しない"
    else
        bad "$path が応答した(HTTP $code)。書き込みの経路が開いている"
    fi
done

note ""
note "--- 5. ポータルページが出ない"
for path in / /index.html /ontology; do
    code=$(curl -s -o "$DISCARD" -w '%{http_code}' "http://localhost:$PORT$path")
    if [ "$code" = "404" ]; then
        note "OK: $path は 404"
    else
        bad "$path が応答した(HTTP $code)"
    fi
done

note ""
note "--- 6. 不正なマッピングのエラー本文は全関係を列挙する(決定10 の根拠)"
# **この検査は「漏れること」を確かめている。** 漏れるからこそ
# `VirtualGraphClient` が本文を捨てる。Ontop が列挙をやめたらここが落ちて、
# 決定10 の根拠が変わったことに気づける。
note "Ontop を起動し直します(存在しない関係を参照するマッピング)"
start_ontop mapping-bad-table.ttl
assert_query "エラー本文に他の関係名が入っている" \
    'SELECT * WHERE { ?s ?p ?o } LIMIT 1' \
    'code == "500" and "Cannot find relation" in raw
     and "available choices" in raw and "customer" in raw'

note ""
note "--- 秘密がプロパティファイルに載っていない"
# **`ONTOP_DB_PASSWORD_FILE` を使う理由の検算である。** プロパティ
# ファイルに秘密が無いまま接続できていることは、1〜3 が通ったことで
# 既に示されている。
if docker exec "$ONTOP" grep -qi 'password' /opt/ontop/input/db.properties; then
    bad "db.properties に password が書かれている"
else
    note "OK: db.properties に秘密が無い"
fi

note ""
if [ "$fail" -ne 0 ]; then
    note "失敗しました"
    exit 1
fi
note "すべて成功しました"
