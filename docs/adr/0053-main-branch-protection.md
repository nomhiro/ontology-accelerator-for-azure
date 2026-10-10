# ADR-0053: main の保護・必須 CI・レビューのマージ方針

- ステータス: 承認済み
- 日付: 2026-10-10
- 対応: `P4-14` / [Issue #46](https://github.com/nomhiro/ontology-accelerator-for-azure/issues/46)
- 関連: `P4-13` / [Issue #41](https://github.com/nomhiro/ontology-accelerator-for-azure/issues/41)、`P4-20` / [Issue #60](https://github.com/nomhiro/ontology-accelerator-for-azure/issues/60)、[ADR-0052](0052-issue-based-development.md)

## コンテキスト

Issue #46 の開始時点で main は branch protection 未設定で、repository ruleset も
存在しなかった。2026-10-10 の再確認でも、branch protection API は
`Branch not protected` (HTTP 404)、ruleset API は空配列だった。

`.github/workflows/ci.yml` は `changes` job でパスを分類し、Python、Web、infra、
containers、reasoner、VKG、shell の7 jobを条件付きで実行する。main の CI run は
後続 run にキャンセルされないが、条件付き job の skip は成功状態として報告される。
required status check にこれらの個別 job を指定しても、必要な job が実行された
ことを一つの安定した結果として保証できない。現在、常時実行の集約 check は無い。

保護設定はリポジトリのマージ権限を変えるため、方針の決定と live な設定変更を
分離する。今回の作業は方針と適用手順までとし、設定は別途の明示承認後に行う。

## 決定

1. **main への通常の変更は pull request 経由にする。** main への直接 push、
   force push、削除を禁止する。GitHub ruleset の対象は `refs/heads/main` とする。
2. **必須 status check は一つの常時実行 CI 集約 check とする。** `changes` が成功し、
   filter が `true` の job は成功していなければならない。filter が `false` の job は
   `skipped` を許可する。必要 job の skip、failure、cancelled、missing、結果の
   判定不能は失敗とする。各 job の skip を GitHub の既定動作に委ねない。
   この集約 check は未実装であり、[P4-20 / #60](https://github.com/nomhiro/ontology-accelerator-for-azure/issues/60)
   で実装・検証する。完了前に ruleset の必須 check として有効化しない。
3. **必須の人手レビュー人数は 0 とする。** 人によるレビューは推奨するが、承認数を
   マージ条件にしない。会話解決など、別の未合意ルールを追加しない。
4. **fork の pull request にも通常の必須 CI を適用する。** `pull_request` で走る検査は
   secrets や書き込み権限を必要としないものにする。ワークフローの権限は読み取り専用
   を基本とし、必須 check のために fork PR の権限を拡張しない。
5. **管理者の bypass は緊急時に限る。** ruleset の bypass actor は管理者だけとし、
   **For pull requests only** を選ぶ。これは管理者にも pull request を要求する一方、
   pull request 上で required check 等の保護を bypass できる設定である。直接 push を
   許す **Always allow** は選ばない。設定だけでは緊急性を判定できないため、bypass の
   理由、対象、時刻、復旧策を記録し、可能な限り直後に通常の検査を通して追認または
   修正する。
6. **ruleset の適用・実 PR 検証は別途承認後に行う。** `P4-20` 完了後、実際の pull
   request で、必要な check の失敗時にマージが止まり、全条件を満たすとマージ可能に
   なることを確認する。今回の決定や文書化を設定変更の許可とみなさない。

## 適用・移行手順

1. [P4-20 / #60](https://github.com/nomhiro/ontology-accelerator-for-azure/issues/60) を完了し、
   変更なしによる正当な skip、各 filter に対応した job の成功、必要 job の skip、
   failure、cancelled、結果欠落・判定不能を確認する。fork PR でも秘密情報なしに
   同じ判定が動くことを確認する。
2. 成功した PR の check run から集約 check の正確な表示名を取得する。推測で
   ruleset の check 名を入力しない。
3. 運用者の明示承認後、GitHub ruleset を `main` に適用する。pull request を必須にし、
   集約 check のみを required status check に指定する。必須レビュー人数は設定しない。
   管理者だけを bypass actor にし、bypass mode は **For pull requests only** とする。
   `Always allow` とその他の bypass actor は設定しない。
4. 実 PR を使い、集約 check が失敗・未実施のときにマージを拒否し、全対象検査が
   成功または正当な skip のときにマージ可能になることを確認する。マージ可能性の
   確認にテスト PR の実マージは不要とする。
5. ruleset の設定内容と検証結果を Issue #46 に記録する。設定が誤って通常作業を
   妨げる場合は、管理者が変更理由を記録して修正または一時無効化し、直ちに再検証する。
   緊急 bypass を使った場合も同様に記録し、通常の保護を復旧する。

## 根拠

条件付き job の個別 check では、「この差分でその検査が不要だった」ことと
「必要な検査が実行されなかった」ことを required check が区別できない。変更内容に応じた
検査の選択は維持しつつ、一つの必須 check に選択と結果の両方を検証させる。

レビュー人数を 0 とすることで、人手レビューの運用を必須化せずに CI による検査を
一貫してマージ条件にする。緊急時の例外は完全に排除しないが、通常ルールとの区別を
監査可能な記録に残す。

## 検討した代替案

- **現在の各 job を個別に必須化する**: filter による正当な skip が成功扱いになり、
  必要な検査が実行されていない PR を止められない。却下。
- **全 job を全 PR で常時実行する**: 差分ごとの検査選択を捨て、時間・計算資源を
  不要に消費する。却下。
- **1人以上のレビューを必須にする**: 人による承認をマージ条件にすることは今回の
  要件ではない。却下。
- **管理者にも bypass を認めない**: 緊急対応の経路を失う。却下。
- **管理者に Always allow を与える**: 緊急時以外も直接 push により pull request 条件を
  回避できる。PR-only bypass で直接 push を認めず、緊急時の check bypass を残す。
- **今回の作業で ruleset を有効化する**: ユーザーは文書化までを選択し、GitHub設定は
  別途承認後とした。実際の aggregate check もまだ無い。対象外。

## 結果（トレードオフ・影響）

- main の保護方針は決まったが、live 設定は未適用である。保護状態は引き続き
  GitHub の branch/ruleset 設定を読み直して確認する。
- required check を設定する前に `P4-20` の実装と実 PR による失敗/成功の検証が必要。
- 人手レビューが自動的に要求されないため、変更のレビュー品質は運用上の推奨に委ねる。
- 管理者 bypass は機能的に緊急限定できない可能性があり、緊急時のみ使う運用規則と
  事後記録で補う。PR-only mode により、ruleset bypass による直接 push は許さない。
- `P4-13` の paths-filter 依存関係修正は完了済みだが、常時実行の fail-closed 集約
  check は別作業として `P4-20` に記録した。

## 一次情報

- [GitHub Docs: Available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)（pull request、required status checks、force push、deletion）
- [GitHub Docs: Creating rulesets for a repository](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)（`For pull requests only` は直接 push を許可せず、PR上で保護を bypass できる）
- [GitHub Docs: Using conditions to control job execution](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-jobs-with-conditions)（条件で skip された job は required check でも成功扱い）
