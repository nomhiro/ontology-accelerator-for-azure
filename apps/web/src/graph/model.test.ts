/**
 * SPARQL の結果からグラフへ(ADR-0044 決定5)。
 *
 * 固定するのは 3 つ。
 *
 * 1. **切り詰めたら「全部は描いていない」と言える**(黙って切らない)
 * 2. **片方の端が消えた辺を残さない**(cytoscape が落ちる)
 * 3. **`draft` は描けないと分かる**(射影されていないため)
 */

import { describe, expect, it } from "vitest";

import {
  DRAFT_NOT_PROJECTED,
  type SparqlResults,
  isProjected,
  shortLabel,
  toGraphModel,
  truncationNotice,
} from "./model";

function results(triples: [string, string, string][], objectType = "uri"): SparqlResults {
  return {
    head: { vars: ["s", "p", "o"] },
    results: {
      bindings: triples.map(([s, p, o]) => ({
        s: { type: "uri", value: s },
        p: { type: "uri", value: p },
        o: { type: objectType, value: o },
      })),
    },
  };
}

const EX = "https://e.example/#";

describe("toGraphModel", () => {
  it("主語と目的語をノードに、述語を辺にする", () => {
    const model = toGraphModel(
      results([
        [`${EX}Product`, `${EX}hasCategory`, `${EX}Category`],
      ]),
    );
    expect(model.nodes.map((n) => n.label).sort()).toEqual(["Category", "Product"]);
    expect(model.edges).toHaveLength(1);
    expect(model.edges[0]!.label).toBe("hasCategory");
    expect(model.truncated).toBe(false);
  });

  it("同じノードを重複させない", () => {
    const model = toGraphModel(
      results([
        [`${EX}A`, `${EX}p`, `${EX}B`],
        [`${EX}A`, `${EX}q`, `${EX}C`],
      ]),
    );
    expect(model.nodes).toHaveLength(3);
    expect(model.edges).toHaveLength(2);
  });

  it("リテラルを別扱いにする", () => {
    const model = toGraphModel(results([[`${EX}A`, `${EX}label`, "商品"]], "literal"));
    const literal = model.nodes.find((n) => n.id === "商品");
    expect(literal?.literal).toBe(true);
    expect(model.nodes.find((n) => n.id === `${EX}A`)?.literal).toBe(false);
  });

  it("上限で切り詰め、切ったと言える", () => {
    // **これが決定5 の本体である。** 黙って切ると、レビュアは
    // 「これがオントロジーの全体だ」と読む。
    const triples: [string, string, string][] = [];
    for (let i = 0; i < 50; i += 1) {
      triples.push([`${EX}S${i}`, `${EX}p`, `${EX}O${i}`]);
    }
    const model = toGraphModel(results(triples), { maxNodes: 10 });

    expect(model.truncated).toBe(true);
    expect(model.nodes).toHaveLength(10);
    expect(model.totalNodes).toBe(100);
    const notice = truncationNotice(model);
    expect(notice).not.toBeNull();
    expect(notice!).toContain("全部は描いていません");
    expect(notice!).toContain("100");
  });

  it("切っていなければ注意を出さない", () => {
    const model = toGraphModel(results([[`${EX}A`, `${EX}p`, `${EX}B`]]));
    expect(truncationNotice(model)).toBeNull();
  });

  it("片方の端が消えた辺を残さない", () => {
    // **存在しないノードを参照する辺は cytoscape を落とす。**
    // グラフが描けなくなるほうが、辺が減るより悪い。
    const triples: [string, string, string][] = [];
    for (let i = 0; i < 20; i += 1) {
      triples.push([`${EX}S${i}`, `${EX}p`, `${EX}O${i}`]);
    }
    const model = toGraphModel(results(triples), { maxNodes: 5 });

    const ids = new Set(model.nodes.map((n) => n.id));
    for (const edge of model.edges) {
      expect(ids.has(edge.source), `${edge.source} が居ない`).toBe(true);
      expect(ids.has(edge.target), `${edge.target} が居ない`).toBe(true);
    }
  });

  it("変数が足りない束縛を推測で埋めない", () => {
    const broken: SparqlResults = {
      head: { vars: ["s", "p", "o"] },
      results: {
        bindings: [
          { s: { type: "uri", value: `${EX}A` }, p: { type: "uri", value: `${EX}p` } },
          {
            s: { type: "uri", value: `${EX}B` },
            p: { type: "uri", value: `${EX}p` },
            o: { type: "uri", value: `${EX}C` },
          },
        ],
      },
    };
    const model = toGraphModel(broken);
    // 1 件目は飛ばす。**空の目的語ノードを作らない。**
    expect(model.edges).toHaveLength(1);
    expect(model.nodes.map((n) => n.id).sort()).toEqual([`${EX}B`, `${EX}C`]);
  });

  it("空の結果で落ちない", () => {
    const model = toGraphModel(results([]));
    expect(model.nodes).toHaveLength(0);
    expect(model.truncated).toBe(false);
    expect(truncationNotice(model)).toBeNull();
  });
});

describe("shortLabel", () => {
  it("# の後ろを使う", () => {
    expect(shortLabel("https://e.example/x#Product")).toBe("Product");
  });

  it("# が無ければ / の後ろを使う", () => {
    expect(shortLabel("https://e.example/x/Product")).toBe("Product");
  });

  it("# が末尾なら / の後ろに落ちる", () => {
    // 最初はここで「全体を返す」と書いていたが、**実装のほうが正しかった**。
    // `x#` のほうが短くて読める — ラベルの目的に合っている。
    expect(shortLabel("https://e.example/x#")).toBe("x#");
  });

  it("区切りが無ければ全体を返す", () => {
    // **例外を投げない。** 1 つのノードのせいでグラフが描けなくなるほうが悪い。
    expect(shortLabel("https://e.example/")).toBe("https://e.example/");
    expect(shortLabel("plain")).toBe("plain");
    expect(shortLabel("")).toBe("");
  });
});

describe("isProjected", () => {
  it("draft と rejected は射影されていない", () => {
    // **ADR-0010 決定1。** draft は Fuseki に現れない = エージェントから
    // 見えない。**空のグラフを出すのではなく、描けない理由を出す。**
    expect(isProjected("draft")).toBe(false);
    expect(isProjected("rejected")).toBe(false);
  });

  it("in-review 以降は射影されている", () => {
    expect(isProjected("in-review")).toBe(true);
    expect(isProjected("approved")).toBe(true);
    expect(isProjected("superseded")).toBe(true);
  });

  it("draft の説明に「エージェントからも見えない」が入る", () => {
    // 単に「描けません」では、設計上そうなっていることが伝わらない。
    expect(DRAFT_NOT_PROJECTED).toContain("エージェント");
    expect(DRAFT_NOT_PROJECTED).toContain("submit");
  });
});
