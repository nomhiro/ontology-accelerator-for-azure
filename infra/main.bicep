// azd テンプレートのエントリポイント。サブスクリプションスコープでリソースグループを作成し、
// 共有基盤 / Container Apps 環境 / Fuseki / Core API / MCP Server / PostgreSQL / Web を配線する。
// Phase 3 以降のリソース (Azure AI Search、Ontop、reasoner) はコスト回避のため意図的に作らない。

targetScope = 'subscription'

// ---------------------------------------------------------------------------
// azd 必須パラメータ
// ---------------------------------------------------------------------------
@minLength(1)
@maxLength(64)
@description('azd 環境名。リソースグループ名とタグ (azd-env-name) の由来になる。')
param environmentName string

@minLength(1)
@description('アプリケーション系リソースを配置するリージョン。')
param location string

@description('デプロイを実行する開発者のプリンシパルID。開発者向けロール割当に使う。空でも可。')
param principalId string = ''

@description('principalId の種別。CI から サービスプリンシパルでデプロイする場合は ServicePrincipal を指定する。')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param principalType string = 'User'

// ---------------------------------------------------------------------------
// デプロイ構成
// ---------------------------------------------------------------------------
@description('デプロイティア。minimal は評価用 (azd up で完結)、production は VNet 統合前提の構成。')
@allowed([
  'minimal'
  'production'
])
param deploymentTier string = 'minimal'

@description('グラフの永続化方式。ephemeral は EmptyDir + 起動時再構築 (既定)、azureFiles は Azure Files に常駐。')
@allowed([
  'ephemeral'
  'azureFiles'
])
param graphPersistence string = 'ephemeral'

@description('Microsoft Foundry のモデル用リージョン。モデル可用性がアプリ用リージョンと異なる場合に分離する。空なら location を使う。')
param modelLocation string = ''

@description('Static Web Apps のリージョン。対応リージョンが限られるため location とは別に指定する。')
@allowed([
  'centralus'
  'eastus2'
  'eastasia'
  'westeurope'
  'westus2'
])
param webLocation string = 'eastasia'

@description('認証モード。entra は Entra ID JWT 検証を強制、disabled はローカル評価用のオプトアウト。')
@allowed([
  'entra'
  'disabled'
])
param authMode string = 'entra'

@description('Entra ID のテナントID。空ならデプロイ先サブスクリプションのテナントを使う。')
param entraTenantId string = ''

@description('API / MCP のアクセストークン検証で期待する audience (App 登録の Application ID URI)。')
param entraApiAudience string = ''

@description('アプリケーションのログレベル。')
param logLevel string = 'INFO'

// ---------------------------------------------------------------------------
// PostgreSQL
// ---------------------------------------------------------------------------
@description('PostgreSQL の管理者ログイン名。')
param postgresAdminLogin string = 'ontologyadmin'

@description('PostgreSQL の管理者パスワード。azd が Key Vault から取得または生成する。')
@secure()
param postgresAdminPassword string

@description('PostgreSQL のデータベース名。')
param postgresDatabaseName string = 'ontology'

// ---------------------------------------------------------------------------
// Fuseki
// ---------------------------------------------------------------------------
@description('Fuseki admin ユーザーのパスワード。azd が Key Vault から取得または生成する。')
@secure()
param fusekiAdminPassword string

@description('Fuseki コンテナの vCPU 数 (文字列)。デモは 0.5、JVM に余裕を持たせるなら 1。')
param fusekiCpu string = '0.5'

@description('Fuseki コンテナのメモリ。fusekiCpu を上げた場合はここも合わせる。')
param fusekiMemory string = '1Gi'

@description('Fuseki の JVM オプション。メモリ割当に合わせて調整する。')
param fusekiJavaOptions string = '-Xmx768m -XX:+UseSerialGC'

// ---------------------------------------------------------------------------
// azd deploy が設定するコンテナイメージ (初回 provision 時は空)
// ---------------------------------------------------------------------------
@description('Core API のコンテナイメージ。azd deploy が SERVICE_API_IMAGE_NAME として設定する。')
param apiImageName string = ''

@description('MCP Server のコンテナイメージ。azd deploy が SERVICE_MCP_IMAGE_NAME として設定する。')
param mcpImageName string = ''

@description('Fuseki のコンテナイメージ。azd deploy が SERVICE_FUSEKI_IMAGE_NAME として設定する。')
param fusekiImageName string = ''

// ---------------------------------------------------------------------------
// 命名とタグ
// ---------------------------------------------------------------------------
var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
}
var ontologyBlobContainer = 'ontologies'
// ADR-0006 のバージョン付き名前付きグラフの接頭辞と、正本 TTL を置く Blob の
// プレフィックス。fuseki.bicep (ローダ) と api.bicep (ProjectionService) の
// 両方が同じ値を見る必要があるため、main.bicep を単一の正本にして両モジュールへ渡す。
var graphIriBase = 'urn:ontology:graph'
var ontologyBlobPrefix = 'approved/'

