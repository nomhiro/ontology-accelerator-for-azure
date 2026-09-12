/**
 * API 呼び出しの形(ADR-0044 決定9)。
 *
 * 固定するのは 3 つ。
 *
 * 1. **トークンを送る/送らない**(`AUTH_MODE=disabled` では送らない)
 * 2. **失敗を黙って空にしない。** 401 と 403 で対処が違う
 * 3. **本文が JSON でなくても落ちない**(ingress が HTML を返すことがある)
 */

import { describe, expect, it } from "vitest";

import { ApiClient, ApiError } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function recordingFetch(response: Response): { calls: Request[]; impl: typeof fetch } {
  const calls: Request[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push(new Request(typeof input === "string" ? input : String(input), init));
    return response;
  }) as typeof fetch;
  return { calls, impl };
}

describe("ApiClient", () => {
  it("トークンがあれば Authorization を付ける", async () => {
    const { calls, impl } = recordingFetch(jsonResponse([{ name: "retail-core" }]));
    const client = new ApiClient({
      baseUrl: "http://localhost:8000",
      token: "abc",
      fetchImpl: impl,
    });

    await client.get("/namespaces");
    expect(calls[0]!.headers.get("Authorization")).toBe("Bearer abc");
  });

  it("トークンが無ければ Authorization を付けない", async () => {
    // `AUTH_MODE=disabled` のローカル開発。**空文字を送らない。**
    const { calls, impl } = recordingFetch(jsonResponse([]));
    const client = new ApiClient({ baseUrl: "http://localhost:8000", fetchImpl: impl });

    await client.get("/namespaces");
    expect(calls[0]!.headers.get("Authorization")).toBeNull();
    expect(client.hasToken).toBe(false);
  });

  it("クエリの undefined を送らない", async () => {
    const { calls, impl } = recordingFetch(jsonResponse({}));
    const client = new ApiClient({ baseUrl: "http://localhost:8000", fetchImpl: impl });

    await client.get("/namespaces/x/versions/1.0.0/diff", { base: undefined });
    expect(calls[0]!.url).not.toContain("base");
  });

  it("失敗を空の結果にしない", async () => {
    // **これが決定9 の要点である。** 401 で空配列を返すと
    // 「名前空間が 1 つも無い」と読まれる。
    const { impl } = recordingFetch(jsonResponse({ detail: "認証が必要です" }, 401));
    const client = new ApiClient({ baseUrl: "http://localhost:8000", fetchImpl: impl });

    await expect(client.get("/namespaces")).rejects.toBeInstanceOf(ApiError);
  });

  it("401 と 403 で対処を書き分ける", async () => {
    const unauthorized = new ApiError(401, "x", "/namespaces");
    const forbidden = new ApiError(403, "x", "/namespaces");

    expect(unauthorized.remedy).toContain("get-access-token");
    expect(forbidden.remedy).toContain("ロール");
    expect(unauthorized.remedy).not.toBe(forbidden.remedy);
  });

  it("状態コードと理由を捨てない", async () => {
    const { impl } = recordingFetch(jsonResponse({ detail: "名前空間が見つかりません" }, 404));
    const client = new ApiClient({ baseUrl: "http://localhost:8000", fetchImpl: impl });

    await client.get("/namespaces/nope").then(
      () => {
        throw new Error("失敗を期待した");
      },
      (error: unknown) => {
        expect(error).toBeInstanceOf(ApiError);
        const apiError = error as ApiError;
        expect(apiError.status).toBe(404);
        expect(apiError.detail).toBe("名前空間が見つかりません");
        expect(apiError.path).toBe("/namespaces/nope");
      },
    );
  });

  it("本文が JSON でなくても落ちない", async () => {
    // ingress やプロキシが HTML を返すことがある。
    // **読めなかったことを隠さない。**
    const html = new Response("<html>502</html>", { status: 502 });
    const { impl } = recordingFetch(html);
    const client = new ApiClient({ baseUrl: "http://localhost:8000", fetchImpl: impl });

    await client.get("/namespaces").then(
      () => {
        throw new Error("失敗を期待した");
      },
      (error: unknown) => {
        expect((error as ApiError).detail).toContain("読めませんでした");
      },
    );
  });

  it("トークンの差し替えで新しいクライアントを返す", async () => {
    // **状態を持たせない。** 同じインスタンスを書き換えると、
    // 実行中の呼び出しが途中で別の主体になる。
    const { impl } = recordingFetch(jsonResponse({}));
    const anonymous = new ApiClient({ baseUrl: "http://localhost:8000", fetchImpl: impl });
    const authenticated = anonymous.withToken("abc");

    expect(anonymous.hasToken).toBe(false);
    expect(authenticated.hasToken).toBe(true);
    expect(authenticated).not.toBe(anonymous);
  });

  it("POST は本文を JSON にして Content-Type を付ける", async () => {
    const { calls, impl } = recordingFetch(jsonResponse({}));
    const client = new ApiClient({ baseUrl: "http://localhost:8000", fetchImpl: impl });

    await client.post("/namespaces/x/versions/1.0.0/submit", { reason: "レビュー依頼" });
    const request = calls[0]!;
    expect(request.method).toBe("POST");
    expect(request.headers.get("Content-Type")).toBe("application/json");
    expect(await request.text()).toBe(JSON.stringify({ reason: "レビュー依頼" }));
  });
});
