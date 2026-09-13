/**
 * MSAL の設定を組み立てる(ADR-0045、`P2A-20`)。
 *
 * # 設定が無ければ何も作らない(決定9)
 *
 * `VITE_ENTRA_CLIENT_ID` が空なら **`null` を返す**。`AUTH_MODE=disabled`
 * (ローカル開発)でトークンを送らずに動く形をそのまま残す。
 *
 * **半端に初期化しない。** 設定が無いのに MSAL を作ると、サインインの
 * ボタンが出て、押すと `AADSTS900971: No reply address provided` のような
 * **設定の誤りに見えないエラー**になる。
 *
 * # トークンは `sessionStorage` に置く(決定7)
 *
 * ADR-0044 決定9 の「メモリにしか持たない」は**撤回した**。リダイレクト方式は
 * ページの遷移をまたぐので、メモリだけでは**戻ってきた時点で PKCE の
 * verifier が消えており、フローが成立しない**。
 *
 * `localStorage` は却下した — **XSS で持ち出されたトークンが再起動後も
 * 有効になる**。`sessionStorage` ならそのタブの寿命に限られる。
 *
 * **既定に頼らず明示する。** `msal-browser` の既定は `sessionStorage` だが、
 * 既定は変わりうる。
 */

/** `import.meta.env` から読む値。テストから差し替えるために型を切る。 */
export interface AuthEnv {
  readonly VITE_ENTRA_CLIENT_ID?: string | undefined;
  readonly VITE_ENTRA_TENANT_ID?: string | undefined;
  readonly VITE_API_SCOPE?: string | undefined;
  readonly VITE_REDIRECT_URI?: string | undefined;
}

/** MSAL に渡す設定(`Configuration` の必要な部分だけ)。 */
export interface AuthConfig {
  readonly auth: {
    readonly clientId: string;
    readonly authority: string;
    readonly redirectUri: string;
    readonly navigateToLoginRequestUrl: boolean;
  };
  readonly cache: {
    readonly cacheLocation: "sessionStorage";
    readonly storeAuthStateInCookie: false;
  };
  /** トークンを取るときに要求するスコープ。 */
  readonly scopes: readonly string[];
}

/** テナントが指定されないときの権限。 */
export const DEFAULT_AUTHORITY_TENANT = "organizations";

/**
 * 設定を組み立てる。**足りなければ `null`**(決定9)。
 *
 * `origin` は現在のオリジン(`globalThis.location.origin`)。
 * **リダイレクト URI の既定はそれそのもの**である — アプリ登録に
 * `http://localhost:5173` を登録してあり、**localhost はポートが照合で
 * 無視される**ので `vite preview` でも通る(ADR-0045 決定4)。
 */
export function buildAuthConfig(env: AuthEnv, origin: string): AuthConfig | null {
  const clientId = (env.VITE_ENTRA_CLIENT_ID ?? "").trim();
  if (!clientId) {
    // **設定が無い = 認証を使わない**(`AUTH_MODE=disabled`)。
    return null;
  }
  const tenant = (env.VITE_ENTRA_TENANT_ID ?? "").trim() || DEFAULT_AUTHORITY_TENANT;
  const scope = (env.VITE_API_SCOPE ?? "").trim() || defaultScope(clientId);
  const redirectUri = (env.VITE_REDIRECT_URI ?? "").trim() || origin;

  return {
    auth: {
      clientId,
      authority: `https://login.microsoftonline.com/${tenant}`,
      redirectUri,
      // サインイン後に元の URL へ戻す処理を MSAL に任せない。
      // **画面の状態は URL に持っていない**ので、戻す必要が無い。
      navigateToLoginRequestUrl: false,
    },
    cache: {
      // **明示する**(決定7)。既定に頼らない。
      cacheLocation: "sessionStorage",
      storeAuthStateInCookie: false,
    },
    scopes: [scope],
  };
}

/**
 * 既定のスコープ(ADR-0045 決定6)。
 *
 * このアプリ登録が公開しているスコープは `user_impersonation` 1 つだけである
 * (実測)。`.default` も使えるが、**公開しているスコープを明示的に要求する
 * ほうが読める**。
 */
export function defaultScope(clientId: string): string {
  return `api://${clientId}/user_impersonation`;
}

/** 認証が設定されていないときに画面へ出す説明。 */
export const AUTH_NOT_CONFIGURED =
  "認証は設定されていません(`VITE_ENTRA_CLIENT_ID` が空)。" +
  "ローカル開発で `AUTH_MODE=disabled` の Core API に対してはこのままで動きます。" +
  "デプロイ環境に対して使うには、`VITE_ENTRA_CLIENT_ID` にアプリ登録の appId を渡し、" +
  "`uv run python scripts/setup-app-role.py` で SPA のリダイレクト URI を登録してください。";
