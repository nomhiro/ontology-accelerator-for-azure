// 共有基盤リソースを構築するモジュール。
// 可観測性 (Log Analytics / Application Insights)、シークレット保管 (Key Vault)、
// 正本ストレージ (Blob、バージョニング有効)、コンテナレジストリ、および全サービスが共有する
// ユーザー割り当てマネージドID とそのロール割当を定義する。

@description('全リソースを配置するリージョン。')
param location string

@description('全リソースに付与する共通タグ (azd-env-name を含む)。')
param tags object

@description('リソース名の一意化に用いるトークン。')
param resourceToken string

@description('azd 標準のリソース種別ごとの省略形マップ。')
param abbrs object

@description('グラフの永続化方式。azureFiles のときのみ Azure Files 用ストレージアカウントを作成する。')
@allowed([
  'ephemeral'
  'azureFiles'
])
param graphPersistence string

@description('オントロジーの正本 (バージョン付きTTL) を格納する Blob コンテナ名。')
param ontologyBlobContainer string = 'ontologies'

@description('デプロイを実行する開発者のプリンシパルID。空文字の場合は開発者向けロール割当を行わない。')
param principalId string = ''

@description('principalId の種別。CI からサービスプリンシパルでデプロイする場合は ServicePrincipal を指定する。')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param principalType string = 'User'

@description('PostgreSQL 管理者パスワード。Key Vault に保管し、Bicep からは平文で参照しない。')
@secure()
param postgresAdminPassword string

@description('Fuseki admin ユーザーのパスワード。Key Vault に保管し、Container Apps へは secretRef で注入する。')
@secure()
param fusekiAdminPassword string

@description('Azure Files を利用する場合のファイル共有サイズ (GiB)。Premium は最小 100 GiB。')
param fileShareQuotaGb int = 100

// ---------------------------------------------------------------------------
// 組み込みロール定義ID (サブスクリプションスコープ)
// ---------------------------------------------------------------------------
var roleIds = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  acrPush: '8311e382-0749-4cb8-b61a-304f252e45ec'
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  keyVaultSecretsOfficer: 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
}

var useAzureFiles = graphPersistence == 'azureFiles'

// ---------------------------------------------------------------------------
// 可観測性
// ---------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    // コスト試算 (docs/cost-estimate.md) の「1〜2 GB/月」を前提に最短保持とする。
    retentionInDays: 30
    features: {
      searchVersion: 1
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${abbrs.insightsComponents}${resourceToken}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// Key Vault — RBAC 認可モード。Container Apps は keyVaultUrl + UAMI で参照する
// ---------------------------------------------------------------------------
resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: '${abbrs.keyVaultVaults}${resourceToken}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    // azd down --purge での完全削除を早くするため最短保持 (7日) にする。
    softDeleteRetentionInDays: 7
    enablePurgeProtection: null
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// azd の secretOrRandomPassword は「Key Vault にあれば取得、なければ乱数生成」で動くため、
// 生成された値をここで書き戻しておかないと provision ごとにパスワードが変わってしまう。
// RBAC 認可モードの Key Vault へのシークレット書き込みはデータプレーン操作であり、
// サブスクリプションの Owner/Contributor だけでは権限が足りない。下で作る
// devKvAssignment (Key Vault Secrets Officer) の後に実行させる必要がある。
// principalId が空 (CI 等) の場合は devKvAssignment 自体が作られず dependsOn は無視される。
// その構成では Secrets Officer をあらかじめ別途付与しておく必要がある。
resource postgresPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'postgres-admin-password'
  properties: {
    value: postgresAdminPassword
  }
  dependsOn: [
    devKvAssignment
  ]
}

resource fusekiPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'fuseki-admin-password'
  properties: {
    value: fusekiAdminPassword
  }
  dependsOn: [
    devKvAssignment
  ]
}

// ---------------------------------------------------------------------------
// Blob Storage — オントロジーの正本 (不変リビジョン) を置く。バージョニング必須
// ---------------------------------------------------------------------------
resource storage 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: '${abbrs.storageStorageAccounts}${resourceToken}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    // 正本アクセスは全て Entra (Managed Identity) 経由。共有キーは無効化する。
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: storage
  name: 'default'
  properties: {
    // 設計原則: オントロジーは不変リビジョンとして保存する。バージョニングはその土台。
    isVersioningEnabled: true
    changeFeed: {
      enabled: false
    }
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource ontologyContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobServices
  name: ontologyBlobContainer
  properties: {
    publicAccess: 'None'
  }
}

