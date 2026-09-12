/**
 * 差分の提示を決める(ADR-0044 決定1)。
 *
 * # なぜ純粋関数なのか
 *
 * **見た目は検証できないが、判断は検証できる。** ブラウザを持たないので
 * 描画結果は確かめられない。しかし「API が何と言ったときに何を見せるか」は
 * 純粋関数にできる(ADR-0044 決定6)。
 *
 * # なぜこれが最も重要な判断なのか
 *
 * [ADR-0016](../../../../docs/adr/0016-semantic-diff.md) 決定5 は、空白ノードが
 * 多すぎたら差分を**計算せず**「計算できなかった」と返すと決めた(rdflib の
 * 正規化は空白ノード 1,000 個で 42 秒かかる)。
 *
 * 画面が「追加 0 / 削除 0」と出したら、**レビュアは「変更が無い」と読む**。
 * 実際には「測っていない」である。**承認の判断が逆になる。**
 *
 * API がここまで丁寧に区別してきたものを、画面が最後に潰してはいけない。
 */

/** 差分の要約(`GET /namespaces/{ns}/versions/{v}/diff` の `diff`)。 */
export interface DiffSummary {
  empty: boolean;
  /** `"exact"` か `"skipped-too-many-blank-nodes"`。 */
  triple_status: string;
  blank_node_count: number;
  has_removed_terms: boolean;
  /** **計算できなかったときは `null`。** 0 ではない。 */
  added_triple_count: number | null;
  removed_triple_count: number | null;
  /** 用語の一覧を `SUMMARY_MAX_TERMS` で切ったか。 */
  truncated: boolean;
  added_terms: string[];
  added_term_count: number;
  removed_terms: string[];
  removed_term_count: number;
  deprecated_terms: string[];
  deprecated_term_count: number;
  /** **計算できなかったときは `null`。** 空配列ではない。 */
  modified_terms: string[] | null;
  modified_term_count: number | null;
}

/** `GET .../diff` の応答。 */
export interface DiffResponse {
  /** **基準の版が無ければ `null`**(最初の版)。 */
  diff: DiffSummary | null;
  owners: unknown[];
}

/** 差分が計算されなかった理由。 */
export const NOT_COMPUTED_STATUS = "skipped-too-many-blank-nodes";

/**
 * 画面に出す差分の状態。
 *
 * **`empty` と `notComputed` を別の値にしているのがこのモジュールの要点で
 * ある。** 同じ型に畳むと、コンポーネントの三項演算子で区別が消える。
 */
export type DiffPresentation =
  | {
      /** 基準の版が無い(この名前空間の最初の版)。 */
      readonly kind: "no-base";
    }
  | {
      /** **差分を計算していない。** 空の差分ではない。 */
      readonly kind: "not-computed";
      readonly blankNodeCount: number;
      readonly message: string;
    }
  | {
      /** 計算した結果、変更が無かった。 */
      readonly kind: "empty";
    }
  | {
      /** 計算できて、変更があった。 */
      readonly kind: "changed";
      /** **最も目立たせるもの**(ADR-0044 決定4)。承認を止める。 */
      readonly removedTerms: readonly string[];
      readonly removedTermCount: number;
      readonly addedTerms: readonly string[];
      readonly addedTermCount: number;
      readonly deprecatedTerms: readonly string[];
      readonly deprecatedTermCount: number;
      /** **`null` は「測っていない」。** 空配列(0 件)と区別する。 */
      readonly modifiedTerms: readonly string[] | null;
      readonly modifiedTermCount: number | null;
      readonly addedTripleCount: number | null;
      readonly removedTripleCount: number | null;
      /** 一覧が切られているか。切られていたら件数のほうを信じる。 */
      readonly truncated: boolean;
    };

/**
 * API の応答から画面の状態を決める。
 *
 * **判定の順序に理由がある。**
 *
 * 1. `diff` が `null` なら基準が無い(最初の版)。**変更が無いのではない**
 * 2. `triple_status` が計算していないことを示すなら、そう言う。
 *    **このとき件数は見ない** — `added_triple_count` は `null` であり、
 *    `empty` も `false` になる(`diff.py` の `is_empty` が
 *    「`None` なら `False`」にしてある)が、**そこに頼らない**。
 *    状態を直接見る
 * 3. `empty` なら「変更なし」と明記する
 * 4. それ以外は変更の中身を出す
 */
export function presentDiff(response: DiffResponse): DiffPresentation {
  const diff = response.diff;
  if (diff === null) {
    return { kind: "no-base" };
  }
  if (diff.triple_status === NOT_COMPUTED_STATUS) {
    return {
      kind: "not-computed",
      blankNodeCount: diff.blank_node_count,
      message:
        `空白ノードが ${diff.blank_node_count} 個あるため差分を計算していません。` +
        "**変更が無いという意味ではありません。** " +
        "承認の判断材料が揃っていないので、必要なら版の内容を直接比較してください。",
    };
  }
  if (diff.empty) {
    return { kind: "empty" };
  }
  return {
    kind: "changed",
    removedTerms: diff.removed_terms,
    removedTermCount: diff.removed_term_count,
    addedTerms: diff.added_terms,
    addedTermCount: diff.added_term_count,
    deprecatedTerms: diff.deprecated_terms,
    deprecatedTermCount: diff.deprecated_term_count,
    modifiedTerms: diff.modified_terms,
    modifiedTermCount: diff.modified_term_count,
    addedTripleCount: diff.added_triple_count,
    removedTripleCount: diff.removed_triple_count,
    truncated: diff.truncated,
  };
}

/**
 * 消えた用語があるか(ADR-0044 決定4)。
 *
 * **`approve` が 422 で止める条件である**(不変条件8、ADR-0017 決定2)。
 * 押した後に分かるより、押す前に分かるほうがよい。
 *
 * **計算できていないときは `false` を返さない。** `null` を返して
 * 「分からない」を伝える — `false` にすると「消えた用語は無い」と
 * 読まれる。
 */
export function hasRemovedTerms(presentation: DiffPresentation): boolean | null {
  switch (presentation.kind) {
    case "changed":
      return presentation.removedTermCount > 0;
    case "empty":
      return false;
    case "no-base":
      // 最初の版なので、消える対象が存在しない。
      return false;
    case "not-computed":
      // **測っていないので言えない。**
      return null;
  }
}
