# shellcheck shell=sh
# 名前空間名・バージョン文字列の検証。
#
# load-snapshot.sh から source して使う。load-snapshot.sh 本体は Blob 一覧の
# 取得や TDB2 の構築など副作用のあるコードをトップレベルに持つため、そのまま
# source すると検証関数だけを取り出してテストすることができない。検証だけを
# ここに切り出しておき、containers/fuseki/lib/validate.test.sh から直接
# source して呼べるようにしてある。
#
# このファイルは source されることだけを想定している(実行可能属性は付けない)。
# log() もここに置く。load-snapshot.sh 側と検証関数の両方から使うため。

log() { echo "load-snapshot: $*" >&2; }

# 名前空間名を検証する。Fuseki のデータセット名・ファイルシステムのパスに
# 使うため、想定外の文字(パストラバーサル等)が混じっていないか確認する。
# 正本の書き込み時点で ontology_core.graphs.validate_namespace_name が検証済み
# のはずだが、ここでも確認するのは多層防御であり、Blob の内容を無条件に
# 信用しないためでもある。
validate_namespace() {
    ns="$1"
    case "${ns}" in
        [a-z0-9][a-z0-9-]*) ;;
        *)
            log "不正な名前空間名をスキップします: ${ns}"
            return 1
            ;;
    esac
    case "${ns}" in
        *[!a-z0-9-]*)
            log "不正な文字を含む名前空間名をスキップします: ${ns}"
            return 1
            ;;
    esac
    if [ "${#ns}" -lt 2 ] || [ "${#ns}" -gt 63 ]; then
        log "名前空間名の長さが不正です: ${ns}"
        return 1
    fi
    if [ "${ns}" = "ds" ]; then
        log "予約された名前空間名をスキップします: ${ns}"
        return 1
    fi
    return 0
}

# Blob の一覧から得たバージョンファイル名(例: "1.0.0.ttl")を検証する。
#
# namespace は validate_namespace が「/」を含めない文字種に制限しているが、
# version_file 側(BLOB_PREFIX と namespace を取り除いた残り)は検証していな
# かった。Blob 名を "versions/alpha/../../evil.ttl" のように作ると
# namespace="alpha"(検証を通過)、version_file="../../evil.ttl" となり、
# curl -o が STAGING_DIR の外へ任意のファイルを書き込めてしまう
# (load-snapshot.sh はこのファイル自身のコメントで「Blob の内容を無条件に
# 信用しない」と明言している対象そのもの)。
# ontology_core.graphs.validate_version の文字種(英数字と . + -)に合わせ、
# 「/」や「..」を含む値を拒否する。
validate_version_file() {
    vf="$1"
    case "${vf}" in
        *.ttl) ;;
        *)
            log "拡張子が .ttl ではない Blob をスキップします: ${vf}"
            return 1
            ;;
    esac
    version="${vf%.ttl}"
    # POSIX の glob には量指定子(`{0,63}` 相当)が無く、`[A-Za-z0-9.+-]*` は
    # 「そのクラスの 1 文字 + 任意の文字が 0 個以上」を意味してしまい、
    # 2 文字以上を要求する形になる(1 文字のバージョン "1" や "a" を拒否してしまう
    # バグがあった)。ontology_core.graphs._VERSION_PATTERN
    # (`[A-Za-z0-9][A-Za-z0-9.+-]{0,63}`、1 文字から許可)に合わせるため、
    # 1 文字だけの場合と 2 文字以上の場合を別パターンとして両方許可する。
    case "${version}" in
        [A-Za-z0-9]) ;;                # 1 文字
        [A-Za-z0-9][A-Za-z0-9.+-]*) ;; # 2 文字以上(先頭は英数字、以降は . + - も可)
        *)
            log "不正なバージョン文字列を含む Blob をスキップします: ${vf}"
            return 1
            ;;
    esac
    case "${version}" in
        *[!A-Za-z0-9.+-]*)
            log "不正な文字を含むバージョン文字列をスキップします: ${vf}"
            return 1
            ;;
    esac
    if [ "${#version}" -lt 1 ] || [ "${#version}" -gt 64 ]; then
        log "バージョン文字列の長さが不正です: ${vf}"
        return 1
    fi
    return 0
}

# GRAPH_IRI_BASE の末尾スラッシュをすべて除去して返す。
#
# ontology_core.graphs.version_graph_iri は `base.rstrip('/')` してから
# namespace/version を連結する。load-snapshot.sh の build_namespace_tdb は
# 従来正規化せずに連結していたため、末尾スラッシュ付きの値(例:
# "urn:ontology:graph/")を渡すと射影(Python 側)は
# ".../graph/ns/1.0.0"、再構築(シェル側)は ".../graph//ns/1.0.0" になり、
# レプリカ再作成後に GRAPH で名指ししたバージョン固定クエリが静かに 0 件になる
# (ブランチ全体レビュー I-4 追加分)。POSIX の `${var%/}` は 1 個しか
# 除去しないため、Python の rstrip('/') に合わせてループで全部除去する。
normalize_graph_iri_base() {
    value="$1"
    while [ "${value%/}" != "${value}" ]; do
        value="${value%/}"
    done
    printf '%s' "${value}"
}

