/**
 * 何ができて、できないなら理由は何かを決める(ADR-0044 決定3)。
 *
 * # 無効なボタンだけを出さない
 *
 * 四眼原則(不変条件12、[ADR-0014](../../../../docs/adr/0014-namespace-rbac.md)
 * 決定5)により、**自分が publish した版は自分では承認できない**。
 * `platform-admin` でも飛び越えられない。
 *
 * 無効化したボタンだけを出すと、レビュアは**壊れていると思う**。
 *
 * # 「押せない」と「押してよいか分からない」を区別する
 *
 * 画面は自分の主体 ID を確実には知らない(`AUTH_MODE=disabled` では
 * トークンが無い)。そのとき**「承認できる」と言ってはいけない** —
 * 押すと 422 になることがある。**分からないと言う。**
 */

/** 版の状態(`OntologyVersionStatus` と同じ値)。 */
export type VersionStatus = "draft" | "in-review" | "approved" | "superseded" | "rejected";

/** 画面から行える操作。 */
export type ActionName = "submit" | "approve" | "reject";

export interface ActionAvailability {
  readonly action: ActionName;
  /** 押せるか。**`false` なら必ず `reason` がある。** */
  readonly enabled: boolean;
  /**
   * 押せない理由、または**押した結果が確かめられないことの注意**。
   *
   * `enabled` が `true` でも入ることがある(四眼原則を確かめられない場合)。
   */
  readonly reason: string | null;
}

export interface VersionForReview {
  readonly version: string;
  readonly status: VersionStatus;
  /** publish した主体。四眼原則の判定に使う。 */
  readonly created_by: string;
}

export interface ApprovalContext {
  /**
   * 画面を見ている主体の ID。**分からなければ `null`。**
   *
   * トークンの `oid` クレームから取る。**署名は検証しない** — これは
   * 説明のためだけに使い、**権限の判断には使わない**(判断は API が行う)。
   */
  readonly principalId: string | null;
  /** 名前空間が四眼原則を要求しているか。 */
  readonly requireTwoPersonApproval: boolean;
}

/** 四眼原則を確かめられないときの注意文。 */
export const UNKNOWN_PRINCIPAL_NOTE =
  "ログイン中の主体が分からないため、四眼原則(自分が公開した版は自分では承認できない)を" +
  "画面では確かめられません。**押すと 422 で拒否されることがあります。**";

/**
 * その版に対して行える操作を返す。
 *
 * **返す配列の長さは状態によらず一定である。** 押せない操作も理由付きで
 * 返す — 消してしまうと「そんな操作は無い」と読まれる。
 */
export function availableActions(
  version: VersionForReview,
  context: ApprovalContext,
): readonly ActionAvailability[] {
  return [
    submitAvailability(version),
    approveAvailability(version, context),
    rejectAvailability(version),
  ];
}

function submitAvailability(version: VersionForReview): ActionAvailability {
  if (version.status === "draft") {
    return { action: "submit", enabled: true, reason: null };
  }
  return {
    action: "submit",
    enabled: false,
    reason: `提出できるのは draft の版だけです(この版は ${version.status})。`,
  };
}

function approveAvailability(
  version: VersionForReview,
  context: ApprovalContext,
): ActionAvailability {
  if (version.status !== "in-review") {
    return {
      action: "approve",
      enabled: false,
      reason:
        version.status === "draft"
          ? "まだ提出されていません。先に submit してレビューへ回してください。"
          : `承認できるのは in-review の版だけです(この版は ${version.status})。`,
    };
  }
  if (!context.requireTwoPersonApproval) {
    return { action: "approve", enabled: true, reason: null };
  }
  if (context.principalId === null) {
    // **「できる」と言い切らない。** 確かめられないことを伝える。
    return { action: "approve", enabled: true, reason: UNKNOWN_PRINCIPAL_NOTE };
  }
  if (context.principalId === version.created_by) {
    return {
      action: "approve",
      enabled: false,
      reason:
        "**あなたが公開した版です。** 四眼原則により、別の主体が承認します" +
        "(platform-admin でも飛び越えられません)。",
    };
  }
  return { action: "approve", enabled: true, reason: null };
}

function rejectAvailability(version: VersionForReview): ActionAvailability {
  if (version.status === "in-review") {
    return { action: "reject", enabled: true, reason: null };
  }
  return {
    action: "reject",
    enabled: false,
    reason: `却下できるのは in-review の版だけです(この版は ${version.status})。`,
  };
}

/**
 * トークンから主体の ID を取り出す(ADR-0044 決定3)。
 *
 * **署名を検証しない。** これは画面の説明にしか使わず、**権限の判断は
 * API が行う**。検証しない値で表示を変えても、できることは変わらない。
 *
 * 読めなければ `null` を返す。**推測しない。**
 */
export function principalIdFromToken(token: string | null): string | null {
  if (!token) {
    return null;
  }
  const parts = token.split(".");
  if (parts.length < 2) {
    return null;
  }
  try {
    const payload = parts[1]!.replace(/-/g, "+").replace(/_/g, "/");
    const padded = payload + "=".repeat((4 - (payload.length % 4)) % 4);
    const claims: unknown = JSON.parse(atob(padded));
    if (typeof claims !== "object" || claims === null) {
      return null;
    }
    const oid = (claims as Record<string, unknown>)["oid"];
    return typeof oid === "string" && oid.length > 0 ? oid : null;
  } catch {
    // **黙って推測しない。** 読めなければ分からないままにする。
    return null;
  }
}
