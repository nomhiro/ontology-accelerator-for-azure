/**
 * 1 つの版をレビューする画面(ADR-0044、`P2A-03`)。
 *
 * **判断はここに書かない。** 差分の状態・承認の可否・出自の読み取りはすべて
 * `diff.ts` / `approval.ts` / `provenance.ts` の純粋関数にあり、テストされて
 * いる(ADR-0044 決定6)。ここは**その結果を並べるだけ**である。
 *
 * 並べる順序には理由がある(ADR-0044 決定4・7)。
 *
 * 1. **LLM 生成の帯**(最上部。人間のレビューを経ていない)
 * 2. **差分が計算できなかった警告**(「変更が無い」と読ませない)
 * 3. **消えた用語**(`approve` を 422 で止める。押す前に知る)
 * 4. 廃止・追加・変更
 * 5. 操作(押せない理由付き)
 */

import {
  Badge,
  Body1,
  Button,
  Card,
  CardHeader,
  Field,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Subtitle1,
  Textarea,
  Title3,
  makeStyles,
  tokens,
} from "@fluentui/react-components";
import { useState } from "react";

import {
  type ActionName,
  type ApprovalContext,
  type VersionForReview,
  availableActions,
} from "./approval";
import { type DiffPresentation, hasRemovedTerms } from "./diff";
import { type Provenance, provenanceBanner } from "./provenance";

const useStyles = makeStyles({
  stack: {
    display: "flex",
    flexDirection: "column",
    rowGap: tokens.spacingVerticalM,
  },
  terms: {
    margin: 0,
    paddingLeft: tokens.spacingHorizontalXL,
    color: tokens.colorNeutralForeground2,
    fontFamily: tokens.fontFamilyMonospace,
    fontSize: tokens.fontSizeBase200,
  },
  actions: {
    display: "flex",
    flexWrap: "wrap",
    columnGap: tokens.spacingHorizontalM,
    rowGap: tokens.spacingVerticalS,
    alignItems: "flex-start",
  },
  reason: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
  },
});

export interface ReviewPanelProps {
  readonly version: VersionForReview;
  readonly diff: DiffPresentation;
  readonly provenance: Provenance;
  readonly context: ApprovalContext;
  /** 監査に入る理由。**画面では必須**(ADR-0044 決定8)。 */
  readonly onAct: (action: ActionName, reason: string) => void;
  readonly busy?: boolean;
}

/** 用語の一覧。**件数と一覧が食い違うことを隠さない。** */
function TermList({
  label,
  terms,
  count,
  truncated,
}: {
  label: string;
  terms: readonly string[];
  count: number;
  truncated: boolean;
}) {
  const styles = useStyles();
  if (count === 0) {
    return <Body1>{label}: なし</Body1>;
  }
  return (
    <div>
      <Body1>
        {label}: {count} 件
        {truncated && terms.length < count ? `(先頭 ${terms.length} 件のみ表示)` : ""}
      </Body1>
      <ul className={styles.terms}>
        {terms.map((term) => (
          <li key={term}>{term}</li>
        ))}
      </ul>
    </div>
  );
}