# BLOB_PREFIX の末尾スラッシュをすべて除去したうえで、必ず 1 個だけ付けて返す。
#
# ontology_core.blob.blob_path_for は `f"{prefix.rstrip('/')}/{namespace}/..."`
# で常に区切りのスラッシュを 1 個保証する。load-snapshot.sh の Blob 一覧処理は
# `"${name#"${BLOB_PREFIX}"}"` で前方一致除去するだけだったため、末尾スラッシュ
# 無しの値(例: "versions")を渡すと Blob 名 "versions/ns/1.0.0.ttl" から
# 除去されるのは "versions" だけになり、残り "/ns/1.0.0.ttl" の先頭が
# namespace の外(スラッシュ)に来て namespace="" と誤認識され、
# 名前空間の階層が無い Blob として全件スキップされる(同上 I-4 追加分)。
normalize_blob_prefix() {
    value="$1"
    while [ "${value%/}" != "${value}" ]; do
        value="${value%/}"
    done
    printf '%s/' "${value}"
}

# ---------------------------------------------------------------------------
# 承認状態マニフェスト(versions/<namespace>/_state.json、ADR-0010 決定7)の
# 解析。ローダは PostgreSQL を見ないため、状態はこのマニフェストだけから知る。
#
# 実行には jq が要る(Dockerfile で導入済み)。マニフェストが取得できない・
# 壊れている名前空間は「黙って全件承認済みとして扱う」のではなく、呼び出し元
# (load-snapshot.sh の build_tdb)がこれらの関数で判定し、その名前空間の
# 読み込みを丸ごとスキップする(修正5)。
# ---------------------------------------------------------------------------

# このローダが解釈できるマニフェストの schema(`P2B-C1`)。
#
# **書き手(Python の `_build_manifest`)が出す値を必ず含めること。** ここに
# 無い schema は「不正な形式」として名前空間ごとスキップされる。ADR-0019
# (`P2B-02`)が schema を 2 に上げたとき**ここを 1 のままにしていたため、
# ローダが全名前空間をスキップしていた**(実測)。射影は再構築可能である
# という不変条件1 の前提が、その間だけ成り立っていなかった。
#
# **契約の一致は `packages/api/tests/test_manifest_contract.py` が機械的に
# 検査する。** 片側だけ直して食い違う事故を二度と起こさないため。
MANIFEST_SCHEMAS="1 2"

# マニフェストの JSON として最低限の形をしているか検証する。
# `schema` が既知であること、`namespace` が文字列であること、`versions` が
# 配列であることまでを見る(内容の正しさは呼び出し元の各関数が個別に見る)。
#
# **未知の(新しい)schema は受け付けない。** そのマニフェストが運んでいる
# 指示をこのローダが知らないので、知っている規則だけで射影すると
# 「新しい指示を黙って無視した射影」になる。**知らないものを推測しない**
# のがこのローダの一貫した方針である(`skip:unknown-status-*` と同じ)。
#
# **その代わり、安全に関わる指示を新しい schema にだけ載せてはいけない。**
# 古いローダが無視すると事故になるものは、既存の欄(`projection` など)で
# 表現すること — 退役(`P2B-19`)が `projection` の `skip:` を使うのは
# この理由である。
#
# **`schema` は数値でなければならない。** `"2"`(文字列)を受け入れると、
# `jq -r` が数値と文字列を同じ出力にするため型の壊れたマニフェストが通る。
# 書き手は必ず数値を出すので、文字列が来ているのは書き手が壊れている合図
# であり、推測して読み込む理由が無い。
validate_manifest_json() {
    # `.schema` を `$s` に束縛してから照合する。**`index()` の引数の中では
    # `.` が配列に差し替わっている**ので、そこに `.schema` と書くと常に
    # `null` になる(実測で全件 reject になった)。
    printf '%s' "$1" | jq -e --arg allowed "${MANIFEST_SCHEMAS}" \
        'type == "object" and ((.schema | type) == "number")
         and (.schema as $s | ($allowed | split(" ") | index($s | tostring)) != null)
         and (.namespace | type == "string") and (.versions | type == "array")' \
        >/dev/null 2>&1
}

# マニフェストの `current`(承認済み現行版。無ければ空文字)を返す。
manifest_current() {
    printf '%s' "$1" | jq -r '.current // ""'
}

# マニフェストの `versions` から、指定した版の `status` を返す。
# 見つからなければ空文字(= draft の可能性。呼び出し元は「マニフェストに
# 載っていない版は読み込まない」という方針でこれを扱う)。
manifest_status_for_version() {
    printf '%s' "$1" | jq -r --arg v "$2" '(.versions[] | select(.version == $v) | .status) // ""'
}

