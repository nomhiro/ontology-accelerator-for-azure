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
# かった。Blob 名を "approved/alpha/../../evil.ttl" のように作ると
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