/** 差分の提示。**4 つの状態を別の見た目にする**(ADR-0044 決定1)。 */
function DiffSection({ diff }: { diff: DiffPresentation }) {
  const styles = useStyles();

  if (diff.kind === "no-base") {
    return (
      <MessageBar intent="info">
        <MessageBarBody>
          <MessageBarTitle>この名前空間の最初の版です</MessageBarTitle>
          比較する基準がないため差分はありません。**変更が無いという意味ではありません。**
        </MessageBarBody>
      </MessageBar>
    );
  }

  if (diff.kind === "not-computed") {
    // **これが ADR-0044 決定1 の本体である。**
    // 空の差分と同じ見た目にしない。件数を出さない。
    return (
      <MessageBar intent="warning">
        <MessageBarBody>
          <MessageBarTitle>差分を計算していません</MessageBarTitle>
          {diff.message}
        </MessageBarBody>
      </MessageBar>
    );
  }

  if (diff.kind === "empty") {
    return (
      <MessageBar intent="success">
        <MessageBarBody>
          <MessageBarTitle>変更はありません</MessageBarTitle>
          計算した結果、追加も削除もありませんでした。
        </MessageBarBody>
      </MessageBar>
    );
  }

  return (
    <div className={styles.stack}>
      {diff.removedTermCount > 0 && (
        // **最も目立たせる**(ADR-0044 決定4)。`approve` が 422 で止める。
        <MessageBar intent="error">
          <MessageBarBody>
            <MessageBarTitle>用語が消えています({diff.removedTermCount} 件)</MessageBarTitle>
            現行版にあった用語を消した版は `approve` が 422 で拒否します(不変条件8)。
            縮めるなら `owl:deprecated true` と後継(`dcterms:isReplacedBy`)を書いてください。
          </MessageBarBody>
        </MessageBar>
      )}
      <TermList
        label="消えた用語"
        terms={diff.removedTerms}
        count={diff.removedTermCount}
        truncated={diff.truncated}
      />
      <TermList
        label="廃止された用語"
        terms={diff.deprecatedTerms}
        count={diff.deprecatedTermCount}
        truncated={diff.truncated}
      />
      <TermList
        label="追加された用語"
        terms={diff.addedTerms}
        count={diff.addedTermCount}
        truncated={diff.truncated}
      />
      {diff.modifiedTerms === null ? (
        // **0 件と書かない**(ADR-0016 が区別したもの)。
        <MessageBar intent="warning">
          <MessageBarBody>変更された用語は測っていません(0 件ではありません)。</MessageBarBody>
        </MessageBar>
      ) : (
        <TermList
          label="変更された用語"
          terms={diff.modifiedTerms}
          count={diff.modifiedTermCount ?? diff.modifiedTerms.length}
          truncated={diff.truncated}
        />
      )}
      <Body1 className={styles.reason}>
        トリプル: 追加 {diff.addedTripleCount ?? "不明"} / 削除{" "}
        {diff.removedTripleCount ?? "不明"}
      </Body1>
    </div>
  );
}

const ACTION_LABELS: Record<ActionName, string> = {
  submit: "提出する (submit)",
  approve: "承認する (approve)",
  reject: "却下する (reject)",
};

export function ReviewPanel({
  version,
  diff,
  provenance,
  context,
  onAct,
  busy = false,
}: ReviewPanelProps) {
  const styles = useStyles();
  const [reason, setReason] = useState("");
  const actions = availableActions(version, context);
  const banner = provenanceBanner(provenance);
  const removed = hasRemovedTerms(diff);

  return (
    <div className={styles.stack}>
      <Title3>
        {version.version} <Badge appearance="outline">{version.status}</Badge>
      </Title3>

      {banner !== null && (
        // **最上部に出す**(ADR-0044 決定7)。
        <MessageBar intent="warning">
          <MessageBarBody>
            <MessageBarTitle>LLM が生成した候補です</MessageBarTitle>
            {banner}
          </MessageBarBody>
        </MessageBar>
      )}

      <Card>
        <CardHeader header={<Subtitle1>差分</Subtitle1>} />
        <DiffSection diff={diff} />
      </Card>

      {removed === null && (
        // **「消えた用語は無い」と言わない。** 測っていないので言えない。
        <MessageBar intent="warning">
          <MessageBarBody>
            用語が消えているかは確かめられていません(差分を計算していないため)。
            承認は 422 で拒否されることがあります。
          </MessageBarBody>
        </MessageBar>
      )}

      <Card>
        <CardHeader header={<Subtitle1>操作</Subtitle1>} />
        <div className={styles.stack}>
          <Field
            label="理由(必須)"
            hint="監査証跡に残り、消せません(ADR-0009 決定7)。何を確認したかを書いてください。"
            required
          >
            <Textarea
              value={reason}
              onChange={(_, data) => setReason(data.value)}
              resize="vertical"
            />
          </Field>
          <div className={styles.actions}>
            {actions.map((action) => (
              <div key={action.action}>
                <Button
                  appearance={action.action === "approve" ? "primary" : "secondary"}
                  // **理由が空なら押せない**(ADR-0044 決定8)。
                  disabled={!action.enabled || busy || reason.trim().length === 0}
                  onClick={() => onAct(action.action, reason.trim())}
                >
                  {ACTION_LABELS[action.action]}
                </Button>
                {action.reason !== null && (
                  // **押せない理由を必ず出す**(ADR-0044 決定3)。
                  <Body1 className={styles.reason}>{action.reason}</Body1>
                )}
              </div>
            ))}
          </div>
          {reason.trim().length === 0 && (
            <Body1 className={styles.reason}>
              理由を書くと操作できます。**API では任意ですが、人が操作する経路では必須にしています**
              — 理由が空の監査は説明になりません。
            </Body1>
          )}
        </div>
      </Card>
    </div>
  );
}
