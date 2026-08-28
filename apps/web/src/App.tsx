import {
  Body1,
  Card,
  CardHeader,
  Subtitle1,
  Title1,
  makeStyles,
  tokens,
} from "@fluentui/react-components";
import { useEffect, useState } from "react";

const API_BASE_URL = import.meta.env["VITE_API_BASE_URL"] ?? "/api";

const useStyles = makeStyles({
  page: {
    maxWidth: "72rem",
    margin: "0 auto",
    padding: tokens.spacingHorizontalXXL,
    display: "flex",
    flexDirection: "column",
    rowGap: tokens.spacingVerticalL,
  },
  status: {
    color: tokens.colorNeutralForeground2,
  },
});

interface HealthResponse {
  status: string;
  version: string;
  auth_mode: string;
}

/**
 * Phase 0 のプレースホルダ画面。
 *
 * Core API に到達できているかだけを表示する。オントロジーのレビュー・承認 UI と
 * グラフ可視化は Phase 2 で実装する。
 */
export function App() {
  const styles = useStyles();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    fetch(`${API_BASE_URL}/healthz`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        setHealth((await response.json()) as HealthResponse);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setError(cause instanceof Error ? cause.message : String(cause));
      });

    return () => {
      controller.abort();
    };
  }, []);

  return (
    <main className={styles.page}>
      <Title1>Ontology Accelerator for Azure</Title1>
      <Body1>
        承認済みのビジネスオントロジーを W3C 標準のナレッジグラフとして管理し、MCP 経由で
        AI エージェントに提供します。この画面は Phase 0 のプレースホルダです。
      </Body1>

      <Card>
        <CardHeader header={<Subtitle1>Core API への接続</Subtitle1>} />
        <Body1 className={styles.status}>
          {health
            ? `接続できました (version ${health.version} / auth_mode ${health.auth_mode})`
            : error
              ? `接続できません: ${error}`
              : "確認中..."}
        </Body1>
      </Card>

      <Card>
        <CardHeader header={<Subtitle1>次のフェーズで実装するもの</Subtitle1>} />
        <Body1 className={styles.status}>
          名前空間の管理 (Phase 1)、オントロジーのレビュー・承認とグラフ可視化 (Phase 2)、
          連邦クエリとベクトル検索 (Phase 3)。
        </Body1>
      </Card>
    </main>
  );
}
