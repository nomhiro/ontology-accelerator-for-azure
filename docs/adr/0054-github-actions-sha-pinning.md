# ADR-0054: GitHub Actions を SHA で固定し Dependabot で更新する

- ステータス: 承認済み
- 日付: 2026-10-11
- 対応: `P4-18` / [Issue #48](https://github.com/nomhiro/ontology-accelerator-for-azure/issues/48)
- 関連: [ADR-0049](0049-license-scanning-in-ci.md)、[ADR-0052](0052-issue-based-development.md)、[ADR-0053](0053-main-branch-protection.md)

## コンテキスト

`P4-04` で GitHub Actions の各参照を最新メジャーへ更新したが、参照はタグのままで、
タグが後から移動した場合に実行コードをレビュー時点の内容へ固定できなかった。
GitHub は Action の参照に完全な commit SHA を使う方法を最も安全な選択肢として
示している。一方、SHA を手で追い続けると更新漏れの恐れがあるため、更新 PR を
自動生成する仕組みと合わせて決める必要がある。

対象は `.github/workflows/ci.yml` と `.github/workflows/copilot-setup-steps.yml` の
外部 Action に限る。プロジェクトの npm/pnpm や Python/uv 依存の更新方針は別課題とする。

## 決定

1. **workflow の外部 Action は、確認済みの完全な 40 文字 commit SHA で固定する。**
   参照の後ろには Dependabot の版解決に使えるリリースタグまたは版番号を
   `# vX` / `# vX.Y.Z` コメントで残す。SHA とコメントは upstream のタグを照合して
   更新し、タグが存在しない Action に推測で版を付けない。
2. **Dependabot の version updates は `github-actions` ecosystem のみとし、週次で
   `/` を走査する。** `/` は `.github/workflows` の Action 参照を対象にするために
   指定する。Dependabot は確認可能な更新 PR を作成するが、自動マージは設定しない。
3. **更新 PR は通常の pull request として扱う。** マージ前に既存 CI の実行結果を
   確認し、人によるレビューを推奨する。レビューを required にする人数は 0 とし、
   branch protection 上のレビュー必須化は行わない（ADR-0053 決定3に従う）。
4. **workflow の権限と fork PR 経路は変更しない。** 現在の `pull_request` を維持し、
   `pull_request_target` / `workflow_run` へ変更しない。`GITHUB_TOKEN` の読み取り専用
   を保ち、Dependabot 自身に追加の書き込み権限を与えない。
5. **npm/pnpm と Python/uv の依存、lockfile は対象外である。** `pnpm-lock.yaml` は
   `lockfileVersion: '9.0'` のまま保持し、workflow の `pnpm/action-setup` は pnpm 9
   を使い続ける。Actions の SHA 変更で lockfile を書き換えない。

## 根拠

タグ参照は、レビュー後にタグが動けば実行されるコードも変わる。commit SHA なら
レビューした参照先を固定できる。Dependabot は GitHub Actions の version update を
週次 PR として提案し、版コメントを伴う SHA 参照も追跡できるため、手動固定だけに
比べて更新漏れを減らせる。自動マージは採用せず、CI の実行結果を確認する運用を保つ。

## 検討した代替案

- **メジャーまたはリリースタグのままにする**: 設定は読みやすいが、タグが移動した
  場合に実行コードが変わり、レビューしたコードを特定できない。却下。
- **SHA に固定し手動で更新する**: 実行コードは固定できるが、更新確認が人の記憶に
  依存し、古い Action が残りやすい。却下。
- **Dependabot で全 package ecosystem を更新する**: npm/pnpm と Python/uv の更新は
  lockfile・依存互換性の別方針が必要で、P4-18 の対象外である。今回まとめて有効化しない。
- **Dependabot PR を自動マージする**: Action の更新内容や CI 結果を人が確認する境界を
  外す。却下。
- **PR のレビュー人数を必須化する**: ADR-0053 がレビュー人数を 0 と決定しており、
  今回それを覆す理由も権限変更の承認もない。人手レビューを推奨にとどめる。

## 結果（トレードオフ・影響）

- `.github/workflows/ci.yml` と `.github/workflows/copilot-setup-steps.yml` の外部 Action
  は確認した SHA に固定される。次回以降の更新は Dependabot PR で提案される。
- アクションの版コメントは読みやすさと Dependabot の版追跡のため残す。コメントの
  タグ自体を workflow が実行するわけではない。
- 更新 PR の自動マージや required review はなく、CI とレビューを確認する責任は
  リポジトリの通常運用に残る。
- GitHub Actions とローカル lockfile の更新周期は別々になる。`pnpm/action-setup` の
  更新時は pnpm の版と lockfile の互換性を別途確認する。
- この変更は GitHub の branch protection、Dependabot alerts、Repository settings を
  変更しない。PR上での実行確認はマージ前に GitHub hosted CI で行う必要がある。

## 一次情報

- [GitHub Docs: Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)（Action を完全な SHA で参照する）
- [GitHub Docs: Keeping your actions up to date with Dependabot](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/auto-update-actions)（`github-actions`、`directory: "/"`、schedule と SHA 参照の更新 PR）
- [GitHub Docs: Dependabot options reference](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference)（version update schedule）
- [Dependabot Core #7913](https://github.com/dependabot/dependabot-core/issues/7913#issuecomment-2748641653)（SHA と `# vX.Y.Z` コメントを更新する挙動の説明）
