// Container Apps 環境 (Consumption ワークロードプロファイル) を構築するモジュール。
// Log Analytics へログを送出し、graphPersistence == 'azureFiles' のときのみ
// Fuseki 用の Azure Files ストレージ定義 (storages) を環境に追加する。

@description('Container Apps 環境の名前。')
param name string

@description('リソースを配置するリージョン。')
param location string

@description('全リソースに付与する共通タグ。')
param tags object

@description('ログ送出先 Log Analytics ワークスペースのリソースID。')
param logAnalyticsWorkspaceName string

@description('デプロイティア。production では VNet 統合を有効化する (Phase 4 で詳細化)。')
@allowed([
  'minimal'
  'production'
])
param deploymentTier string

@description('グラフの永続化方式。')
@allowed([
  'ephemeral'
  'azureFiles'
])
param graphPersistence string

@description('Azure Files を使う場合のストレージアカウント名。ephemeral では空文字。')
param filesStorageAccountName string = ''

@description('Azure Files を使う場合のファイル共有名。ephemeral では空文字。')
param fusekiFileShareName string = ''

var useAzureFiles = graphPersistence == 'azureFiles' && !empty(filesStorageAccountName)

// TODO(Phase 4): deploymentTier == 'production' では専用 VNet を作成し、
// vnetConfiguration.infrastructureSubnetId / internal: true を設定して
// Private Endpoint 構成へ寄せる。あわせて zoneRedundant: true も有効化する
// (zoneRedundant は VNet 統合が前提)。minimal では VNet を作らない。
var vnetIntegrationRequired = deploymentTier == 'production'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    zoneRedundant: false
  }
}

// SMB マウントにはアカウントキーが必要なため、モジュール内で listKeys() を呼ぶ。
// (キーを module output に出さないための構成)
resource filesStorage 'Microsoft.Storage/storageAccounts@2024-01-01' existing = if (useAzureFiles) {
  name: filesStorageAccountName
}

resource fusekiStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = if (useAzureFiles) {
  parent: containerAppsEnvironment
  name: 'fuseki-databases'
  properties: {
    azureFile: {
      accountName: filesStorageAccountName
      accountKey: useAzureFiles ? filesStorage.listKeys().keys[0].value : ''
      shareName: fusekiFileShareName
      accessMode: 'ReadWrite'
    }
  }
}

output id string = containerAppsEnvironment.id
output name string = containerAppsEnvironment.name
output defaultDomain string = containerAppsEnvironment.properties.defaultDomain
output staticIp string = containerAppsEnvironment.properties.staticIp
output fusekiStorageName string = useAzureFiles ? fusekiStorage.name : ''
output vnetIntegrationRequired bool = vnetIntegrationRequired
