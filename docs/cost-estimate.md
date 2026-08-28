# 月額費用試算

> **これは見積りです。** 実際の課金額は使用状況、為替、価格改定、サブスクリプションの契約形態(EA / CSP / 従量課金)により変動します。本ドキュメントの数値をもって課金額を保証するものではありません。

## 単価の出典

| 項目 | 内容 |
|---|---|
| 出典 | **Azure Retail Prices API** — `https://prices.azure.com/api/retail/prices` |
| リージョン | **Japan East** |
| 取得時点 | **2026-08** |
| 通貨 | USD(すべて retail 価格。割引契約・予約は考慮していません) |

### 根拠単価(API 取得値)

| リソース | 単価 |
|---|---|
| ACA Consumption vCPU (active) | `$0.000024 / vCPU秒` |
| ACA Consumption vCPU (idle) | `$0.000003 / vCPU秒` |
| ACA Consumption メモリ | `$0.000003 / GiB秒` |
| ACA 無料枠 | **180,000 vCPU秒 + 360,000 GiB秒 / 月**(**サブスクリプション単位でアプリ間共有**) |
| PostgreSQL Flexible Server B1ms | `$0.026 / 時` |
| Azure AI Search Basic | `$0.133 / 時` |
| Azure AI Search S1 | `$0.444 / 時` |
| Azure Files Premium LRS | `$0.192 / GiB / 月` |

### 計算の前提

- 1 か月 = **730 時間 = 2,628,000 秒**(Azure の月額表示に合わせた慣例値)
- ACA 無料枠は**サブスクリプション単位でアプリ間共有**されるため、複数アプリを同一サブスクリプションで動かす場合、下記の控除は 1 回しか効きません。本試算では常時稼働する Fuseki に対して控除を適用しています
- ACA の無料枠控除後の秒数に単価を掛けます

---

## minimal — Phase 1 MVP 時点(AI Search 未デプロイ、EmptyDir 構成)

| リソース | 構成 | 月額(idle 課金前提) |
|---|---|---|
| ACA: Fuseki(常時 1 レプリカ) | 0.5 vCPU / 1 GiB(デモ) | $10 |
| " | 1 vCPU / 2 GiB(推奨・JVM 余裕) | $22 |
| ACA: API + MCP | scale-to-zero | $0〜5 |
| PostgreSQL Flexible Server | B1ms + 32 GiB | $23 |
| Blob Storage | 数 GB | $1 |
| ACR Basic | (ghcr.io 利用なら $0) | $5 |
| Log Analytics | 1〜2 GB / 月 | $0〜5 |
| Azure Files | **不要**(EmptyDir 構成) | $0 |
| Static Web Apps / Key Vault | Free / 従量 | $0 |
| **合計** | Fuseki 0.5 vCPU | **月 $39〜49** |
| **合計** | Fuseki 1 vCPU | **月 $51〜61** |
| Microsoft Foundry (LLM) | 帰納実行時のみ従量 | 変動(中規模スキーマ 1 回で $1〜5 程度) |

---

## 計算式(検算可能な形)

### Fuseki 0.5 vCPU / 1 GiB — 常時 1 レプリカ

vCPU:

```
0.5 vCPU × 2,628,000 秒            = 1,314,000 vCPU秒
1,314,000 − 180,000 (無料枠)       = 1,134,000 vCPU秒(課金対象)
1,134,000 × $0.000003 (idle 単価)  = $3.40
```

メモリ:

```
1 GiB × 2,628,000 秒               = 2,628,000 GiB秒
2,628,000 − 360,000 (無料枠)       = 2,268,000 GiB秒(課金対象)
2,268,000 × $0.000003              = $6.80
```

合計: `$3.40 + $6.80 = $10.20` → **表の $10**

### Fuseki 1 vCPU / 2 GiB — 常時 1 レプリカ(推奨)

vCPU:

```
1 vCPU × 2,628,000 秒              = 2,628,000 vCPU秒
2,628,000 − 180,000 (無料枠)       = 2,448,000 vCPU秒(課金対象)
2,448,000 × $0.000003 (idle 単価)  = $7.34
```

メモリ:

```
2 GiB × 2,628,000 秒               = 5,256,000 GiB秒
5,256,000 − 360,000 (無料枠)       = 4,896,000 GiB秒(課金対象)
4,896,000 × $0.000003              = $14.69
```

合計: `$7.34 + $14.69 = $22.03` → **表の $22**

### PostgreSQL Flexible Server B1ms + 32 GiB

コンピュート:

```
$0.026 / 時 × 730 時 = $18.98
```

表の **$23** はこのコンピュートに 32 GiB のストレージ分を加えた値です(差分は約 $4)。ストレージの単価は本試算の根拠単価一覧に含めていないため、ここでは内訳を確定させず、コンピュート分のみ検算可能な形で示します。ストレージ単価を確定させたい場合は Retail Prices API の該当 SKU を再取得してください。

### ACA: API + MCP(scale-to-zero)

