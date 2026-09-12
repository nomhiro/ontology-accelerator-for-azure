/**
 * 差分の提示(ADR-0044 決定1)。
 *
 * **このファイルでいちばん重要なのは
 * `test 計算していないことを「変更が無い」と同じ形にしない` である。**
 *
 * [ADR-0016](../../../../docs/adr/0016-semantic-diff.md) 決定5 が
 * わざわざ区別した「計算できなかった」を、画面が最後に潰さないことを固定する。
 */

import { describe, expect, it } from "vitest";

import {
  type DiffResponse,
  type DiffSummary,
  NOT_COMPUTED_STATUS,
  hasRemovedTerms,
  presentDiff,
} from "./diff";

function summary(overrides: Partial<DiffSummary> = {}): DiffSummary {
  return {
    empty: false,
    triple_status: "exact",
    blank_node_count: 3,
    has_removed_terms: false,
    added_triple_count: 4,
    removed_triple_count: 0,
    truncated: false,
    added_terms: ["https://e.example/#A"],
    added_term_count: 1,
    removed_terms: [],
    removed_term_count: 0,
    deprecated_terms: [],
    deprecated_term_count: 0,
    modified_terms: [],
    modified_term_count: 0,
    ...overrides,
  };
}

function response(diff: DiffSummary | null): DiffResponse {
  return { diff, owners: [] };
}

describe("presentDiff", () => {
  it("計算していないことを「変更が無い」と同じ形にしない", () => {
    // **これが ADR-0044 決定1 の本体である。**
    //
    // 空白ノードが多すぎて差分を計算しなかったとき、API は件数を `null` に
    // する(`diff.py` の `is_empty` も `False` になる)。画面が
    // 「追加 0 / 削除 0」と出すと、**レビュアは「変更が無い」と読む**。
    const notComputed = presentDiff(
      response(
        summary({
          triple_status: NOT_COMPUTED_STATUS,
          blank_node_count: 1200,
          added_triple_count: null,
          removed_triple_count: null,
          modified_terms: null,
          modified_term_count: null,
          empty: false,
        }),
      ),
    );
    const empty = presentDiff(response(summary({ empty: true })));

    expect(notComputed.kind).toBe("not-computed");
    expect(empty.kind).toBe("empty");
    // **同じ型に畳まれていない。**
    expect(notComputed.kind).not.toBe(empty.kind);
  });

  it("計算していないときは理由と件数を運ぶ", () => {
    const presentation = presentDiff(
      response(summary({ triple_status: NOT_COMPUTED_STATUS, blank_node_count: 1200 })),
    );
    if (presentation.kind !== "not-computed") {
      throw new Error("not-computed を期待した");
    }
    expect(presentation.blankNodeCount).toBe(1200);
    expect(presentation.message).toContain("1200");
    // **「変更が無い」と読ませない文言が必ず入る。**
    expect(presentation.message).toContain("変更が無いという意味ではありません");
  });

  it("計算していないときは件数を見ない", () => {
    // `empty` が `true` で来ても(API の実装が変わっても)、
    // **状態を直接見ているので「変更なし」にはならない。**
    const presentation = presentDiff(
      response(summary({ triple_status: NOT_COMPUTED_STATUS, empty: true })),
    );
    expect(presentation.kind).toBe("not-computed");
  });

  it("基準の版が無いことを「変更が無い」と混同しない", () => {
    // 最初の版は `diff` が `null` で返る。**0 件ではない。**
    expect(presentDiff(response(null)).kind).toBe("no-base");
  });

  it("変更があれば中身を運ぶ", () => {
    const presentation = presentDiff(
      response(
        summary({
          removed_terms: ["https://e.example/#Gone"],
          removed_term_count: 1,
          added_terms: ["https://e.example/#New"],
          added_term_count: 1,
          deprecated_terms: ["https://e.example/#Old"],
          deprecated_term_count: 1,
          modified_terms: ["https://e.example/#Changed"],
          modified_term_count: 1,
          removed_triple_count: 2,
        }),
      ),
    );
    if (presentation.kind !== "changed") {
      throw new Error("changed を期待した");
    }
    expect(presentation.removedTerms).toEqual(["https://e.example/#Gone"]);
    expect(presentation.deprecatedTerms).toEqual(["https://e.example/#Old"]);
    expect(presentation.modifiedTerms).toEqual(["https://e.example/#Changed"]);
    expect(presentation.removedTripleCount).toBe(2);
  });

  it("変更された用語の「測っていない」を 0 件にしない", () => {
    // **`modified_terms` は `null` になりうる**(`diff.py` の `summary()`)。
    // これを空配列に畳むと「変更された用語は無い」と読まれる。
    const presentation = presentDiff(
      response(summary({ modified_terms: null, modified_term_count: null })),
    );
    if (presentation.kind !== "changed") {
      throw new Error("changed を期待した");
    }
    expect(presentation.modifiedTerms).toBeNull();
    expect(presentation.modifiedTermCount).toBeNull();
  });

  it("一覧が切られていることを運ぶ", () => {
    const presentation = presentDiff(
      response(summary({ truncated: true, added_terms: ["a"], added_term_count: 500 })),
    );
    if (presentation.kind !== "changed") {
      throw new Error("changed を期待した");
    }
    // **件数と一覧が食い違うことを画面が知れる。**
    expect(presentation.truncated).toBe(true);
    expect(presentation.addedTerms).toHaveLength(1);
    expect(presentation.addedTermCount).toBe(500);
  });
});

describe("hasRemovedTerms", () => {
  it("消えた用語があれば true", () => {
    const presentation = presentDiff(
      response(summary({ removed_terms: ["x"], removed_term_count: 1 })),
    );
    expect(hasRemovedTerms(presentation)).toBe(true);
  });

  it("変更が無ければ false", () => {
    expect(hasRemovedTerms(presentDiff(response(summary({ empty: true }))))).toBe(false);
  });

  it("最初の版は false(消える対象が無い)", () => {
    expect(hasRemovedTerms(presentDiff(response(null)))).toBe(false);
  });

  it("計算していないときは false ではなく null", () => {
    // **`false` にすると「消えた用語は無い」と読まれる。**
    // 承認は 422 で止まりうるので、分からないと言わなければならない。
    const presentation = presentDiff(
      response(summary({ triple_status: NOT_COMPUTED_STATUS })),
    );
    expect(hasRemovedTerms(presentation)).toBeNull();
  });
});
