// オントロジー候補の生成に使うモデル (ADR-0043、P2A-02)。
//
// **API キーを使わない** (決定9)。`disableLocalAuth: true` でローカル認証その
// ものを無効にし、マネージド ID (`Cognitive Services OpenAI User`) だけで呼ぶ。
// **キーが存在しなければ、キーが漏れることもない。**
//
// **`kind: 'OpenAI'` を選んだ** (決定8 の却下案)。`properties.endpoint` が
// そのまま `{endpoint}/openai/deployments/{d}/chat/completions` の基底になる
// のがこの kind で最も確実である。`AIServices` (Foundry の新しい形) でも同じ
// 経路は使えるが、**1 回のデプロイ窓で確かめきるために確実さを取った**。
//
// **モデルと割り当ては実測して決めた** (決定11、2026-09-12 / Japan East):
//
// | 測ったこと | 結果 |
// |---|---|
// | `gpt-4.1` (2025-04-14) の SKU | GlobalStandard ほかで利用可能 |
// | `OpenAI.GlobalStandard.gpt4.1` の割り当て | 9,000 (使用 0) |
//
// 既定の容量 10 は測った割り当ての約 0.1% である。

@description('Cognitive Services アカウントの名前。')
param name string

@description('リソースを配置するリージョン。**アプリのリージョンと分けられる** (R4。Japan East で使えるモデルは限られる)。')
param location string

@description('全リソースに付与する共通タグ。')
param tags object

@description('推論を呼ぶマネージドID のプリンシパルID。`Cognitive Services OpenAI User` を付与する。')
param identityPrincipalId string

@description('カスタムサブドメイン名。Entra 認証で推論を呼ぶには必須 (キー認証を無効にしているため)。')
param customSubDomainName string

@description('デプロイするモデル名。既定は実測で Japan East に割り当てがあることを確認した gpt-4.1 (ADR-0043 決定11)。')
param modelName string = 'gpt-4.1'

@description('モデルの版。')
param modelVersion string = '2025-04-14'

@description('デプロイの SKU。')
param modelSkuName string = 'GlobalStandard'

@description('デプロイの容量 (千 TPM 単位)。既定 10 は Japan East で実測した割り当て 9,000 の約 0.1%。')
param modelCapacity int = 10

@description('モデルのデプロイ名。アプリはこの名前で呼ぶ (MODEL_DEPLOYMENT)。')
param deploymentName string = 'ontology-proposer'

// `Cognitive Services OpenAI User`。推論の呼び出しに必要な最小のロール。
// **`Contributor` や `OpenAI Contributor` を付けない** — デプロイの作成や
// 削除はアプリの仕事ではない。
var openAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: customSubDomainName
    publicNetworkAccess: 'Enabled'
    // **キー認証を無効にする** (ADR-0043 決定9)。マネージド ID だけで呼ぶ。
    disableLocalAuth: true
  }
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: deploymentName
  sku: {
    name: modelSkuName
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
  }
}

resource openAiUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, identityPrincipalId, openAiUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      openAiUserRoleId
    )
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output name string = account.name
output endpoint string = account.properties.endpoint
output deploymentName string = deployment.name
output modelName string = modelName