リクエストがない間はレプリカが 0 になるため、下限は $0 です。上限 $5 は、無料枠を Fuseki が消費し切った状態で API と MCP が断続的に起動する場合の目安です。実際の値は呼び出し頻度に完全に依存するため、レンジで示しています。

### Azure AI Search(Phase 3 で追加)

```
Basic: $0.133 / 時 × 730 時 = $97.09  → +$97 / 月
S1   : $0.444 / 時 × 730 時 = $324.12 → $324 / 月
```

### Azure Files Premium LRS(`graphPersistence: azureFiles` を選んだ場合)

Premium は最小 100 GiB からのプロビジョニングです。

```
$0.192 / GiB / 月 × 100 GiB = $19.20 → 月 $19
```

**EmptyDir 構成(既定)ではこの $19 が不要になります。** これが射影設計([ADR-0002](adr/0002-triple-store-as-rebuildable-projection.md))による確実な削減分です。

### minimal 合計の内訳

Fuseki 0.5 vCPU の場合:

```
Fuseki $10 + API/MCP $0〜5 + PostgreSQL $23 + Blob $1
  + ACR $5 + Log Analytics $0〜5 + Azure Files $0 + SWA/Key Vault $0
= $39 〜 $49
```

Fuseki 1 vCPU の場合:

```
Fuseki $22 + API/MCP $0〜5 + PostgreSQL $23 + Blob $1
  + ACR $5 + Log Analytics $0〜5 + Azure Files $0 + SWA/Key Vault $0
= $51 〜 $61
```

ACR Basic は、公開イメージを `ghcr.io` に置く運用にすれば $5 を削減できます。

---

## 最大の見積り不確実性: ACA の idle/active 判定

> **ACA の idle 単価は active の 1/8 です。Fuseki が常時 active と判定されると vCPU 分が 8 倍になり、1 vCPU 構成で最悪 月 $105 前後まで上振れします。Phase 1 スパイクで idle/active 比率を実測して確定します。**

上振れ時の計算(1 vCPU / 2 GiB が常時 active):

```
vCPU : 2,448,000 vCPU秒 × $0.000024 (active 単価) = $58.75
メモリ: 4,896,000 GiB秒 × $0.000003              = $14.69
Fuseki 小計                                       = $73.44
```

これに他リソース($29〜34)を加えると **$102〜107** となり、「月 $105 前後」の根拠になります。差額は Fuseki の vCPU 分のみで、`$58.75 − $7.34 = $51.41` です。

**この不確実性を潰すことが Phase 1 の必須スパイクの 1 つです。** 実測値が得られ次第、README とこのドキュメントの数値を実測ベースに置き換えます。

---

## Phase 3 以降(AI Search 追加後)

AI Search Basic `+$97/月` → **合計 月 $136〜158 + LLM 従量**

```
Fuseki 0.5 vCPU: $39〜49 + $97 = $136〜146
Fuseki 1 vCPU  : $51〜61 + $97 = $148〜158
→ 全体レンジ $136〜158
```

## production(参考概算)

AI Search S1(`$324/月`)、PostgreSQL General Purpose、ACA 専用プラン or AKS + Managed Disk、Private Endpoint(約 $7.3/月 × 5 本)等で **月 $700〜1,200 規模**です。

これは概算であり、内訳の確定は **Phase 4** で行います。現時点の数値は「桁を把握する」ためのものとして扱ってください。

## Microsoft Foundry (LLM) の従量課金

オントロジー帰納の実行時のみ発生します。**中規模スキーマ 1 回の帰納で $1〜5 程度**を目安としています。使用するモデル、スキーマの規模、リトライ回数によって変動するため、上記の月額表には含めていません。

---

## コストを止める方法

```bash
azd down --purge
```

`azd down` で全リソースを削除できることを設計原則としています(設計原則 1)。`--purge` は論理削除保護のあるリソース(Key Vault 等)も完全に削除します。評価後は必ず実行してください。

コストを抑えるための設計上の選択:

- **AI Search は Phase 3 まで未デプロイ**にしてコストを回避します($97/月)
- **API / MCP は scale-to-zero** にします
- **Azure Files は既定で不要**です(EmptyDir 構成、$19/月の削減)
- **ACR は省略可能**です(`ghcr.io` 利用、$5/月の削減)
- **Purview には依存しません**(CU 課金の回避。[ADR-0007](adr/0007-no-purview-dependency.md))

## 単価の再取得方法

単価は変動します。以下のように Retail Prices API から再取得できます(例: ACA Consumption / Japan East)。

```bash
curl -s "https://prices.azure.com/api/retail/prices?\$filter=serviceName%20eq%20'Azure%20Container%20Apps'%20and%20armRegionName%20eq%20'japaneast'" | jq '.Items[] | {meterName, unitOfMeasure, retailPrice}'
```

取得した単価が本ドキュメントの根拠単価と乖離している場合は、取得時点を更新したうえで試算を再計算してください。
