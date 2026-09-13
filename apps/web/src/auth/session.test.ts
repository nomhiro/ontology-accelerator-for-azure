/**
 * サインインとトークンの取得(ADR-0045)。
 *
 * **このファイルでいちばん重要なのは
 * `test 対話が必要なことを失敗と混ぜない` である。**
 *
 * `acquireTokenSilent` の例外を握り潰して `null` を返すと、画面は
 * **トークン無しで API を叩いて 401 を受け、「権限が無い」と読む**。
 * 実際には「サインインし直せば解決する」である。
 */

import { describe, expect, it, vi } from "vitest";

import {
  type AccountLike,
  type MsalLike,
  acquireToken,
  describeAuthError,
  isInteractionRequired,
  resolveAccount,
} from "./session";

const ACCOUNT: AccountLike = {
  homeAccountId: "home-1",
  environment: "login.microsoftonline.com",
  tenantId: "tenant-1",
  username: "someone@example.com",
  localAccountId: "local-1",
  name: "Someone",
};

const SCOPES = ["api://app/user_impersonation"];

function msal(overrides: Partial<MsalLike> = {}): MsalLike {
  return {
    initialize: vi.fn(async () => undefined),
    handleRedirectPromise: vi.fn(async () => null),
    getAllAccounts: vi.fn(() => []),
    setActiveAccount: vi.fn(),
    acquireTokenSilent: vi.fn(async () => ({ accessToken: "token-value" })),
    loginRedirect: vi.fn(async () => undefined),
    logoutRedirect: vi.fn(async () => undefined),
    ...overrides,
  };
}

describe("resolveAccount", () => {
  it("リダイレクトから戻ってきたアカウントを先に見る", async () => {
    // **`handleRedirectPromise` を呼ばないと認可コードが処理されず、
    // アカウントが現れない。**
    const instance = msal({
      handleRedirectPromise: vi.fn(async () => ({ account: ACCOUNT })),
      getAllAccounts: vi.fn(() => []),
    });
    expect(await resolveAccount(instance)).toEqual(ACCOUNT);
    expect(instance.handleRedirectPromise).toHaveBeenCalled();
  });

  it("既にサインイン済みのアカウントを使う", async () => {
    const instance = msal({ getAllAccounts: vi.fn(() => [ACCOUNT]) });
    expect(await resolveAccount(instance)).toEqual(ACCOUNT);
  });

  it("サインインしていなければ null", async () => {
    expect(await resolveAccount(msal())).toBeNull();
  });

  it("initialize を必ず呼ぶ", async () => {
    // msal-browser 5.x は `initialize()` を呼ばないと他の API が失敗する。
    const instance = msal();
    await resolveAccount(instance);
    expect(instance.initialize).toHaveBeenCalled();
  });

  it("有効なアカウントを設定する", async () => {
    const instance = msal({ getAllAccounts: vi.fn(() => [ACCOUNT]) });
    await resolveAccount(instance);
    expect(instance.setActiveAccount).toHaveBeenCalledWith(ACCOUNT);
  });
});

describe("acquireToken", () => {
  it("トークンを返す", async () => {
    const outcome = await acquireToken(msal(), ACCOUNT, SCOPES);
    expect(outcome).toEqual({ kind: "token", accessToken: "token-value" });
  });

  it("サインインしていなければ signed-out を返す", async () => {
    // **失敗ではない。** サインインのボタンを出す状態である。
    expect(await acquireToken(msal(), null, SCOPES)).toEqual({ kind: "signed-out" });
  });

  it("対話が必要なことを失敗と混ぜない", async () => {
    // **これがこのファイルの本体である。**
    const error = Object.assign(new Error("silent failed"), {
      name: "InteractionRequiredAuthError",
      errorCode: "interaction_required",
    });
    const outcome = await acquireToken(
      msal({ acquireTokenSilent: vi.fn(async () => Promise.reject(error)) }),
      ACCOUNT,
      SCOPES,
    );
    expect(outcome.kind).toBe("interaction-required");
    if (outcome.kind !== "interaction-required") {
      throw new Error("interaction-required を期待した");
    }
    // **ページが離れることを予告する**(書きかけの理由が消える)。
    expect(outcome.message).toContain("ページが離れます");
  });

  it("本当の失敗は理由を運ぶ", async () => {
    const error = Object.assign(new Error("boom"), { errorCode: "endpoints_resolution_error" });
    const outcome = await acquireToken(
      msal({ acquireTokenSilent: vi.fn(async () => Promise.reject(error)) }),
      ACCOUNT,
      SCOPES,
    );
    expect(outcome.kind).toBe("failed");
    if (outcome.kind !== "failed") {
      throw new Error("failed を期待した");
    }
    expect(outcome.message).toContain("endpoints_resolution_error");
  });

  it("空のトークンを返さない", async () => {
    // **空の `Authorization` を送ると 401 になり、「権限が無い」と読まれる。**
    const outcome = await acquireToken(
      msal({ acquireTokenSilent: vi.fn(async () => ({ accessToken: "" })) }),
      ACCOUNT,
      SCOPES,
    );
    expect(outcome.kind).toBe("failed");
  });

  it("先に静かな取得を試す", async () => {
    // ページが離れないので、入力途中の理由が消えない(決定2 の副作用への対策)。
    const instance = msal();
    await acquireToken(instance, ACCOUNT, SCOPES);
    expect(instance.acquireTokenSilent).toHaveBeenCalledWith({
      scopes: [...SCOPES],
      account: ACCOUNT,
    });
    expect(instance.loginRedirect).not.toHaveBeenCalled();
  });
});

describe("isInteractionRequired", () => {
  it("name で判定できる", () => {
    expect(isInteractionRequired({ name: "InteractionRequiredAuthError" })).toBe(true);
  });

  it("errorCode で判定できる", () => {
    // **名前とコードの両方を見る。** 版によって名前が変わる。
    for (const code of ["interaction_required", "login_required", "consent_required"]) {
      expect(isInteractionRequired({ errorCode: code }), code).toBe(true);
    }
  });

  it("メッセージに混ざる形も拾う", () => {
    expect(isInteractionRequired({ errorMessage: "AADSTS50076: interaction_required" })).toBe(true);
  });

  it("関係ない失敗を対話扱いにしない", () => {
    // **取り違えると、直らない状態でサインインを促し続ける。**
    expect(isInteractionRequired({ errorCode: "endpoints_resolution_error" })).toBe(false);
    expect(isInteractionRequired(new Error("network"))).toBe(false);
    expect(isInteractionRequired(null)).toBe(false);
    expect(isInteractionRequired("interaction_required")).toBe(false);
  });
});

describe("describeAuthError", () => {
  it("コードと本文を両方運ぶ", () => {
    expect(describeAuthError({ errorCode: "x", errorMessage: "y" })).toBe("x: y");
  });

  it("Error でも読める形にする", () => {
    expect(describeAuthError(new Error("boom"))).toContain("boom");
  });

  it("何も読めなくても文字列を返す", () => {
    // **落ちない。** 理由が読めないことを隠さないために文字列化する。
    expect(describeAuthError(42)).toBe("42");
    expect(describeAuthError({})).toBe("[object Object]");
  });
});
