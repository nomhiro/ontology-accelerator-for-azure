# ADR-0052: Issuesを状態の正本にし、共通エージェント指示で開発する

- ステータス: 承認済み
- 日付: 2026-10-04
- 対応タスク: `P4-10`

## コンテキスト

残作業は `docs/backlog.md` の表だけでなく、完了タスクの補足、ADRの未解決事項、
公開文書、開発環境にも分散していた。利用者は未検証事項・任意機能を含むIssue化と、
GitHub Copilotでも使える開発指示を求め、今回の指示整備とクラウドセットアップの
別Issue化を承認した。

既存の `CLAUDE.md` はCopilot CLI・クラウドエージェントでも対応するが、すべての
Chat/IDE/レビュー環境で同じファイルが読まれるわけではない。ファイルの存在と
実環境での読み込み・検査成功も別の事実である。

## 決定

1. **タスクの現在の状態はGitHub Issuesが正本。** `docs/backlog.md` は既存ID・
   出典・判断履歴・Issueリンクの索引にする。移行前の完了記録を削除しない。
   状態だけの変更を文書へ二重記録しない。
2. **IDを維持する。** 新規作業も重複しないIDを採番する。コミットにはID、
   PRにはIssueと受け入れ条件・検証の証拠を記載する。完全対応だけ `Closes`、
   部分対応は `Refs` とし、マージ前に完了扱いしない。
3. **必須・任意・検証・設計判断を区別する。** 必須はPhaseマイルストーンへ、
   任意・条件付きは `scope:optional` とし必須マイルストーンへ入れない。
   設計判断Issueの起票を機能採用の決定とみなさない。採用した実装が残る場合は
   独立Issueを作り、リンクと出典を索引に追加する。
4. **着手・ブロック・再開をIssueに記録する。** open/closedが完了状態、
   `status:in-progress` は明示した着手、`status:blocked` は具体的前提の待ち。
   単なる関連や順序を依存にしない。見送り/解決済み/意図的制約を区別し、
   却下案を未完了バグとして復活させない。
5. **共通の詳細指示は `AGENTS.md`。** 既存17不変条件・検証規律・注意事項を保持し、
   `CLAUDE.md` は共通指示をimportする入口にする。Copilot用の全体指示には重要規則を
   自己完結して載せ、パス別指示には該当領域の重点検査を置く。全文の三重管理や
   「リンクしたから自動読込される」という前提を作らない。
6. **クラウド環境と実機確認は別Issue。** 今回の指示整備だけでCopilotクラウド、
   Dev Container、実ブラウザ、Azureの未確認事項を検証済みと書かない。
   課金、権限変更、エージェント起動、外部送信は利用者の承認なしに行わない。

## 根拠

Issueには議論・担当・PR・ブロック理由・検証結果を同じ場所に残せる。
ソース側には安定したIDと設計根拠を残すことで、Issue状態の二重管理を避けながら
従来のADR・コミットからの参照を維持できる。

## 検討した代替案

- **backlogとIssueの両方で状態を更新する**: 片方だけ更新されると引き継ぎが壊れる。
  却下。索引/判断の変更だけはコードと同じコミットで更新する。
- **未決事項をすべて実装必須にする**: 任意機能がリリースを際限なく止める。
  却下。判断と実装、必須と任意を分ける。
- **巨大なCLAUDE.mdを各ツール用に全文コピーする**: 不変条件や運用が乖離する。
  却下。詳細正本・短い要約・ツール入口へ分担する。
- **今回クラウドsetup・保護設定も変更する**: 実環境検証や運用者の承認が必要で、
  起票・指示整備の範囲を超える。別Issueで扱う。

## 結果（トレードオフ・影響）

- GitHubへアクセスできない場合は状態更新ができない。結果と未更新事項を報告し、
  ローカル文書だけで完了したことにしない。
- 短い要約と共通詳細の整合は確認が必要。変更時は両方を比較する。
- `CLAUDE.md` の既存リンクは維持し、入口から詳細を辿れるようにする。
- 指示はモデルの振る舞いの補助であって、CIや認可を強制する仕組みではない。
  必須CI・レビューの実設定とクラウドの実動確認は別作業に残る。

## 一次情報

- [GitHub: custom instructions support](https://docs.github.com/en/copilot/reference/custom-instructions-support)
- [GitHub: repository instructions](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/add-custom-instructions/add-repository-instructions)
- [GitHub: cloud agent environment](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/customize-the-agent-environment)
- [Claude Code: memory and imports](https://code.claude.com/docs/en/memory)
