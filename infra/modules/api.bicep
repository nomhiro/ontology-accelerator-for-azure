// Core API (FastAPI) を Container Apps 上に構築するモジュール。
// external ingress で外部公開し、scale-to-zero でアイドル時のコストを抑える。
// Fuseki への書き込み (SPARQL Update / Graph Store Protocol) を行える唯一のサービス。

@description('API コンテナアプリの名前。')
param name string

@description('リソースを配置するリージョン。')
param location string

@description('全リソースに付与する共通タグ。')
param tags object

@description('azd deploy のターゲット識別に使うサービス名。')
param serviceName string = 'api'

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

@description('API のアクセストークン検証で期待する audience (App 登録の Application ID URI)。')
param entraApiAudience string = ''

@description('Fuseki の SPARQL Query エンドポイント URL。')
param sparqlQueryEndpoint string

@description('Fuseki の SPARQL Update エンドポイント URL。')
param sparqlUpdateEndpoint string

@description('Fuseki の Graph Store Protocol エンドポイント URL。')
param sparqlGspEndpoint string

@description('Fuseki の admin API ベース URL。データセット管理に使う。')
param fusekiAdminEndpoint string

@description('Fuseki admin API のユーザー名。')
param fusekiAdminUser string = 'admin'

@description('Fuseki admin パスワードの Key Vault シークレット URI。')
param fusekiPasswordSecretUri string

@description('PostgreSQL 管理者パスワードの Key Vault シークレット URI。authMode == disabled のときのみ注入する。')
param postgresPasswordSecretUri string

@description('PostgreSQL のホスト名。')
param postgresHost string

@description('PostgreSQL のポート番号。')
param postgresPort string = '5432'

@description('PostgreSQL のデータベース名。')
param postgresDatabase string

@description('PostgreSQL の接続ユーザー名。Entra 認証では UAMI 名を使う。')
param postgresUser string

@description('オントロジー正本を格納する Blob エンドポイント URL。')
param storageAccountUrl string

@description('オントロジー正本の Blob コンテナ名。')
param ontologyBlobContainer string

@description('名前付きグラフ IRI の接頭辞。infra/modules/fuseki.bicep の graphIriBase と同じ値を main.bicep から渡すこと(値がずれると射影したグラフを ProjectionService が見つけられなくなる)。')
param graphIriBase string = 'urn:ontology:graph'

@description('正本 TTL を置く Blob のプレフィックス。containers/fuseki/load-snapshot.sh の BLOB_PREFIX と揃える。ADR-0010 決定8で `approved/` から改名した。')
param blobPrefix string = 'versions/'

@description('Application Insights の接続文字列。')
param applicationInsightsConnectionString string

@description('ログレベル。')
param logLevel string = 'INFO'

@description('SPARQL の SERVICE 句 (連邦クエリ) を許可するか。SSRF 対策のため既定は false。')
param sparqlAllowService string = 'false'

@description('SPARQL クエリのタイムアウト秒数。')
param sparqlQueryTimeoutSeconds string = '30'

@description('SPARQL クエリの最大結果件数。')
param sparqlMaxResults string = '10000'

var placeholderImage = 'mcr.microsoft.com/k8se/quickstart:latest'
var resolvedImage = empty(imageName) ? placeholderImage : imageName
var resolvedTenantId = empty(entraTenantId) ? subscription().tenantId : entraTenantId

// authMode == 'entra' では PostgreSQL へ Entra トークンで接続するため、パスワードは注入しない
// (packages/core は POSTGRES_PASSWORD が空のときトークン取得にフォールバックする)。
var usePostgresPassword = authMode == 'disabled'

var containerSecrets = concat(
  [
    {
      name: 'fuseki-admin-password'
      keyVaultUrl: fusekiPasswordSecretUri
      identity: identityId
    }
  ],
  usePostgresPassword
    ? [
        {
          name: 'postgres-admin-password'
          keyVaultUrl: postgresPasswordSecretUri
          identity: identityId
        }
      ]
    : []
)

var postgresPasswordEnv = usePostgresPassword
  ? [
      {
        name: 'POSTGRES_PASSWORD'
        secretRef: 'postgres-admin-password'
      }
    ]
  : []

resource api 'Microsoft.App/containerApps@2024-03-01' = {
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
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: containerSecrets
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
          name: 'api'
          image: resolvedImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          // NOTE: ここの環境変数名は packages/api / packages/core の Python コードが
          // そのまま読む契約。名前を変える場合は両方を同時に変更する。
          env: concat([
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
              // Fuseki の admin API (データセット管理) は Basic 認証で保護されている。
              // 設計原則どおり、この資格情報を持つのは Core API だけ。
              name: 'FUSEKI_ADMIN_USER'
              value: fusekiAdminUser
            }
            {
              name: 'FUSEKI_ADMIN_PASSWORD'
              secretRef: 'fuseki-admin-password'
            }
            {
              name: 'POSTGRES_HOST'
              value: postgresHost
            }
            {
              name: 'POSTGRES_PORT'
              value: postgresPort
            }
            {
              name: 'POSTGRES_DATABASE'
              value: postgresDatabase
            }
            {
              name: 'POSTGRES_USER'
              value: postgresUser
            }
            {
              name: 'AZURE_STORAGE_ACCOUNT_URL'
              value: storageAccountUrl
            }
            {
              name: 'ONTOLOGY_BLOB_CONTAINER'
              value: ontologyBlobContainer
            }
            {
              name: 'GRAPH_IRI_BASE'
              value: graphIriBase
            }
            {
              name: 'BLOB_PREFIX'
              value: blobPrefix
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
              name: 'SPARQL_ALLOW_SERVICE'
              value: sparqlAllowService
            }
            {
              name: 'SPARQL_QUERY_TIMEOUT_SECONDS'
              value: sparqlQueryTimeoutSeconds
            }
            {
              name: 'SPARQL_MAX_RESULTS'
              value: sparqlMaxResults
            }
          ], postgresPasswordEnv)
          // /healthz は認証不要でプロセスの生存だけを返す (依存先の到達性は含めない)。
          // 依存先の一時的な不調でレプリカが落ちないようにする意図。
          probes: [
            {
              // 起動時に packages/api/src/ontology_api/migrate.py が
              // `alembic upgrade head` を実行し、完了するまで uvicorn (= /healthz) が
              // 応答しない。Startup probe が成功するまで Readiness / Liveness は
              // 評価されないため、この間はマイグレーションの所要時間を probe に
              // kill されずに確保できる。予算は fuseki.bicep の Startup probe
              // (initialDelaySeconds(10) + periodSeconds(10) * failureThreshold(30)
              // = 310秒、最大5分)と同等以上を取る。この予算を切ると、
              // マイグレーション中の transactional DDL が kill でロールバックし、
              // 再起動して同じ場所で再び kill される = そのマイグレーションが
              // 永久に適用できない状態に陥る(migrate.py の lock_timeout(240秒)は
              // この予算内に収まるよう設定してある)。
              type: 'Startup'
              httpGet: {
                path: '/healthz'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 10
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 30
              successThreshold: 1
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/healthz'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 3
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 3
              successThreshold: 1
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8000
                scheme: 'HTTP'
              }
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
              successThreshold: 1
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

output name string = api.name
output uri string = 'https://${api.properties.configuration.ingress.fqdn}'
output fqdn string = api.properties.configuration.ingress.fqdn
