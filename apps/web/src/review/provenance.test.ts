/**
 * LLM 生成の出自の読み取り(ADR-0044 決定7)。
 *
 * # Python 側との結合を固定する
 *
 * ここが読む文字列は **Python の `ProposalResult.provenance()`** が作る。
 * **両側にテストを置いてある** — Python 側は
 * `packages/api/tests/test_proposal_service.py`、こちら側がこれ。
 *
 * 下の `REAL_REASON` は**実機で実際に監査に入った文字列**である
 * (2026-09-13 のデプロイ窓。`GET .../versions/2.0.0/decisions` から取った)。
 * フェイクではなく、実物を固定している。
 */

import { describe, expect, it } from "vitest";

import { GENERATION_MARKER, provenanceBanner, readProvenance } from "./provenance";

/** 実機の監査に入っていた文字列(2026-09-13)。 */
const REAL_REASON =
  "実機確認: カタログから候補を生成する\n" +
  "LLM による生成(model=gpt-4.1、deployment=ontology-proposer、" +
  "api-version=2024-10-21、試行 1 回)。**人間のレビューを経ていない候補である**";

describe("readProvenance", () => {
  it("実機の監査の文字列を読める", () => {
    // **これが結合の検証である。** Python 側の文言を変えるとここが落ちる。
    const provenance = readProvenance(REAL_REASON);
    expect(provenance.generated).toBe(true);
    expect(provenance.model).toBe("gpt-4.1");
    expect(provenance.deployment).toBe("ontology-proposer");
    expect(provenance.attempts).toBe(1);
    expect(provenance.struggled).toBe(false);
  });

  it("人が書いた版は生成扱いにしない", () => {
    const provenance = readProvenance("小売ドメインの用語を追加した");
    expect(provenance.generated).toBe(false);
    expect(provenance.attempts).toBeNull();
  });

  it("理由が無くても落ちない", () => {
    expect(readProvenance(null).generated).toBe(false);
    expect(readProvenance(undefined).generated).toBe(false);
    expect(readProvenance("").generated).toBe(false);
  });

  it("3 回以上かかったことを信号として扱う", () => {
    // **モデルがこのスキーマを扱いかねている**(ADR-0043 決定4)。
    const provenance = readProvenance(`${GENERATION_MARKER}(model=gpt-4.1、試行 3 回)`);
    expect(provenance.attempts).toBe(3);
    expect(provenance.struggled).toBe(true);
  });

  it("回数が読めなければ null にする。false に畳まない", () => {
    // **分からないことを「大丈夫」に変えてはいけない。**
    const provenance = readProvenance(`${GENERATION_MARKER}(model=gpt-4.1)`);
    expect(provenance.generated).toBe(true);
    expect(provenance.attempts).toBeNull();
    expect(provenance.struggled).toBeNull();
  });
});

describe("provenanceBanner", () => {
  it("生成でなければ帯を出さない", () => {
    expect(provenanceBanner(readProvenance("人が書いた"))).toBeNull();
  });

  it("人間のレビューを経ていないことを最初に言う", () => {
    const banner = provenanceBanner(readProvenance(REAL_REASON));
    expect(banner).not.toBeNull();
    expect(banner!).toContain("人間のレビューを経ていません");
    expect(banner!).toContain("gpt-4.1");
    expect(banner!).toContain("1 回");
  });

  it("3 回以上かかったことを帯に書く", () => {
    const banner = provenanceBanner(readProvenance(`${GENERATION_MARKER}(試行 4 回)`));
    expect(banner!).toContain("3 回以上");
  });

  it("回数が読めなかったことを「1 回」と書かない", () => {
    // **読めなかったのだから読めなかったと書く。**
    const banner = provenanceBanner(readProvenance(`${GENERATION_MARKER}(model=x)`));
    expect(banner!).toContain("読み取れませんでした");
    expect(banner!).not.toContain("1 回");
  });
});
