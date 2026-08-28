// MCP Server (Python MCP SDK / Streamable HTTP) を Container Apps 上に構築するモジュール。
// AI エージェント向けの読み取り専用ツール面を external ingress で公開する。
// MCP_READ_ONLY=true を固定し、SPARQL Update / SERVICE 句は封じる。

@description('MCP Server コンテナアプリの名前。')
param name string

@description('リソースを配置するリージョン。')
param location string

@description('全リソースに付与する共通タグ。')
param tags object

@description('azd deploy のターゲット識別に使うサービス名。')
param serviceName string = 'mcp'

@description('Container Apps 環境のリソースID。')
param containerAppsEnvironmentId string

@description('コンテナイメージの取得元 ACR のログインサーバー。')
param containerRegistryLoginServer string

@description('ユーザー割り当てマネージドID のリソースID。')
param identityId string

@description('ユーザー割り当てマネージドID のクライアントID。')
param identityClientId string

@description('デプロイするイメージ。azd deploy 前は空文字でプレースホルダを使う。')
param imageName string = ''

@description('認証モード。entra は Entra ID JWT 検証、disabled はローカル評価用のオプトアウト。')
@allowed([
  'entra'
  'disabled'
])
param authMode string = 'entra'

@description('Entra ID のテナントID。空の場合はデプロイ先サブスクリプションのテナントを使う。')
param entraTenantId string = ''

@description('アクセストークン検証で期待する audience。')
param entraApiAudience string = ''

@description('Fuseki の SPARQL Query エンドポイント URL。')
param sparqlQueryEndpoint string

@description('Fuseki の SPARQL Update エンドポイント URL。MCP からは使わないが設定の対称性のため渡す。')
param sparqlUpdateEndpoint string

@description('Fuseki の Graph Store Protocol エンドポイント URL。')
param sparqlGspEndpoint string

@description('Fuseki の admin API ベース URL。')
param fusekiAdminEndpoint string

@description('Core API のベース URL。名前空間一覧などは API 経由で取得する。')
param coreApiUrl string

@description('Application Insights の接続文字列。')
param applicationInsightsConnectionString string

@description('ログレベル。')
param logLevel string = 'INFO'

@description('SPARQL クエリのタイムアウト秒数。')
param sparqlQueryTimeoutSeconds string = '30'

@description('SPARQL クエリの最大結果件数。')
param sparqlMaxResults string = '10000'

var placeholderImage = 'mcr.microsoft.com/k8se/quickstart:latest'
var resolvedImage = empty(imageName) ? placeholderImage : imageName
var resolvedTenantId = empty(entraTenantId) ? subscription().tenantId : entraTenantId

resource mcp 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': serviceName })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnvironmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: containerRegistryLoginServer
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'mcp'
          image: resolvedImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          // NOTE: ここの環境変数名は packages/mcp-server / packages/core の Python コードが
          // そのまま読む契約。名前を変える場合は両方を同時に変更する。
          env: [
            {
              name: 'AUTH_MODE'
              value: authMode
            }
            {
              name: 'ENTRA_TENANT_ID'
              value: resolvedTenantId
            }
            {
              name: 'ENTRA_API_AUDIENCE'
              value: entraApiAudience
            }
            {
              name: 'SPARQL_QUERY_ENDPOINT'
              value: sparqlQueryEndpoint
            }
            {
              name: 'SPARQL_UPDATE_ENDPOINT'
              value: sparqlUpdateEndpoint
            }
            {
              name: 'SPARQL_GSP_ENDPOINT'
              value: sparqlGspEndpoint
            }
            {
              name: 'FUSEKI_ADMIN_ENDPOINT'
              value: fusekiAdminEndpoint
            }
            {
              // 設計原則: エージェントへは読み取りのみを提供する。
              name: 'MCP_READ_ONLY'
              value: 'true'
            }
            {
              name: 'CORE_API_URL'
              value: coreApiUrl
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: identityClientId
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: applicationInsightsConnectionString
            }
            {
              name: 'LOG_LEVEL'
              value: logLevel
            }
            {
              // SSRF 対策。Azure IMDS 等への到達を防ぐため連邦クエリを禁止する。
              name: 'SPARQL_ALLOW_SERVICE'
              value: 'false'
            }
            {
              name: 'SPARQL_QUERY_TIMEOUT_SECONDS'
              value: sparqlQueryTimeoutSeconds
            }
            {
              name: 'SPARQL_MAX_RESULTS'
              value: sparqlMaxResults
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
        rules: [
          {
            name: 'http-scale'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

output name string = mcp.name
output uri string = 'https://${mcp.properties.configuration.ingress.fqdn}'
output fqdn string = mcp.properties.configuration.ingress.fqdn
