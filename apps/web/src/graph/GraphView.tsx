/**
 * オントロジーのグラフを描く(ADR-0044 決定5、`P2A-04`)。
 *
 * **データは SPARQL から引く。** Turtle をブラウザで解析しない — この製品が
 * エージェントに提供している口をそのまま使う。**画面が動くことがその口の
 * 検証になる。**
 *
 * **`draft` は描けない。** ストアに射影されていないため
 * ([ADR-0010](../../../../docs/adr/0010-approval-and-projection.md) 決定1)。
 * 空のグラフを出すのではなく、**描けない理由**を出す。
 *
 * **描画は検証できていない**(ADR-0044 の受け入れるコスト)。ブラウザを
 * 持たないので、確かめたのは `model.ts` の純粋関数と型検査・ビルドまでである。
 */

import { Body1, MessageBar, MessageBarBody, makeStyles, tokens } from "@fluentui/react-components";
import cytoscape from "cytoscape";
import { useEffect, useRef } from "react";

import { type GraphModel, truncationNotice } from "./model";

const useStyles = makeStyles({
  stack: {
    display: "flex",
    flexDirection: "column",
    rowGap: tokens.spacingVerticalS,
  },
  canvas: {
    height: "28rem",
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground1,
  },
});

export interface GraphViewProps {
  readonly model: GraphModel;
}

export function GraphView({ model }: GraphViewProps) {
  const styles = useStyles();
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = container.current;
    if (!element) {
      return;
    }
    const instance = cytoscape({
      container: element,
      elements: [
        ...model.nodes.map((node) => ({
          data: { id: node.id, label: node.label, literal: node.literal ? "1" : "0" },
        })),
        ...model.edges.map((edge) => ({
          data: { id: edge.id, source: edge.source, target: edge.target, label: edge.label },
        })),
      ],
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "font-size": "9px",
            "background-color": tokens.colorBrandBackground,
            color: tokens.colorNeutralForeground1,
            width: 18,
            height: 18,
          },
        },
        {
          // リテラルは別の見た目にする(用語と値を混同させない)。
          selector: 'node[literal = "1"]',
          style: {
            shape: "round-rectangle",
            "background-color": tokens.colorNeutralBackground4,
          },
        },
        {
          selector: "edge",
          style: {
            label: "data(label)",
            "font-size": "8px",
            width: 1,
            "line-color": tokens.colorNeutralStroke2,
            "target-arrow-shape": "triangle",
            "target-arrow-color": tokens.colorNeutralStroke2,
            "curve-style": "bezier",
          },
        },
      ],
      layout: { name: "cose", animate: false },
    });

    return () => {
      // **必ず破棄する。** 残すと再描画のたびにインスタンスが積む。
      instance.destroy();
    };
  }, [model]);

  const notice = truncationNotice(model);

  return (
    <div className={styles.stack}>
      {notice !== null && (
        // **黙って切らない**(ADR-0044 決定5)。
        <MessageBar intent="warning">
          <MessageBarBody>{notice}</MessageBarBody>
        </MessageBar>
      )}
      <Body1>
        ノード {model.nodes.length} 個 / 辺 {model.edges.length} 本
      </Body1>
      <div ref={container} className={styles.canvas} />
    </div>
  );
}
