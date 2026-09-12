// ソース DB のスキャンを定期実行する Container Apps Job (ADR-0042、P2A-19)。
//
// **既定は Manual トリガである。** `scanJobCron` が空なら自動では走らない
// (ADR-0042 決定1)。定期実行は他人の DB に対して繰り返し接続を試み、実行ごとに
// 課金され、運用者が気づかないうちに始まるものなので、**設定したときだけ動く**のが
// 安全側である (SCAN_ALLOWED_HOSTS を既定で空にしたのと同じ理由)。
//
// **イメージは Core API と同じものを使う** (決定5)。ジョブが呼ぶのは
// ontology_api.services.scan そのもので、依存も同じである。2 つ目のイメージは
// ビルド時間を倍にし、スキャンの本体が 2 つの版で動く余地を作る。
//
// **provision の時点では apiImageName が空なので、scripts/postdeploy.sh が
// `az containerapp job update --image` で差し替えて副作用で確認する** (決定5)。
// azd 1.28.0 が host: containerapp-job を扱えるかは確認できなかった
// (azd show は不正な host 値も黙って受け付けるため、受け付けたことは対応の証拠に
// ならない。実測)。**未確認の挙動に依存しない。**
//
// **Fuseki と Blob の資格情報を渡さない** (決定6)。スキャンが触るのは
// PostgreSQL (カタログ) とソース DB だけである。イメージは API と同じなので、
// **渡せば使えてしまう** — 渡さないことが security property である。

@description('ジョブの名前。')
param name string

@description('リソースを配置するリージョン。')
param location string

@description('全リソースに付与する共通タグ。')
param tags object

@description('Container Apps 環境のリソースID。')
param containerAppsEnvironmentId string

@description('コンテナイメージの取得元 ACR のログインサーバー。')
param containerRegistryLoginServer string

@description('ユーザー割り当てマネージドID のリソースID。')
param identityId string

@description('ユーザー割り当てマネージドID のクライアントID。scan_runs.started_by にも入る。')
param identityClientId string

@description('デプロイするイメージ。Core API と同じものを使う。azd deploy 前は空文字でプレースホルダになり、postdeploy が差し替える。')
param imageName string = ''

@description('定期実行の cron 式 (UTC、5 フィールド)。**空なら Manual トリガになり、自動では走らない** (ADR-0042 決定1)。例: 毎日 18:00 UTC = 03:00 JST なら `0 18 * * *`。')
param scanJobCron string = ''

@description('PostgreSQL のホスト名。')
param postgresHost string

@description('PostgreSQL のポート番号。')
param postgresPort string = '5432'

@description('PostgreSQL のデータベース名。')
param postgresDatabase string

@description('PostgreSQL の接続ユーザー名。Entra 認証では UAMI 名を使う。')
param postgresUser string

@description('PostgreSQL 管理者パスワードの Key Vault シークレット URI。authMode == disabled のときのみ注入する。')
param postgresPasswordSecretUri string

@description('認証モード。ジョブは HTTP を受け付けないが、PostgreSQL への接続方式の選択に使う。')
@allowed([
  'entra'
  'disabled'
])
param authMode string = 'entra'

@description('スキャンで接続を許可するホスト (カンマ区切り)。**空ならスキャンは使えない** (ADR-0041 決定5、不変条件11)。')
param scanAllowedHosts string = ''

@description('ソース DB のパスワードを置く Key Vault の URL。値はここにもカタログにも入らない (ADR-0041 決定4)。')
param scanVaultUrl string = ''

@description('Application Insights の接続文字列。')
param applicationInsightsConnectionString string

@description('ログレベル。')
param logLevel string = 'INFO'

@description('ジョブに割り当てる vCPU。スキャンは 5 本のクエリを 1 接続で流すだけで CPU を使わない。')
param cpu string = '0.5'

@description('ジョブに割り当てるメモリ。')
param memory string = '1Gi'

@description('1 回の実行の上限秒数 (ADR-0042 決定8)。無制限にすると、終わらない掃引に課金が続く。')
param replicaTimeoutSeconds int = 1800

var placeholderImage = 'mcr.microsoft.com/k8se/quickstart:latest'
var resolvedImage = empty(imageName) ? placeholderImage : imageName

// authMode == 'entra' では PostgreSQL へ Entra トークンで接続するためパスワードを注入しない
// (api.bicep と同じ判断。packages/core は POSTGRES_PASSWORD が空のときトークン取得に
// フォールバックする)。
var usePostgresPassword = authMode == 'disabled'

var jobSecrets = usePostgresPassword
  ? [
      {
        name: 'postgres-admin-password'
        keyVaultUrl: postgresPasswordSecretUri
        identity: identityId
      }
    ]
  : []

var postgresPasswordEnv = usePostgresPassword
  ? [
      {
        name: 'POSTGRES_PASSWORD'
        secretRef: 'postgres-admin-password'
      }
    ]
  : []

// **トリガの型は cron の有無で決まる** (ADR-0042 決定1)。
var useSchedule = !empty(scanJobCron)

var triggerConfig = useSchedule
  ? {
      scheduleTriggerConfig: {
        cronExpression: scanJobCron
        parallelism: 1
        replicaCompletionCount: 1
      }
    }
  : {
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
    }

resource scanJob 'Microsoft.App/jobs@2024-03-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnvironmentId
    workloadProfileName: 'Consumption'
    configuration: union(
      {
        triggerType: useSchedule ? 'Schedule' : 'Manual'
        replicaTimeout: replicaTimeoutSeconds
        // **再試行は 0 回** (ADR-0042 決定3)。届かなかったことは scan_runs に
        // 残るので、再試行は健全なソースを再度叩くだけである。
        replicaRetryLimit: 0
        secrets: jobSecrets
        registries: [
          {
            server: containerRegistryLoginServer
            identity: identityId
          }
        ]
      },
      triggerConfig
    )
    template: {
      containers: [
        {
          name: 'scan-job'
          image: resolvedImage
          // **Core API と同じイメージを別のコマンドで起動する** (ADR-0042 決定5)。
          command: [
            'python'
            '-m'
            'ontology_api.scan_job'
          ]
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          // NOTE: 環境変数名は packages/api / packages/core の Python コードが
          // そのまま読む契約。api.bicep と揃えること。
          //
          // **ここに無いものは意図して渡していない** (ADR-0042 決定6)。
          // FUSEKI_ADMIN_PASSWORD と Blob のエンドポイントは、スキャンが
          // 触らないので渡さない。
          env: concat(
            [
              {
                name: 'AUTH_MODE'
                value: authMode
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
                // **接続先の allowlist** (ADR-0041 決定5)。空ならジョブは
                // 何も掃引できない。
                name: 'SCAN_ALLOWED_HOSTS'
                value: scanAllowedHosts
              }
              {
                // 秘密の**在り処**。値はここに入らない (ADR-0041 決定4)。
                name: 'SCAN_VAULT_URL'
                value: scanVaultUrl
              }
              {
                // マネージド ID。scan_runs.started_by にも入る (ADR-0042 決定2)。
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
            ],
            postgresPasswordEnv
          )
        }
      ]
    }
  }
}

output name string = scanJob.name
output id string = scanJob.id
output triggerType string = useSchedule ? 'Schedule' : 'Manual'