// ---------------------------------------------------------------------------
// Azure Files (graphPersistence == 'azureFiles' のときのみ)
// TDB2 をネットワークFS上に置く「ストア自体を正本にしたい」利用者向けのオプトイン。
// 既定の ephemeral 構成では作成されない (Premium 最小 100 GiB = 約 $19/月 の節約)。
//
// TODO(Phase 4): NFS 共有 (storageType: 'NfsAzureFile') はファイルロックの観点で
// TDB2 に適するが、Container Apps 環境に カスタム VNet が必須になる。VNet を作らない
// minimal ティアと両立しないため、ここでは SMB (Premium) を採用している。
// production ティアで VNet を導入する際に NFS へ切り替える。
// ---------------------------------------------------------------------------
resource filesStorage 'Microsoft.Storage/storageAccounts@2024-01-01' = if (useAzureFiles) {
  name: '${abbrs.storageStorageAccounts}fs${resourceToken}'
  location: location
  tags: tags
  kind: 'FileStorage'
  sku: {
    name: 'Premium_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    // Container Apps の SMB マウントはアカウントキーを使うため共有キーを無効化できない。
    allowSharedKeyAccess: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource filesService 'Microsoft.Storage/storageAccounts/fileServices@2024-01-01' = if (useAzureFiles) {
  parent: filesStorage
  name: 'default'
}

resource fusekiShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2024-01-01' = if (useAzureFiles) {
  parent: filesService
  name: 'fuseki-databases'
  properties: {
    shareQuota: fileShareQuotaGb
    enabledProtocols: 'SMB'
    accessTier: 'Premium'
  }
}

// ---------------------------------------------------------------------------
// Azure Container Registry
// ---------------------------------------------------------------------------
resource containerRegistry 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: '${abbrs.containerRegistryRegistries}${resourceToken}'
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// ユーザー割り当てマネージドID — Fuseki / API / MCP が共有する
// ---------------------------------------------------------------------------
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: '${abbrs.managedIdentityUserAssignedIdentities}${resourceToken}'
  location: location
  tags: tags
}

// ---------------------------------------------------------------------------
// ロール割当 (サービス用 UAMI)
// ---------------------------------------------------------------------------
resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: containerRegistry
  name: guid(containerRegistry.id, identity.id, roleIds.acrPull)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.acrPull)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Fuseki だけを見れば 'Storage Blob Data Reader' で足りるが、Core API が同じIDで
// 正本 (バージョン付きTTL) を書き込むため Contributor を割り当てる。
// Fuseki と API の ID を分離するのは Phase 4 のハードニング項目。
resource blobContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, identity.id, roleIds.storageBlobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      roleIds.storageBlobDataContributor
    )
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Container Apps の secrets[].keyVaultUrl による参照に必須。
resource kvSecretsUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, identity.id, roleIds.keyVaultSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultSecretsUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// ロール割当 (デプロイを実行する開発者)
// ---------------------------------------------------------------------------
resource devBlobAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  scope: storage
  name: guid(storage.id, principalId, roleIds.storageBlobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      roleIds.storageBlobDataContributor
    )
    principalId: principalId
    principalType: principalType
  }
}

resource devKvAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  scope: keyVault
  name: guid(keyVault.id, principalId, roleIds.keyVaultSecretsOfficer)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultSecretsOfficer)
    principalId: principalId
    principalType: principalType
  }
}

resource devAcrAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  scope: containerRegistry
  name: guid(containerRegistry.id, principalId, roleIds.acrPush)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.acrPush)
    principalId: principalId
    principalType: principalType
  }
}

// ---------------------------------------------------------------------------
// outputs
// ---------------------------------------------------------------------------
output logAnalyticsWorkspaceId string = logAnalytics.id
output logAnalyticsWorkspaceName string = logAnalytics.name
output logAnalyticsCustomerId string = logAnalytics.properties.customerId
output applicationInsightsConnectionString string = appInsights.properties.ConnectionString
output applicationInsightsName string = appInsights.name

output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
// 末尾のバージョン無し URI を返すため secretUriWithVersion ではなく組み立てる (最新版を追従させる)。
// 返すのはシークレット値ではなく参照 URI なので、値の漏洩を疑うリンタ規則を明示的に無効化する。
#disable-next-line outputs-should-not-contain-secrets
output fusekiPasswordSecretUri string = '${keyVault.properties.vaultUri}secrets/${fusekiPasswordSecret.name}'
#disable-next-line outputs-should-not-contain-secrets
output postgresPasswordSecretUri string = '${keyVault.properties.vaultUri}secrets/${postgresPasswordSecret.name}'

output storageAccountName string = storage.name
output storageAccountUrl string = storage.properties.primaryEndpoints.blob
output ontologyBlobContainerName string = ontologyContainer.name

output filesStorageAccountName string = useAzureFiles ? filesStorage.name : ''
output fusekiFileShareName string = useAzureFiles ? fusekiShare.name : ''

output containerRegistryName string = containerRegistry.name
output containerRegistryLoginServer string = containerRegistry.properties.loginServer

output identityId string = identity.id
output identityName string = identity.name
output identityClientId string = identity.properties.clientId
output identityPrincipalId string = identity.properties.principalId
