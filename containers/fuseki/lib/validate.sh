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

# マニフェストの JSON として最低限の形をしているか検証する。
# `schema` が 1 であること、`namespace` が文字列であること、`versions` が
# 配列であることまでを見る(内容の正しさは呼び出し元の各関数が個別に見る)。
validate_manifest_json() {
    printf '%s' "$1" | jq -e \
        'type == "object" and (.schema == 1) and (.namespace | type == "string") and (.versions | type == "array")' \
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
