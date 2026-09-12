/**
 * Core API への呼び出し(ADR-0044 決定9)。
 *
 * # トークンをメモリにしか持たない
 *
 * `localStorage` に書かない。**XSS で持ち出されるため**である。画面を
 * 再読み込みしたら貼り直す — 不便だが、**トークンが永続化される不便さの
 * ほうが高い**。
 *
 * `AUTH_MODE=disabled`(ローカル開発)ではトークンが要らない。
 *
 * # MSAL を入れていない
 *
 * SPA のリダイレクト URI はアプリ登録の変更を伴い、**デプロイ窓なしに
 * 検証できない**(ADR-0044 決定9)。「書いたが動かしていない認証」を
 * 残さない。`P2A-20` として記録してある。
 *
 * # 401 を黙って空にしない
 *
 * トークンが無い/期限切れのときに空の一覧を出すと、**「名前空間が 1 つも
 * 無い」と読まれる**。`ApiError` にして理由と対処を画面へ運ぶ。
 */

const DEFAULT_BASE_URL = "/api";

/** API が返した失敗。**状態コードを捨てない。** */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly path: string,
  ) {
    super(`${path} -> HTTP ${status}: ${detail}`);
    this.name = "ApiError";
  }

  /**
   * 運用者への対処。
   *
   * **「失敗しました」で終わらせない** — 401 と 403 は対処が違う。
   */
  get remedy(): string {
    switch (this.status) {
      case 401:
        return (
          "トークンが無いか期限切れです。`az account get-access-token --scope " +
          '"api://<appId>/.default" --query accessToken -o tsv` で取得して貼り直してください' +
          "(ローカル開発で `AUTH_MODE=disabled` ならトークンは要りません)。"
        );
      case 403:
        return (
          "権限が足りません。名前空間のロール(data-analyst < data-steward < maintainer < owner)" +
          "か、`platform-admin` アプリロールが必要です。"
        );
      case 404:
        return "対象が見つかりません。名前空間名と版を確認してください。";
      case 409:
        return "状態が競合しています。画面を再読み込みして最新の状態を確認してください。";
      case 413:
        return "対象が大きすぎます。範囲を絞ってください。";
      case 422:
        return "内容が受け付けられませんでした。詳細を読んで直してください。";
      case 502:
        return "外部の依存(トリプルストア・モデル・ソース DB)に届きませんでした。";
      default:
        return "詳細を読んで対処してください。";
    }
  }
}

export interface ClientOptions {
  readonly baseUrl?: string;
  /** `null` ならトークンを送らない(`AUTH_MODE=disabled` 用)。 */
  readonly token?: string | null;
  /** テストから差し替える。 */
  readonly fetchImpl?: typeof fetch;
}

/**
 * 型を付けた薄い呼び出し口。
 *
 * **薄いままにしておく。** 判断は `src/review/` の純粋関数に置く
 * (ADR-0044 決定6)。
 */
export class ApiClient {
  private readonly baseUrl: string;
  private readonly token: string | null;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/u, "");
    this.token = options.token ?? null;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  /** トークンを差し替えた新しいクライアントを返す(**状態を持たせない**)。 */
  withToken(token: string | null): ApiClient {
    return new ApiClient({ baseUrl: this.baseUrl, token, fetchImpl: this.fetchImpl });
  }

  get hasToken(): boolean {
    return this.token !== null && this.token.length > 0;
  }

  async get<T>(path: string, query?: Record<string, string | undefined>): Promise<T> {
    return this.request<T>("GET", path, { query });
  }

  async post<T>(
    path: string,
    body?: unknown,
    query?: Record<string, string | undefined>,
  ): Promise<T> {
    return this.request<T>("POST", path, { body, query });
  }

  private async request<T>(
    method: string,
    path: string,
    options: { body?: unknown; query?: Record<string, string | undefined> },
  ): Promise<T> {
    const url = new URL(`${this.baseUrl}${path}`, globalThis.location?.origin ?? "http://localhost");
    for (const [key, value] of Object.entries(options.query ?? {})) {
      if (value !== undefined) {
        url.searchParams.set(key, value);
      }
    }

    const headers: Record<string, string> = {};
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
    }

    const response = await this.fetchImpl(url.toString(), {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });

    if (!response.ok) {
      throw new ApiError(response.status, await readDetail(response), path);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }
}

/**
 * 失敗の理由を取り出す。
 *
 * **本文が JSON でなくても落ちない。** プロキシや ingress が HTML を
 * 返すことがある(そのとき理由が読めないことを隠さない)。
 */
async function readDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null) {
      const detail = (body as Record<string, unknown>)["detail"];
      if (typeof detail === "string") {
        return detail;
      }
      return JSON.stringify(detail ?? body);
    }
    return String(body);
  } catch {
    return "(応答の本文を読めませんでした)";
  }
}
