/**
 * サインインの状態と操作(ADR-0045、`P2A-20`)。
 *
 * **判断はここに書かない。** トークンの取得結果の分類は `session.ts`、
 * 設定の組み立ては `config.ts` にあり、どちらもテストされている。
 *
 * **貼り付けの口は無い**(ADR-0044 決定9 の撤回。ADR-0045 決定8)。
 * 2 つの経路を残すと、MSAL が動かないときに貼り付けへ逃げる運用が生まれ、
 * **「サインインが壊れている」ことに誰も気づかない**。
 */

import {
  Body1,
  Button,
  Card,
  CardHeader,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Subtitle1,
  makeStyles,
  tokens,
} from "@fluentui/react-components";

import { AUTH_NOT_CONFIGURED } from "./config";
import type { AccountLike, TokenOutcome } from "./session";

const useStyles = makeStyles({
  row: {
    display: "flex",
    flexWrap: "wrap",
    columnGap: tokens.spacingHorizontalM,
    rowGap: tokens.spacingVerticalS,
    alignItems: "center",
  },
  muted: {
    color: tokens.colorNeutralForeground3,
  },
});

export interface SignInBarProps {
  /** 認証が設定されているか(`VITE_ENTRA_CLIENT_ID`)。 */
  readonly configured: boolean;
  readonly account: AccountLike | null;
  readonly outcome: TokenOutcome | null;
  readonly onSignIn: () => void;
  readonly onSignOut: () => void;
  readonly busy: boolean;
}

export function SignInBar({
  configured,
  account,
  outcome,
  onSignIn,
  onSignOut,
  busy,
}: SignInBarProps) {
  const styles = useStyles();

  if (!configured) {
    // **設定が無ければサインインのボタンを出さない**(ADR-0045 決定9)。
    // 出すと、押したときに設定の誤りに見えないエラーになる。
    return (
      <MessageBar intent="info">
        <MessageBarBody>
          <MessageBarTitle>認証は設定されていません</MessageBarTitle>
          {AUTH_NOT_CONFIGURED}
        </MessageBarBody>
      </MessageBar>
    );
  }

  return (
    <Card>
      <CardHeader header={<Subtitle1>サインイン</Subtitle1>} />
      <div className={styles.row}>
        {account === null ? (
          <>
            <Button appearance="primary" onClick={onSignIn} disabled={busy}>
              サインイン
            </Button>
            <Body1 className={styles.muted}>
              Microsoft Entra ID で認証します(認可コードフロー + PKCE)。
              **押すとページが離れます。**
            </Body1>
          </>
        ) : (
          <>
            <Body1>
              {account.name ?? account.username}(<code>{account.username}</code>)
            </Body1>
            <Button onClick={onSignOut} disabled={busy}>
              サインアウト
            </Button>
          </>
        )}
      </div>

      {outcome?.kind === "interaction-required" && (
        // **失敗と混ぜない。** サインインし直せば解決する。
        <MessageBar intent="warning">
          <MessageBarBody>
            <MessageBarTitle>サインインが必要です</MessageBarTitle>
            {outcome.message}
          </MessageBarBody>
        </MessageBar>
      )}

      {outcome?.kind === "failed" && (
        <MessageBar intent="error">
          <MessageBarBody>
            <MessageBarTitle>トークンを取得できませんでした</MessageBarTitle>
            {outcome.message}
          </MessageBarBody>
        </MessageBar>
      )}
    </Card>
  );
}