// modelLocation は Phase 2 で Microsoft Foundry を追加する際に使う。
// いま参照先がないため、値の解決だけ済ませて output で環境へ渡す。
var resolvedModelLocation = empty(modelLocation) ? location : modelLocation

resource resourceGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: '${abbrs.resourcesResourceGroups}${environmentName}'
  location: location
  tags: tags
}

// ---------------------------------------------------------------------------
// 共有基盤
// ---------------------------------------------------------------------------
module shared './modules/shared.bicep' = {
  name: 'shared'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    abbrs: abbrs
    graphPersistence: graphPersistence
    ontologyBlobContainer: ontologyBlobContainer
    principalId: principalId
    principalType: principalType
    postgresAdminPassword: postgresAdminPassword
    fusekiAdminPassword: fusekiAdminPassword
  }
}

// ---------------------------------------------------------------------------
// PostgreSQL (正本)
// ---------------------------------------------------------------------------
module postgres './modules/postgres.bicep' = {
  name: 'postgres'
  scope: resourceGroup
  params: {
    name: '${abbrs.dBforPostgreSQLServers}${resourceToken}'
    location: location
    tags: tags
    deploymentTier: deploymentTier
    databaseName: postgresDatabaseName
    administratorLogin: postgresAdminLogin
    administratorLoginPassword: postgresAdminPassword
    identityPrincipalId: shared.outputs.identityPrincipalId
    identityName: shared.outputs.identityName
    authMode: authMode
  }
}

// ---------------------------------------------------------------------------
// Container Apps 環境
// ---------------------------------------------------------------------------
module containerAppsEnvironment './modules/container-apps-env.bicep' = {
  name: 'container-apps-env'
  scope: resourceGroup
  params: {
    name: '${abbrs.appManagedEnvironments}${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceName: shared.outputs.logAnalyticsWorkspaceName
    deploymentTier: deploymentTier
    graphPersistence: graphPersistence
    filesStorageAccountName: shared.outputs.filesStorageAccountName
    fusekiFileShareName: shared.outputs.fusekiFileShareName
  }
}

// ---------------------------------------------------------------------------
// Fuseki (internal のみ / 再構築可能な射影)
// ---------------------------------------------------------------------------
module fuseki './modules/fuseki.bicep' = {
  name: 'fuseki'
  scope: resourceGroup
  params: {
    name: '${abbrs.appContainerApps}fuseki-${resourceToken}'
    location: location
    tags: tags
    containerAppsEnvironmentId: containerAppsEnvironment.outputs.id
    containerRegistryLoginServer: shared.outputs.containerRegistryLoginServer
    identityId: shared.outputs.identityId
    identityClientId: shared.outputs.identityClientId
    imageName: fusekiImageName
    cpu: fusekiCpu
    memory: fusekiMemory
    javaOptions: fusekiJavaOptions
    graphPersistence: graphPersistence
    fusekiStorageName: containerAppsEnvironment.outputs.fusekiStorageName
    storageAccountUrl: shared.outputs.storageAccountUrl
    ontologyBlobContainer: shared.outputs.ontologyBlobContainerName
    fusekiPasswordSecretUri: shared.outputs.fusekiPasswordSecretUri
    applicationInsightsConnectionString: shared.outputs.applicationInsightsConnectionString
    logLevel: logLevel
    graphIriBase: graphIriBase
    blobPrefix: ontologyBlobPrefix
  }
}

// ---------------------------------------------------------------------------
// Core API (external / 書き込み口はここだけ)
// ---------------------------------------------------------------------------
module api './modules/api.bicep' = {
  name: 'api'
  scope: resourceGroup
  params: {
    name: '${abbrs.appContainerApps}api-${resourceToken}'
    location: location
    tags: tags
    containerAppsEnvironmentId: containerAppsEnvironment.outputs.id
    containerRegistryLoginServer: shared.outputs.containerRegistryLoginServer
    identityId: shared.outputs.identityId
    identityClientId: shared.outputs.identityClientId
    imageName: apiImageName
    authMode: authMode
    entraTenantId: entraTenantId
    entraApiAudience: entraApiAudience
    sparqlQueryEndpoint: fuseki.outputs.queryEndpoint
    sparqlUpdateEndpoint: fuseki.outputs.updateEndpoint
    sparqlGspEndpoint: fuseki.outputs.gspEndpoint
    fusekiAdminEndpoint: fuseki.outputs.adminEndpoint
    fusekiPasswordSecretUri: shared.outputs.fusekiPasswordSecretUri
    postgresPasswordSecretUri: shared.outputs.postgresPasswordSecretUri
    postgresHost: postgres.outputs.host
    postgresDatabase: postgres.outputs.databaseName
    postgresUser: postgres.outputs.connectionUser
    storageAccountUrl: shared.outputs.storageAccountUrl
    ontologyBlobContainer: shared.outputs.ontologyBlobContainerName
    applicationInsightsConnectionString: shared.outputs.applicationInsightsConnectionString
    logLevel: logLevel
    graphIriBase: graphIriBase
    blobPrefix: ontologyBlobPrefix
  }
}

