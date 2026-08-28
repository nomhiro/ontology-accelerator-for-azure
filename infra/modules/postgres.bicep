// PostgreSQL Flexible Server を構築するモジュール。
// 正本 (名前空間・RBAC・承認履歴・R2RML マッピング) の格納先。
// Microsoft Entra 認証を有効化し、UAMI を Entra 管理者として登録してパスワードレス接続を可能にする。

@description('PostgreSQL Flexible Server の名前。')
param name string

@description('リソースを配置するリージョン。')
param location string

@description('全リソースに付与する共通タグ。')
param tags object

@description('デプロイティア。minimal は Burstable B1ms、production は GeneralPurpose + HA。')
@allowed([
  'minimal'
  'production'
])
param deploymentTier string

@description('作成するデータベース名。')
param databaseName string = 'ontology'

@description('管理者ログイン名。authMode == disabled のフォールバック経路で使う。')
param administratorLogin string

@description('管理者パスワード。Key Vault に保管された値を azd 経由で受け取る。')
@secure()
param administratorLoginPassword string

@description('Entra 管理者として登録する UAMI のプリンシパルID (オブジェクトID)。')
param identityPrincipalId string

@description('Entra 管理者として登録する UAMI の名前。PostgreSQL 側のロール名になる。')
param identityName string

@description('認証モード。entra のときパスワード認証も残すが、接続は Entra を既定にする。')
@allowed([
  'entra'
  'disabled'
])
param authMode string = 'entra'

var isProduction = deploymentTier == 'production'

var skuConfig = isProduction
  ? {
      name: 'Standard_D2ds_v5'
      tier: 'GeneralPurpose'
    }
  : {
      name: 'Standard_B1ms'
      tier: 'Burstable'
    }

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: name
  location: location
  tags: tags
  sku: skuConfig
  properties: {
    version: '16'
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    authConfig: {
      // Entra 認証を有効化してサービス間接続をパスワードレスにする。
      activeDirectoryAuth: 'Enabled'
      // authMode == disabled (ローカル評価用オプトアウト) のフォールバックとして
      // パスワード認証も残す。値は Key Vault 管理。
      passwordAuth: 'Enabled'
      tenantId: subscription().tenantId
    }
    storage: {
      storageSizeGB: isProduction ? 128 : 32
      autoGrow: 'Enabled'
      tier: isProduction ? 'P10' : 'P4'
    }
    backup: {
      backupRetentionDays: isProduction ? 35 : 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: isProduction ? 'ZoneRedundant' : 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
    createMode: 'Default'
  }
}

// UAMI を Entra 管理者に登録する。リソース名はオブジェクトIDでなければならない。
resource entraAdministrator 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2024-08-01' = {
  parent: postgres
  name: identityPrincipalId
  properties: {
    principalType: 'ServicePrincipal'
    principalName: identityName
    tenantId: subscription().tenantId
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
  // 子リソースの同時操作はサーバー側で競合するため直列化する。
  dependsOn: [
    entraAdministrator
  ]
}

// minimal: Azure サービス (Container Apps の送信IPは固定できない) からの接続を許可する。
// 0.0.0.0-0.0.0.0 は「Azure 内のリソースからのアクセスを許可」を意味する特殊レンジ。
//
// TODO(Phase 4): production では publicNetworkAccess を Disabled にし、
// delegatedSubnetResourceId + privateDnsZoneArmResourceId による VNet 統合、
// あるいは Private Endpoint に置き換える。このファイアウォール規則も削除する。
resource allowAzureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = if (!isProduction) {
  parent: postgres
  name: 'AllowAllAzureServicesAndResources'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
  dependsOn: [
    database
  ]
}

output name string = postgres.name
output host string = postgres.properties.fullyQualifiedDomainName
output databaseName string = databaseName
// Entra 認証では UAMI 名がそのまま接続ユーザー名になる。
output connectionUser string = authMode == 'entra' ? identityName : administratorLogin
