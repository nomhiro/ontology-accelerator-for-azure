/**
 * レビュー・承認とグラフ可視化の画面(`P2A-03` / `P2A-04`、ADR-0044)。
 *
 * **判断はここに書かない。** 差分の状態・承認の可否・出自・グラフの打ち切りは
 * すべて純粋関数にあり、テストされている(ADR-0044 決定6)。ここは
 * **取得と受け渡しだけ**を行う。
 *
 * **トークンはメモリにしか持たない**(ADR-0044 決定9)。`localStorage` に
 * 書かない — 再読み込みで消えるのは不便だが、持ち出される不便さのほうが高い。
 * `AUTH_MODE=disabled`(ローカル開発)ではトークンが要らない。
 */

import {
  Body1,
  Button,
  Card,
  CardHeader,
  Dropdown,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Option,
  Spinner,
  Subtitle1,
  Tab,
  TabList,
  Title1,
  makeStyles,
  tokens,
} from "@fluentui/react-components";
import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";

import { ApiClient, ApiError } from "./api/client";
import type { components } from "./api/schema";

import {
  DRAFT_NOT_PROJECTED,
  type GraphModel,
  type SparqlResults,
  isProjected,
  toGraphModel,
} from "./graph/model";
import { ReviewPanel } from "./review/ReviewPanel";
import { type ActionName, principalIdFromToken } from "./review/approval";
import { type DiffResponse, presentDiff } from "./review/diff";
import { readProvenance } from "./review/provenance";

/**
 * グラフの描画は**動的に読み込む**。
 *
 * cytoscape は約 1MB あり、レビューの経路(主たる用途)がその分を払う理由が
 * 無い。グラフのタブを開いたときだけ読み込む。
 */
const GraphView = lazy(async () => ({
  default: (await import("./graph/GraphView")).GraphView,
}));

const API_BASE_URL = import.meta.env["VITE_API_BASE_URL"] ?? "/api";

const useStyles = makeStyles({
  page: {
    maxWidth: "76rem",
    margin: "0 auto",
    padding: tokens.spacingHorizontalXXL,
    display: "flex",
    flexDirection: "column",
    rowGap: tokens.spacingVerticalL,
  },
  row: {
    display: "flex",
    flexWrap: "wrap",
    columnGap: tokens.spacingHorizontalM,
    rowGap: tokens.spacingVerticalS,
    alignItems: "flex-end",
  },
  muted: {
    color: tokens.colorNeutralForeground3,
  },
});

/**
 * 応答の型は**生成されたものを使う**(ADR-0004、ADR-0044 決定2)。
 *
 * `apps/web/src/api/schema.ts` は gitignore された生成物である
 * (`just gen-api` / CI が作る)。**手書きの型は ADR-0004 が却下している** —
 * Phase 2 の構造(リビジョン・差分・承認状態)で必ず乖離する。
 */
type Namespace = components["schemas"]["Namespace"];
type Version = components["schemas"]["OntologyVersion"];
type Decision = components["schemas"]["AuditEvent"];

/** 直近の `published` の理由を返す(出自が入っている)。 */
function publishReason(decisions: Decision[]): string | null {
  return decisions.find((d) => d.action === "published")?.reason ?? null;
}

/** グラフに使うクエリ。**上限は画面側でも掛ける**(ADR-0044 決定5)。 */
const GRAPH_QUERY = "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 2000";