// ---------------------------------------------------------------------------
// MCP Server (external / 読み取り専用)
// ---------------------------------------------------------------------------
module mcp './modules/mcp-server.bicep' = {
  name: 'mcp'
  scope: resourceGroup
  params: {
    name: '${abbrs.appContainerApps}mcp-${resourceToken}'
    location: location
    tags: tags
    containerAppsEnvironmentId: containerAppsEnvironment.outputs.id
    containerAppsEnvironmentDefaultDomain: containerAppsEnvironment.outputs.defaultDomain
    containerRegistryLoginServer: shared.outputs.containerRegistryLoginServer
    identityId: shared.outputs.identityId
    identityClientId: shared.outputs.identityClientId
    imageName: mcpImageName
    authMode: authMode
    entraTenantId: entraTenantId
    entraApiAudience: entraApiAudience
    sparqlQueryEndpoint: fuseki.outputs.queryEndpoint
    sparqlUpdateEndpoint: fuseki.outputs.updateEndpoint
    sparqlGspEndpoint: fuseki.outputs.gspEndpoint
    fusekiAdminEndpoint: fuseki.outputs.adminEndpoint
    coreApiUrl: api.outputs.uri
    applicationInsightsConnectionString: shared.outputs.applicationInsightsConnectionString
    logLevel: logLevel
  }
}

// ---------------------------------------------------------------------------
// Web (Static Web Apps)
// ---------------------------------------------------------------------------
module web './modules/web.bicep' = {
  name: 'web'
  scope: resourceGroup
  params: {
    name: '${abbrs.webStaticSites}${resourceToken}'
    location: webLocation
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// outputs — azd が .azure/<env>/.env に環境変数として書き出す
// ---------------------------------------------------------------------------
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = subscription().tenantId
output AZURE_RESOURCE_GROUP string = resourceGroup.name
output AZURE_MODEL_LOCATION string = resolvedModelLocation

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = shared.outputs.containerRegistryLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = shared.outputs.containerRegistryName
output AZURE_CONTAINER_APPS_ENVIRONMENT_NAME string = containerAppsEnvironment.outputs.name
output AZURE_CONTAINER_APPS_ENVIRONMENT_ID string = containerAppsEnvironment.outputs.id

// secretOrRandomPassword がシークレットを引き当てるために必須の output。
output AZURE_KEY_VAULT_NAME string = shared.outputs.keyVaultName
output AZURE_KEY_VAULT_ENDPOINT string = shared.outputs.keyVaultUri

output AZURE_STORAGE_ACCOUNT_NAME string = shared.outputs.storageAccountName
output AZURE_STORAGE_ACCOUNT_URL string = shared.outputs.storageAccountUrl
output ONTOLOGY_BLOB_CONTAINER string = shared.outputs.ontologyBlobContainerName

output AZURE_CLIENT_ID string = shared.outputs.identityClientId
output AZURE_MANAGED_IDENTITY_NAME string = shared.outputs.identityName
output APPLICATIONINSIGHTS_CONNECTION_STRING string = shared.outputs.applicationInsightsConnectionString
output AZURE_LOG_ANALYTICS_WORKSPACE_NAME string = shared.outputs.logAnalyticsWorkspaceName

output SERVICE_API_URI string = api.outputs.uri
output SERVICE_API_NAME string = api.outputs.name
output SERVICE_MCP_URI string = mcp.outputs.uri
output SERVICE_MCP_NAME string = mcp.outputs.name
output SERVICE_WEB_URI string = web.outputs.uri
output SERVICE_WEB_NAME string = web.outputs.name
output SERVICE_FUSEKI_NAME string = fuseki.outputs.name
output SERVICE_FUSEKI_INTERNAL_FQDN string = fuseki.outputs.internalFqdn

output SPARQL_QUERY_ENDPOINT string = fuseki.outputs.queryEndpoint
output SPARQL_UPDATE_ENDPOINT string = fuseki.outputs.updateEndpoint
output SPARQL_GSP_ENDPOINT string = fuseki.outputs.gspEndpoint
output FUSEKI_ADMIN_ENDPOINT string = fuseki.outputs.adminEndpoint

output POSTGRES_HOST string = postgres.outputs.host
output POSTGRES_PORT string = '5432'
output POSTGRES_DATABASE string = postgres.outputs.databaseName
output POSTGRES_USER string = postgres.outputs.connectionUser

output AUTH_MODE string = authMode
output DEPLOYMENT_TIER string = deploymentTier
output GRAPH_PERSISTENCE string = graphPersistence
