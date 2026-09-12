/**
 * 承認できるか、できないなら理由(ADR-0044 決定3)。
 *
 * 固定するのは 3 つ。
 *
 * 1. **押せないときは必ず理由がある**(無効なボタンだけを出さない)
 * 2. **四眼原則を確かめられないときは「できる」と言い切らない**
 * 3. **自分が公開した版は自分では承認できない**(不変条件12)
 */

import { describe, expect, it } from "vitest";

import {
  type ApprovalContext,
  UNKNOWN_PRINCIPAL_NOTE,
  type VersionForReview,
  type VersionStatus,
  availableActions,
  principalIdFromToken,
} from "./approval";

const ME = "me-oid";
const SOMEONE_ELSE = "other-oid";

function version(status: VersionStatus, createdBy = SOMEONE_ELSE): VersionForReview {
  return { version: "1.0.0", status, created_by: createdBy };
}

function context(overrides: Partial<ApprovalContext> = {}): ApprovalContext {
  return { principalId: ME, requireTwoPersonApproval: true, ...overrides };
}

function find(
  actions: readonly { action: string; enabled: boolean; reason: string | null }[],
  name: string,
) {
  const found = actions.find((a) => a.action === name);
  if (!found) {
    throw new Error(`${name} が返っていない`);
  }
  return found;
}

describe("availableActions", () => {
  it("押せない操作にも必ず理由がある", () => {
    // **これが決定3 の本体である。** 無効なボタンだけを出すと、
    // レビュアは壊れていると思う。
    for (const status of ["draft", "in-review", "approved", "superseded", "rejected"] as const) {
      for (const action of availableActions(version(status), context())) {
        if (!action.enabled) {
          expect(action.reason, `${status} の ${action.action} に理由が無い`).toBeTruthy();
        }
      }
    }
  });

  it("操作の一覧は状態によらず 3 つ返る", () => {
    // **消してしまうと「そんな操作は無い」と読まれる。**
    for (const status of ["draft", "in-review", "approved"] as const) {
      expect(availableActions(version(status), context())).toHaveLength(3);
    }
  });

  it("自分が公開した版は自分では承認できない", () => {
    const actions = availableActions(version("in-review", ME), context());
    const approve = find(actions, "approve");
    expect(approve.enabled).toBe(false);
    expect(approve.reason).toContain("あなたが公開した版");
    // **platform-admin でも飛び越えられないことを書く**(不変条件12)。
    expect(approve.reason).toContain("platform-admin");
  });

  it("他人が公開した版は承認できる", () => {
    const approve = find(availableActions(version("in-review", SOMEONE_ELSE), context()), "approve");
    expect(approve.enabled).toBe(true);
    expect(approve.reason).toBeNull();
  });

  it("四眼原則が無効なら自分の版でも承認できる", () => {
    const approve = find(
      availableActions(version("in-review", ME), context({ requireTwoPersonApproval: false })),
      "approve",
    );
    expect(approve.enabled).toBe(true);
    expect(approve.reason).toBeNull();
  });

  it("主体が分からないときは「できる」と言い切らない", () => {
    // **押すと 422 になることがある。** 画面が確かめられないことを伝える。
    const approve = find(
      availableActions(version("in-review", ME), context({ principalId: null })),
      "approve",
    );
    expect(approve.enabled).toBe(true);
    expect(approve.reason).toBe(UNKNOWN_PRINCIPAL_NOTE);
    expect(approve.reason).toContain("422");
  });

  it("draft は提出できるが承認できない", () => {
    const actions = availableActions(version("draft"), context());
    expect(find(actions, "submit").enabled).toBe(true);
    expect(find(actions, "approve").enabled).toBe(false);
    expect(find(actions, "approve").reason).toContain("submit");
  });

  it("approved を再提出できない", () => {
    const actions = availableActions(version("approved"), context());
    expect(find(actions, "submit").enabled).toBe(false);
    expect(find(actions, "approve").enabled).toBe(false);
    expect(find(actions, "reject").enabled).toBe(false);
  });

  it("in-review なら却下できる", () => {
    expect(find(availableActions(version("in-review"), context()), "reject").enabled).toBe(true);
  });
});

describe("principalIdFromToken", () => {
  function token(claims: Record<string, unknown>): string {
    const payload = btoa(JSON.stringify(claims)).replace(/\+/g, "-").replace(/\//g, "_");
    return `header.${payload}.signature`;
  }

  it("oid を読む", () => {
    expect(principalIdFromToken(token({ oid: "abc-123" }))).toBe("abc-123");
  });

  it("トークンが無ければ null", () => {
    expect(principalIdFromToken(null)).toBeNull();
    expect(principalIdFromToken("")).toBeNull();
  });

  it("形が違えば推測しない", () => {
    // **黙って推測しない。** 読めなければ分からないままにする。
    expect(principalIdFromToken("not-a-jwt")).toBeNull();
    expect(principalIdFromToken("a.not-base64!.c")).toBeNull();
  });

  it("oid が無ければ null", () => {
    expect(principalIdFromToken(token({ sub: "x" }))).toBeNull();
  });

  it("oid が空文字なら null", () => {
    // 空文字を主体 ID として扱うと、`created_by` が空の版と一致してしまう。
    expect(principalIdFromToken(token({ oid: "" }))).toBeNull();
  });
});
