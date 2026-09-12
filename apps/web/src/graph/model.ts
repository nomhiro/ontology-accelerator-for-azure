/**
 * SPARQL の結果からグラフの要素を作る(ADR-0044 決定5)。
 *
 * # なぜ Turtle を解析しないのか
 *
 * **この製品が提供している口を使う。** SPARQL エンドポイントはエージェントに
 * 提供しているものそのもので、画面が同じ口を使えば**画面が動くことがその口の
 * 検証になる**。
 *
 * そして **`draft` はストアに無い**
 * ([ADR-0010](../../../../docs/adr/0010-approval-and-projection.md) 決定1)。
 * SPARQL で引けば**引けないことが分かる** — これは欠点ではなく
 * **設計の可視化**である。生成された候補(`P2A-02`)は `draft` なので
 * グラフを描けない。**空のグラフを出すのではなく、描けない理由を出す。**
 *
 * # 上限を超えたら「全部は描いていない」と言う
 *
 * [ADR-0034](../../../../docs/adr/0034-construct-describe.md) 決定4 は
 * RDF を切り詰めることを拒んだ — **Turtle に「一部である」と書く場所が
 * 無い**(封筒が無い)ためである。
 *
 * **グラフには封筒がある。** 「1,000 ノードのうち 200 を描いています」と
 * 画面に書ける。だから切り詰めてよい。**ただし黙ってはいけない。**
 */

/** SPARQL の 1 つの束縛値。 */
export interface SparqlTerm {
  type: string;
  value: string;
}

/** SPARQL SELECT の応答(`?s ?p ?o` を期待する)。 */
export interface SparqlResults {
  head: { vars: string[] };
  results: { bindings: Record<string, SparqlTerm>[] };
}

/** 描画するノード。 */
export interface GraphNode {
  readonly id: string;
  /** 表示名。IRI の末尾(`#` か `/` の後ろ)。 */
  readonly label: string;
  /** リテラルか。リテラルは別の見た目にする。 */
  readonly literal: boolean;
}

/** 描画する辺。 */
export interface GraphEdge {
  readonly id: string;
  readonly source: string;
  readonly target: string;
  readonly label: string;
}

export interface GraphModel {
  readonly nodes: readonly GraphNode[];
  readonly edges: readonly GraphEdge[];
  /** **切り詰めたか。** 切ったら必ず画面に出す。 */
  readonly truncated: boolean;
  /** 切る前のノード数。`truncated` のときに「全体のうち何個」を言うため。 */
  readonly totalNodes: number;
  /** 切る前の辺の数。 */
  readonly totalEdges: number;
}

/** 既定の上限。これを超えたら切り詰めて、切ったと言う。 */
export const GRAPH_MAX_NODES = 200;

/**
 * IRI から表示名を作る。
 *
 * **失敗しても例外を投げない。** グラフが 1 つのノードのせいで
 * 描けなくなるほうが悪い。
 */
export function shortLabel(value: string): string {
  const hash = value.lastIndexOf("#");
  if (hash >= 0 && hash < value.length - 1) {
    return value.slice(hash + 1);
  }
  const slash = value.lastIndexOf("/");
  if (slash >= 0 && slash < value.length - 1) {
    return value.slice(slash + 1);
  }
  return value;
}

/**
 * 束縛からグラフを作る。
 *
 * **ノードの上限で切る。辺は残ったノードの間のものだけを残す。**
 * 片方の端が消えた辺を残すと、cytoscape が存在しないノードを参照して
 * 落ちる(**グラフが描けなくなるほうが、辺が減るより悪い**)。
 *
 * `?s ?p ?o` 以外の変数名は無視する。**足りなければその束縛を飛ばす** —
 * 推測で埋めない。
 */
export function toGraphModel(
  results: SparqlResults,
  options: { readonly maxNodes?: number } = {},
): GraphModel {
  const maxNodes = options.maxNodes ?? GRAPH_MAX_NODES;
  const nodes = new Map<string, GraphNode>();
  const rawEdges: GraphEdge[] = [];

  for (const [index, binding] of results.results.bindings.entries()) {
    const subject = binding["s"];
    const predicate = binding["p"];
    const object = binding["o"];
    if (!subject || !predicate || !object) {
      // **推測で埋めない。** `?s ?p ?o` が揃っていない束縛は飛ばす。
      continue;
    }
    register(nodes, subject);
    register(nodes, object);
    rawEdges.push({
      id: `e${index}`,
      source: subject.value,
      target: object.value,
      label: shortLabel(predicate.value),
    });
  }

  const totalNodes = nodes.size;
  const totalEdges = rawEdges.length;
  if (totalNodes <= maxNodes) {
    return {
      nodes: [...nodes.values()],
      edges: rawEdges,
      truncated: false,
      totalNodes,
      totalEdges,
    };
  }

  const kept = new Set([...nodes.keys()].slice(0, maxNodes));
  return {
    nodes: [...nodes.values()].filter((node) => kept.has(node.id)),
    // **両端が残っている辺だけを残す。** 存在しないノードを参照する辺は
    // cytoscape を落とす。
    edges: rawEdges.filter((edge) => kept.has(edge.source) && kept.has(edge.target)),
    truncated: true,
    totalNodes,
    totalEdges,
  };
}

function register(nodes: Map<string, GraphNode>, term: SparqlTerm): void {
  if (nodes.has(term.value)) {
    return;
  }
  nodes.set(term.value, {
    id: term.value,
    label: shortLabel(term.value),
    literal: term.type === "literal" || term.type === "typed-literal",
  });
}

/**
 * 切り詰めたことを伝える文(ADR-0044 決定5)。
 *
 * 切っていなければ `null`。
 */
export function truncationNotice(model: GraphModel): string | null {
  if (!model.truncated) {
    return null;
  }
  return (
    `**全部は描いていません。** ノード ${model.totalNodes} 個のうち ` +
    `${model.nodes.length} 個、辺 ${model.totalEdges} 本のうち ${model.edges.length} 本を` +
    "描いています。全体を見るには SPARQL で絞り込んでください。"
  );
}

/** `draft` を描けない理由(ADR-0044 決定5)。 */
export const DRAFT_NOT_PROJECTED =
  "**この版は draft のため、グラフを描けません。** draft は正本(Blob と PostgreSQL)" +
  "にだけ存在し、トリプルストアには射影されていません(ADR-0010 決定1)。" +
  "つまり AI エージェントからも見えません。`submit` すると名前付きグラフに載り、" +
  "ここで描けるようになります。";

/**
 * その状態の版がストアにあるか。
 *
 * `draft` と `rejected` は射影されていない。**「描けない」と「空である」を
 * 区別するために使う。**
 */
export function isProjected(status: string): boolean {
  return status === "in-review" || status === "approved" || status === "superseded";
}