export function App() {
  const styles = useStyles();
  const [token, setToken] = useState<string | null>(null);
  const [tokenDraft, setTokenDraft] = useState("");
  const [namespaces, setNamespaces] = useState<Namespace[]>([]);
  const [selectedNamespace, setSelectedNamespace] = useState<string | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [graph, setGraph] = useState<GraphModel | null>(null);
  const [tab, setTab] = useState<"review" | "graph">("review");
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [busy, setBusy] = useState(false);

  const client = useMemo(
    () => new ApiClient({ baseUrl: API_BASE_URL, token }),
    [token],
  );
  const namespace = namespaces.find((n) => n.name === selectedNamespace) ?? null;
  const version = versions.find((v) => v.version === selectedVersion) ?? null;

  const load = useCallback(
    async (work: () => Promise<void>) => {
      setBusy(true);
      setError(null);
      try {
        await work();
      } catch (cause: unknown) {
        // **黙って空にしない**(ADR-0044 決定9)。
        setError(cause instanceof Error ? cause : new Error(String(cause)));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  useEffect(() => {
    void load(async () => {
      const listed = await client.get<Namespace[]>("/namespaces");
      setNamespaces(listed);
      setSelectedNamespace((current) => current ?? listed[0]?.name ?? null);
    });
  }, [client, load]);

  useEffect(() => {
    if (!selectedNamespace) {
      return;
    }
    void load(async () => {
      const listed = await client.get<Version[]>(`/namespaces/${selectedNamespace}/versions`);
      setVersions(listed);
      setSelectedVersion(listed[listed.length - 1]?.version ?? null);
    });
  }, [client, load, selectedNamespace]);

  useEffect(() => {
    if (!selectedNamespace || !selectedVersion) {
      return;
    }
    void load(async () => {
      const [diffResponse, decisionList] = await Promise.all([
        client.get<DiffResponse>(
          `/namespaces/${selectedNamespace}/versions/${selectedVersion}/diff`,
        ),
        client.get<Decision[]>(
          `/namespaces/${selectedNamespace}/versions/${selectedVersion}/decisions`,
        ),
      ]);
      setDiff(diffResponse);
      setDecisions(decisionList);
    });
  }, [client, load, selectedNamespace, selectedVersion]);

  // グラフは**射影されている版だけ**引く(ADR-0044 決定5)。
  useEffect(() => {
    setGraph(null);
    if (!selectedNamespace || !version || !isProjected(version.status)) {
      return;
    }
    void load(async () => {
      const results = await client.get<SparqlResults>(
        `/namespaces/${selectedNamespace}/sparql`,
        { query: GRAPH_QUERY },
      );
      setGraph(toGraphModel(results));
    });
  }, [client, load, selectedNamespace, version]);

  const act = useCallback(
    (action: ActionName, reason: string) => {
      if (!selectedNamespace || !selectedVersion) {
        return;
      }
      void load(async () => {
        await client.post(
          `/namespaces/${selectedNamespace}/versions/${selectedVersion}/${action}`,
          { reason },
        );
        const listed = await client.get<Version[]>(`/namespaces/${selectedNamespace}/versions`);
        setVersions(listed);
      });
    },
    [client, load, selectedNamespace, selectedVersion],
  );

  return (
    <main className={styles.page}>
      <Title1>Ontology Accelerator for Azure</Title1>

      <Card>
        <CardHeader header={<Subtitle1>接続</Subtitle1>} />
        <div className={styles.row}>
          <Field
            label="アクセストークン"
            hint="AUTH_MODE=disabled のローカル開発では空のままでよい。**メモリにしか保持しません**(再読み込みで消えます)。"
          >
            <Input
              type="password"
              value={tokenDraft}
              onChange={(_, data) => setTokenDraft(data.value)}
              placeholder="az account get-access-token ... の accessToken"
            />
          </Field>
          <Button onClick={() => setToken(tokenDraft.trim() || null)}>適用</Button>
          <Body1 className={styles.muted}>
            {client.hasToken ? "トークンを送っています" : "トークンを送っていません"}
          </Body1>
        </div>
      </Card>

      {error !== null && (
        <MessageBar intent="error">
          <MessageBarBody>
            <MessageBarTitle>{error.message}</MessageBarTitle>
            {error instanceof ApiError ? error.remedy : "詳細を読んで対処してください。"}
          </MessageBarBody>
        </MessageBar>
      )}

      <Card>
        <CardHeader header={<Subtitle1>対象</Subtitle1>} />
        <div className={styles.row}>
          <Field label="名前空間">
            <Dropdown
              value={selectedNamespace ?? ""}
              selectedOptions={selectedNamespace ? [selectedNamespace] : []}
              onOptionSelect={(_, data) => setSelectedNamespace(data.optionValue ?? null)}
            >
              {namespaces.map((item) => (
                <Option key={item.name} value={item.name}>
                  {item.name}
                </Option>
              ))}
            </Dropdown>
          </Field>
          <Field label="版">
            <Dropdown
              value={selectedVersion ?? ""}
              selectedOptions={selectedVersion ? [selectedVersion] : []}
              onOptionSelect={(_, data) => setSelectedVersion(data.optionValue ?? null)}
            >
              {versions.map((item) => (
                <Option
                  key={item.version}
                  value={item.version}
                  // Fluent UI は子が単一の文字列でないとき `text` を要求する。
                  text={`${item.version} (${item.status})`}
                >
                  {item.version} ({item.status})
                </Option>
              ))}
            </Dropdown>
          </Field>
          {busy && <Spinner size="tiny" />}
        </div>
        {namespace?.retired_at !== null && namespace !== null && (
          <MessageBar intent="warning">
            <MessageBarBody>
              この名前空間は退役しています。内容を増やせません(ADR-0032 決定5)。
            </MessageBarBody>
          </MessageBar>
        )}
      </Card>

      {version !== null && diff !== null && (
        <>
          <TabList selectedValue={tab} onTabSelect={(_, data) => setTab(data.value as typeof tab)}>
            <Tab value="review">レビュー</Tab>
            <Tab value="graph">グラフ</Tab>
          </TabList>

          {tab === "review" ? (
            <ReviewPanel
              version={{
                version: version.version,
                status: version.status as never,
                created_by: version.created_by,
              }}
              diff={presentDiff(diff)}
              provenance={readProvenance(publishReason(decisions))}
              context={{
                principalId: principalIdFromToken(token),
                requireTwoPersonApproval: namespace?.require_two_person_approval ?? true,
              }}
              onAct={act}
              busy={busy}
            />
          ) : !isProjected(version.status) ? (
            // **空のグラフを出さない**(ADR-0044 決定5)。
            <MessageBar intent="info">
              <MessageBarBody>
                <MessageBarTitle>グラフを描けません</MessageBarTitle>
                {DRAFT_NOT_PROJECTED}
              </MessageBarBody>
            </MessageBar>
          ) : graph !== null ? (
            <Suspense fallback={<Body1 className={styles.muted}>グラフを読み込み中…</Body1>}>
              <GraphView model={graph} />
            </Suspense>
          ) : (
            <Body1 className={styles.muted}>読み込み中…</Body1>
          )}
        </>
      )}
    </main>
  );
}
