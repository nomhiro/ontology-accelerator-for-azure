/**
 * サインインとトークンの取得(ADR-0045、`P2A-20`)。
 *
 * # MSAL を直接触るのはここだけにする
 *
 * **判断は純粋関数に置く**(ADR-0044 決定6)。MSAL のインスタンスを
 * 引数で受けるので、テストから偽物を渡せる。
 *
 * # 静かに失敗させない
 *
 * `acquireTokenSilent` は**対話が必要なとき例外を投げる**
 * (`InteractionRequiredAuthError`)。これを握り潰して `null` を返すと、
 * 画面は**トークン無しで API を叩いて 401 を受け、「権限が無い」と読む**。
 *
 * だから**「対話が必要」と「本当に失敗した」を区別して返す**。
 */

/** MSAL のうち、ここが使う部分だけ。**テストから偽物を渡すために切る。**
 *
 * **`readonly string[]` を使わない。** MSAL の `SilentRequest.scopes` は
 * 可変の `string[]` なので、`readonly` にすると実物を渡せなくなる
 * (**キャストで潰すのではなく、実物の形に合わせる**)。
 */
export interface MsalLike {
  initialize(): Promise<void>;
  handleRedirectPromise(): Promise<{ account: AccountLike } | null>;
  getAllAccounts(): AccountLike[];
  setActiveAccount?(account: AccountLike | null): void;
  acquireTokenSilent(request: {
    scopes: string[];
    account: AccountLike;
  }): Promise<{ accessToken: string }>;
  loginRedirect(request: { scopes: string[] }): Promise<void>;
  logoutRedirect(request?: { account?: AccountLike | null }): Promise<void>;
}

/**
 * アカウント。**MSAL の `AccountInfo` が要求する欄をすべて持つ。**
 *
 * 表示に使うのは `name` と `username` だけだが、**欄を削ると実物の
 * `AccountInfo` を渡せなくなる**(`logoutRedirect` が `AccountInfo` を
 * 要求する)。狭い型を作ってキャストで埋めるより、実物に合わせる。
 */
export interface AccountLike {
  readonly homeAccountId: string;
  readonly environment: string;
  readonly tenantId: string;
  readonly username: string;
  readonly localAccountId: string;
  readonly name?: string | undefined;
}

/** トークンを取ろうとした結果。 */
export type TokenOutcome =
  | { readonly kind: "token"; readonly accessToken: string }
  | {
      /** **対話が必要。** 失敗ではない — サインインを促す。 */
      readonly kind: "interaction-required";
      readonly message: string;
    }
  | {
      /** サインインしていない。 */
      readonly kind: "signed-out";
    }
  | {
      /** **本当に失敗した。** 理由を捨てない。 */
      readonly kind: "failed";
      readonly message: string;
    };

/** MSAL が「対話が必要」を示すときのエラー名・コード。 */
const INTERACTION_REQUIRED = new Set([
  "InteractionRequiredAuthError",
  "interaction_required",
  "login_required",
  "consent_required",
]);

/**
 * 例外が「対話が必要」を意味するか。
 *
 * **名前とコードの両方を見る。** MSAL は `errorCode` を持つ独自の
 * エラークラスを投げるが、**版によって名前が変わる**ので片方だけに
 * 頼らない。
 */
export function isInteractionRequired(cause: unknown): boolean {
  if (typeof cause !== "object" || cause === null) {
    return false;
  }
  const record = cause as Record<string, unknown>;
  for (const key of ["name", "errorCode"]) {
    const value = record[key];
    if (typeof value === "string" && INTERACTION_REQUIRED.has(value)) {
      return true;
    }
  }
  // `errorMessage` に混ざる形もある(版差の受け止め)。
  const message = record["errorMessage"] ?? record["message"];
  if (typeof message === "string") {
    for (const marker of INTERACTION_REQUIRED) {
      if (message.includes(marker)) {
        return true;
      }
    }
  }
  return false;
}

/** 失敗の理由を文字列にする。**捨てない。** */
export function describeAuthError(cause: unknown): string {
  if (typeof cause === "object" && cause !== null) {
    const record = cause as Record<string, unknown>;
    const code = record["errorCode"];
    const message = record["errorMessage"] ?? record["message"];
    const parts = [code, message].filter((part): part is string => typeof part === "string");
    if (parts.length > 0) {
      return parts.join(": ");
    }
  }
  return String(cause);
}

/**
 * サインイン済みのアカウントを返す。**無ければ `null`。**
 *
 * **リダイレクトから戻ってきた場合を先に処理する。** `handleRedirectPromise`
 * を呼ばないと、戻ってきた認可コードが処理されずアカウントが現れない。
 */
export async function resolveAccount(msal: MsalLike): Promise<AccountLike | null> {
  await msal.initialize();
  const redirect = await msal.handleRedirectPromise();
  const account = redirect?.account ?? msal.getAllAccounts()[0] ?? null;
  msal.setActiveAccount?.(account);
  return account;
}

/**
 * トークンを取る。
 *
 * **`acquireTokenSilent` を先に試す**(決定2 の副作用への対策)。成功すれば
 * ページが離れないので、入力途中の理由が消えない。
 */
export async function acquireToken(
  msal: MsalLike,
  account: AccountLike | null,
  scopes: readonly string[],
): Promise<TokenOutcome> {
  if (account === null) {
    return { kind: "signed-out" };
  }
  try {
    // **配列を複製して渡す。** MSAL は可変の配列を要求する。
    const result = await msal.acquireTokenSilent({ scopes: [...scopes], account });
    if (!result.accessToken) {
      // **空文字を返さない。** 空の `Authorization` を送ると 401 になり、
      // 「権限が無い」と読まれる。
      return { kind: "failed", message: "MSAL が空のトークンを返しました" };
    }
    return { kind: "token", accessToken: result.accessToken };
  } catch (cause: unknown) {
    if (isInteractionRequired(cause)) {
      // **失敗と混ぜない。** サインインを促せば解決する。
      return {
        kind: "interaction-required",
        message:
          "サインインが必要です(トークンの期限切れ、または同意が未取得)。" +
          "**サインインするとページが離れます** — 書きかけの理由は失われます。",
      };
    }
    return { kind: "failed", message: describeAuthError(cause) };
  }
}
