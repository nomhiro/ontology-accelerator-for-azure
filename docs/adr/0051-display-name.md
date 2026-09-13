# ADR-0051: 表示名を `Ontology Accelerator for Azure` に確定する

- 状態: 採択
- 日付: 2026-09-13
- 関連: `P4-05`、[ADR-0008](0008-independent-implementation.md)（R8）

## 文脈

[ADR-0008](0008-independent-implementation.md) は商標について 2 つの制約を挙げていた。

1. **"AWS" / "Amazon" をプロジェクト名に使えない**（Apache-2.0 §6 は商標権を許諾しない）
2. **"Azure" も同じ論点を持つ**（R8）。「OSS 名の先頭に "Azure" を置くのは Microsoft の商標ガイドライン上グレー」として、表示名 `Ontology Accelerator for Azure` を**暫定**とし、判断を先送りしていた

`P4-05` はこの先送りを解く。**公開（`P4-08` の v0.1.0 リリース）の前に確定させる必要がある** — 名前は README・`azure.yaml` の `template`・GitHub Pages の URL・awesome-azd の申請（`P4-07`）に伝播し、後から変えると外部のリンクが壊れる。

## 一次情報

Microsoft は「自分のアプリ名に Microsoft の製品名を入れてよいか」に**明文で答えている**（[Trademark and copyright protection](https://learn.microsoft.com/windows/apps/publish/partner-center/trademark-and-copyright-protection) の Q2。2026-09-13 に確認）。

> Microsoft strongly prefers that you do not. But if there is a clear business necessity, you may do so if you follow these guidelines:
>
> 1. The name of your app **should not begin with** the Microsoft product or service name at issue. For example, "Xbox Points Calculator" should not be used as an app name. **"Bob's Points Calculator for Xbox" is a better name.** This makes it clear that the app is not distributed by Microsoft.
> 2. Use language that explains that your app was designed to work in conjunction with a Microsoft product or service. For example, **"works with," "for," "designed for," and "optimized for" are all acceptable terms**. Use of terms such as "certified," "official," "authentic," and "licensed" imply legal affiliation or overt endorsement and thus **must not be used** absent a formal trademark license agreement with Microsoft.
> 3. Use the Microsoft product/service name **only in plain text**. Do not use any fancy fonts or accompanying graphics.

**これは推測ではなく、Microsoft が例示した「良い名前」の型そのものである。**

## 決定

### 決定1: 表示名は `Ontology Accelerator for Azure` で確定する。「暫定」の注記を外す

上の 3 条件に**そのまま適合している**。

| 条件 | 現行の表示名 | 判定 |
|---|---|---|
| 製品名で**始まらない** | `Ontology Accelerator` で始まる | **適合**（Microsoft の例 `Bob's Points Calculator for Xbox` と同じ型） |
| 許容される語を使う | `for` を使っている | **適合** |
| `certified` / `official` / `authentic` / `licensed` を使わない | 使っていない（機械的に確認した） | **適合** |
| 平文で書く | ロゴ・装飾フォントを使っていない（アーキ図は Mermaid の平文） | **適合** |

**ADR-0008 の「グレーである」という評価は、一次情報を読む前の推定だった。** 実際にグレーなのは**製品名で始める形**（`Azure Ontology Accelerator`）であって、`... for Azure` は Microsoft 自身が推奨している形である。

### 決定2: リポジトリのスラッグ `ontology-accelerator-for-azure` はそのままにする

GitHub のスラッグは `ontology-accelerator-for-azure` で、表示名と同じ語順である。**`git clone` が作るディレクトリ名もこれになる**ので、利用者が触る名前はすべて適合している。

**変えない理由**: GitHub Pages の URL（`https://nomhiro.github.io/ontology-accelerator-for-azure/`）が README と外部から参照されている。リダイレクトは GitHub が張るが、**「暫定だから変える」という理由がもう無い**。

### 決定3: ローカルの作業ディレクトリ名（`azure-ontology-accelerator`）は成果物ではないので追わない

これは開発者のマシン上のパスであって、**配布物にも公開物にも現れない**。`git clone` は `ontology-accelerator-for-azure` を作る。

**ADR-0008 が「これも再考の余地がある」と書いていた点は、ここで閉じる** — 再考の対象ではなく、**そもそも公開されないもの**である。

### 決定4: 非提携の明記は維持する。加えて「AWS 版の Azure 版」という名乗り方をしない

README の

> 本プロジェクトは Amazon Web Services および Microsoft とは提携・承認・スポンサー関係にありません。AWS, Amazon, Azure, Microsoft は各社の商標です。

を維持する。これは決定1 の条件のうち「Microsoft が配布しているものではないと明らかにする」を補強する。

**「AWS Context Ontology Accelerator の Azure 版」のような表現は使わない**（ADR-0008 のまま）。AWS 版との関係は README の「AWS 版との関係」節で**事実として**説明する（何を参考にし、何が違うか）。

## 却下した案

| 案 | 却下の理由 |
|---|---|
| `Azure Ontology Accelerator`（先頭に Azure） | **Microsoft が明示的に避けるよう書いている形**（`Xbox Points Calculator` の例）。Microsoft が配布しているものと誤認されうる |
| 固有の造語を新しく付ける（例: `Kotodama`、`Semantica`） | **識別性は上がるが、検索で見つからなくなる。** この製品の利用者は「Azure でオントロジーを扱う方法」を探している。**一次情報が現行の型を認めているのに、探しにくい名前へ変える理由が無い** |
| `Ontology Accelerator`（Azure を外す） | **何の上で動くのかが名前から消える。** AWS 版と紛れる（あちらは `Context Ontology Accelerator`）うえ、azd テンプレートとしての位置づけも伝わらない |
| `Context Ontology Accelerator for Azure`（AWS 版の語をそろえる） | **AWS 版の製品名をほぼそのまま使うことになる。** ADR-0008 が避けると決めた形である |

## 帰結

- **`P4-08`（v0.1.0 リリース）と `P4-07`（awesome-azd 申請）の前提が固まった。** 名前が変わる前提で書いていた注記を外せる
- **README・ADR-0008 の「暫定」の記述を消した。** 先送りの記録が残っていると、後任が同じ調査を繰り返す（この ADR 群が避けようとしているもの）
- **禁止語（`certified` / `official` / `authentic` / `licensed`）を使わない制約が恒久的に残る。** 現時点では守れていることを確認したが、**機械的な検査は置いていない**（バックログに `P4-09` として追記した）