# マニフェストの `versions` から、指定した版の `projection` を返す(ADR-0019 決定1)。
# 見つからない、または schema 1 のマニフェスト(この欄が無い)なら空文字。
#
# **schema 2 から、保持ポリシーの判断は正本側(Python)で行い、判断済みの結果を
# この欄で運ぶ。** ローダは状態を再判定しない。以前は判断がこのファイルの
# projection_targets にあり、`reconcile` は食い違いを避けて superseded の在否を
# 不問にしていた。そのため保持ポリシーを誰も強制していなかった。
manifest_projection_for_version() {
    printf '%s' "$1" | jq -r --arg v "$2" '(.versions[] | select(.version == $v) | .projection) // ""'
}

# 版の射影先を決める(ADR-0010 決定5、P1-18、ADR-0019 決定1)。
#
# 状態別の振り分けは元々 load-snapshot.sh の build_namespace_tdb に case 文
# としてインラインで埋まっていて、再実行可能なテストが無かった。判断を I/O
# (tdbloader の実行)から切り離すため、副作用の無い純粋関数としてここに
# 置く。build_namespace_tdb はこの関数の出力を解釈するだけにし、状態を
# 判定する case 文は持たない(判断が二箇所に存在すると、片方だけ直して
# 食い違う)。
#
# 引数: manifest(_state.json の内容), version(版文字列),
#       retain(SUPERSEDED_RETAIN の値)
# 標準出力に返す(空白区切り。case 文で状態を再判定させないため、
# 呼び出し側は文字列全体の一致で見分ける):
#   "named default"  … 名前付きグラフと既定グラフの両方へ読み込む
#   "named"          … 名前付きグラフのみへ読み込む
#   "skip:<理由>"    … 読み込まない(この版は丸ごとスキップする)。理由の値:
#                        superseded-retain-0   … superseded かつ SUPERSEDED_RETAIN=0
#                        not-in-manifest       … マニフェストに載っていない(draft の可能性)
#                        unknown-status-<状態> … ローダが知らない状態
#
# **スキップは空文字ではなく理由付きで返す(P1-22)。** 振り分けをこの関数に
# 切り出した結果、呼び出し元(build_namespace_tdb)に判断が無くなり、
# 「superseded だからスキップした」と「マニフェストに無いからスキップした」を
# 区別する情報がその場から失われた。旧コードは理由別にログを出していた。
# 判断を 1 箇所に保ったまま呼び出し元が理由をログに出せるようにする。
# **空文字は返さない。** 空文字を返すと、呼び出し元でうっかり未設定の変数と
# 区別が付かなくなり、「静かに読み込まれない」状態に戻る。
projection_targets() {
    manifest="$1"
    version="$2"
    retain="$3"

    # **マニフェストが判断済みならそれに従う**(ADR-0019 決定1)。
    # 保持ポリシーの入力(`approved_at` の順序)はマニフェストに無いので、
    # ここで「直近 N 版」を決めることはできない。決めるのは正本側である。
    decided="$(manifest_projection_for_version "${manifest}" "${version}")"
    if [ -n "${decided}" ]; then
        printf '%s' "${decided}"
        return 0
    fi

    # ---- 以下は後方互換のための経路(schema 1 のマニフェスト) ----
    #
    # `projection` を持たないマニフェストが残っている窓(デプロイの入れ替え中、
    # `reconcile` を回す前)のために、従来の状態ベースの判断に落ちる。
    # **`SUPERSEDED_RETAIN` はここでしか効かない**(しかも個数ではなく
    # 真偽値としてしか効かない。これが ADR-0019 で直した元の不具合である)。
    # **黙って落ちない** — 呼び出し元が警告を出せるよう、理由を返す値に
    # `legacy` を含めない代わりに、load-snapshot.sh 側でこの経路を検出して
    # ログに出す(manifest_projection_for_version が空を返したことで分かる)。
    status="$(manifest_status_for_version "${manifest}" "${version}")"
    case "${status}" in
        approved)
            current="$(manifest_current "${manifest}")"
            if [ "${version}" = "${current}" ]; then
                printf 'named default'
            else
                # current は 1 つしかない。他に approved が残っていても
                # (本来起こらないはずだが)既定グラフには載せない。将来
                # current の扱いが変わったときに気づけるよう、明示的に
                # テストしておく(validate.test.sh)。
                printf 'named'
            fi
            ;;
        in-review)
            printf 'named'
            ;;
        superseded)
            if [ "${retain}" = "0" ]; then
                printf 'skip:superseded-retain-0'
            else
                printf 'named'
            fi
            ;;
        '')
            # マニフェストに載っていない版(draft の可能性)。読み込まない
            # (ADR-0010 決定5。推測は Critical(P1-C1)を再来させる)。
            printf 'skip:not-in-manifest'
            ;;
        *)
            # ローダが知らない状態。将来 API 側が新しい状態を導入したときに、
            # 黙って読み込まないのではなく状態名を添えて言えるようにする
            # (「静かに間違う」を避ける。推測して読み込むことは絶対にしない)。
            printf 'skip:unknown-status-%s' "${status}"
            ;;
    esac
}
