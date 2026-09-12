/**
 * 候補が LLM 生成かどうかを判定する(ADR-0044 決定7)。
 *
 * # なぜ最上部に出すのか
 *
 * [ADR-0043](../../../../docs/adr/0043-ontology-proposal.md) 決定10 は、生成の
 * 出自(モデル・デプロイ・API バージョン・**試行回数**)を
 * `audit_events.reason` に書くと決めた。そこには
 * **「人間のレビューを経ていない候補である」**という文字列も入る。
 *
 * 監査にそう書いてあるのだから、**レビュアが最初に見るべきものである**。
 *
 * # 試行回数を出す理由
 *
 * 1 回で通った候補と 3 回目でやっと通った候補は別物で、後者は
 * **モデルがこのスキーマを扱いかねている**信号である(ADR-0043 決定4)。
 *
 * # 文字列に依存していることの注意
 *
 * この判定は **Python 側の `ProposalResult.provenance()` が作る文字列**に
 * 依存している(`packages/api/src/ontology_api/services/proposal.py`)。
 * **両側にテストを置いて結合を固定してある** —
 * Python 側は `test_proposal_service.py`、こちら側は `provenance.test.ts`。
 *
 * 構造化された欄(`audit_events` の列)にしなかったのは、ADR-0043 決定10 が
 * 「監査は追記専用なので消えない」ことを根拠に `reason` を選んだためである。
 * **列を増やすとマイグレーションが必要になり、既存の行は埋まらない。**
 */

/**
 * `provenance()` が必ず含む印。
 *
 * **Python 側と一致していなければならない**
 * (`ProposalResult.provenance()` の先頭)。
 */
export const GENERATION_MARKER = "LLM による生成";

/** 人のレビューを経ていないことを示す印。 */
export const UNREVIEWED_MARKER = "人間のレビューを経ていない候補である";

/** 試行回数を取り出す。 */
const ATTEMPTS = /試行\s*(\d+)\s*回/u;
const MODEL = /model=([^、)\s]+)/u;
const DEPLOYMENT = /deployment=([^、)\s]+)/u;

export interface Provenance {
  /** LLM が生成した候補か。 */
  readonly generated: boolean;
  /** 何回目で検証を通ったか。**読めなければ `null`。** */
  readonly attempts: number | null;
  readonly model: string | null;
  readonly deployment: string | null;
  /**
   * 3 回以上かかったか。**モデルがこのスキーマを扱いかねている信号**
   * (ADR-0043 決定4)。読めなければ `null`。
   */
  readonly struggled: boolean | null;
}

/** LLM 生成でなかったときの値。 */
const NOT_GENERATED: Provenance = {
  generated: false,
  attempts: null,
  model: null,
  deployment: null,
  struggled: null,
};

/**
 * 監査の `reason` から出自を読む。
 *
 * **読めなかった欄は `null` にする。** 0 や既定値で埋めない —
 * 「1 回で通った」と「回数が分からない」は別物である。
 */
export function readProvenance(reason: string | null | undefined): Provenance {
  if (!reason || !reason.includes(GENERATION_MARKER)) {
    return NOT_GENERATED;
  }
  const attemptsMatch = ATTEMPTS.exec(reason);
  const attempts = attemptsMatch ? Number.parseInt(attemptsMatch[1]!, 10) : null;
  return {
    generated: true,
    attempts,
    model: MODEL.exec(reason)?.[1] ?? null,
    deployment: DEPLOYMENT.exec(reason)?.[1] ?? null,
    // **`null` を `false` に畳まない。** 分からないことを「大丈夫」に
    // 変えてはいけない。
    struggled: attempts === null ? null : attempts >= 3,
  };
}

/**
 * 画面の最上部に出す警告文。
 *
 * LLM 生成でなければ `null`(帯を出さない)。
 */
export function provenanceBanner(provenance: Provenance): string | null {
  if (!provenance.generated) {
    return null;
  }
  const parts = ["**この版は LLM が生成した候補です。人間のレビューを経ていません。**"];
  if (provenance.model) {
    parts.push(`モデル: ${provenance.model}`);
  }
  if (provenance.attempts === null) {
    // **「1 回」と書かない。** 読めなかったのだから読めなかったと書く。
    parts.push("試行回数: 監査の記録から読み取れませんでした");
  } else {
    parts.push(`試行回数: ${provenance.attempts} 回`);
    if (provenance.struggled) {
      parts.push(
        "**検証を通るまでに 3 回以上かかっています。** " +
          "モデルがこのスキーマを扱いかねている可能性があります(ADR-0043 決定4)。",
      );
    }
  }
  return parts.join(" / ");
}
