/**
 * MSAL の設定の組み立て(ADR-0045 決定6・7・9)。
 *
 * 固定するのは 3 つ。
 *
 * 1. **設定が無ければ何も作らない**(決定9)。半端に初期化しない
 * 2. **`sessionStorage` を明示する**(決定7)。`localStorage` を使わない
 * 3. **スコープは公開しているものを要求する**(決定6)
 */

import { describe, expect, it } from "vitest";

import { AUTH_NOT_CONFIGURED, buildAuthConfig, defaultScope } from "./config";

const CLIENT_ID = "bb5008d3-cc5f-47ce-8dda-d4fae0273303";
const ORIGIN = "http://localhost:5173";

describe("buildAuthConfig", () => {
  it("clientId が無ければ null を返す", () => {
    // **これが決定9 の本体である。** 設定が無いのに MSAL を作ると、
    // 押すと `AADSTS900971` のような**設定の誤りに見えないエラー**になる。
    expect(buildAuthConfig({}, ORIGIN)).toBeNull();
    expect(buildAuthConfig({ VITE_ENTRA_CLIENT_ID: "" }, ORIGIN)).toBeNull();
    expect(buildAuthConfig({ VITE_ENTRA_CLIENT_ID: "   " }, ORIGIN)).toBeNull();
  });

  it("clientId があれば設定を作る", () => {
    const config = buildAuthConfig({ VITE_ENTRA_CLIENT_ID: CLIENT_ID }, ORIGIN);
    expect(config).not.toBeNull();
    expect(config!.auth.clientId).toBe(CLIENT_ID);
  });

  it("sessionStorage を明示する", () => {
    // **既定に頼らない**(決定7)。msal-browser の既定は変わりうる。
    const config = buildAuthConfig({ VITE_ENTRA_CLIENT_ID: CLIENT_ID }, ORIGIN)!;
    expect(config.cache.cacheLocation).toBe("sessionStorage");
    expect(config.cache.storeAuthStateInCookie).toBe(false);
  });

  it("localStorage を使わない", () => {
    // **XSS で持ち出されたトークンが再起動後も有効になる**ため却下した。
    const config = buildAuthConfig({ VITE_ENTRA_CLIENT_ID: CLIENT_ID }, ORIGIN)!;
    expect(JSON.stringify(config)).not.toContain("localStorage");
  });

  it("リダイレクト URI の既定は現在のオリジンである", () => {
    // アプリ登録に `http://localhost:5173` を登録してあり、
    // **localhost はポートが照合で無視される**ので preview でも通る。
    const config = buildAuthConfig({ VITE_ENTRA_CLIENT_ID: CLIENT_ID }, "http://localhost:4173")!;
    expect(config.auth.redirectUri).toBe("http://localhost:4173");
  });

  it("リダイレクト URI を明示できる", () => {
    const config = buildAuthConfig(
      { VITE_ENTRA_CLIENT_ID: CLIENT_ID, VITE_REDIRECT_URI: "https://app.example/callback" },
      ORIGIN,
    )!;
    expect(config.auth.redirectUri).toBe("https://app.example/callback");
  });

  it("テナントが無ければ organizations を使う", () => {
    const config = buildAuthConfig({ VITE_ENTRA_CLIENT_ID: CLIENT_ID }, ORIGIN)!;
    expect(config.auth.authority).toBe("https://login.microsoftonline.com/organizations");
  });

  it("テナントを明示できる", () => {
    const config = buildAuthConfig(
      { VITE_ENTRA_CLIENT_ID: CLIENT_ID, VITE_ENTRA_TENANT_ID: "e4ed36be-1111" },
      ORIGIN,
    )!;
    expect(config.auth.authority).toBe("https://login.microsoftonline.com/e4ed36be-1111");
  });

  it("スコープの既定は公開しているものである", () => {
    // このアプリ登録が公開しているのは `user_impersonation` 1 つだけ(実測)。
    const config = buildAuthConfig({ VITE_ENTRA_CLIENT_ID: CLIENT_ID }, ORIGIN)!;
    expect(config.scopes).toEqual([`api://${CLIENT_ID}/user_impersonation`]);
  });

  it("スコープを差し替えられる", () => {
    const config = buildAuthConfig(
      { VITE_ENTRA_CLIENT_ID: CLIENT_ID, VITE_API_SCOPE: `api://${CLIENT_ID}/.default` },
      ORIGIN,
    )!;
    expect(config.scopes).toEqual([`api://${CLIENT_ID}/.default`]);
  });

  it("元の URL へ戻す処理を MSAL に任せない", () => {
    // 画面の状態は URL に持っていないので、戻す必要が無い。
    const config = buildAuthConfig({ VITE_ENTRA_CLIENT_ID: CLIENT_ID }, ORIGIN)!;
    expect(config.auth.navigateToLoginRequestUrl).toBe(false);
  });
});

describe("defaultScope", () => {
  it("appId から組み立てる", () => {
    expect(defaultScope("abc")).toBe("api://abc/user_impersonation");
  });
});

describe("AUTH_NOT_CONFIGURED", () => {
  it("対処を書く", () => {
    // **「設定されていません」で終わらせない。**
    expect(AUTH_NOT_CONFIGURED).toContain("VITE_ENTRA_CLIENT_ID");
    expect(AUTH_NOT_CONFIGURED).toContain("AUTH_MODE=disabled");
    expect(AUTH_NOT_CONFIGURED).toContain("setup-app-role.py");
  });
});
