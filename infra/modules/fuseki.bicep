// Apache Jena Fuseki を Container Apps 上に構築するモジュール。設計の核心部分。
// 「トリプルストアは再構築可能な射影」という原則を実装する: メインコンテナの entrypoint が
// Blob 上の正本スナップショットを TDB2 にロードし、startup probe がロード完了までトラフィックを止める。
// ingress は internal のみで、書き込み口 (SPARQL Update / GSP) は Core API からしか届かない。

@description('Fuseki コンテナアプリの名前。')
param name string

@description('リソースを配置するリージョン。')
param location string

@description('全リソースに付与する共通タグ。')
param tags object

@description('azd deploy のターゲット識別に使うサービス名。')
param serviceName string = 'fuseki'

@description('Container Apps 環境のリソースID。')
param containerAppsEnvironmentId string

@description('コンテナイメージの取得元 ACR のログインサーバー。')
param containerRegistryLoginServer string

@description('ユーザー割り当てマネージドID のリソースID。')
param identityId string

@description('ユーザー割り当てマネージドID のクライアントID。Azure SDK の資格情報選択に使う。')
param identityClientId string

@description('デプロイするイメージ。azd deploy 前 (初回 provision 時) は空文字でプレースホルダを使う。')
param imageName string = ''

@description('Fuseki コンテナに割り当てる vCPU 数 (文字列。例: 0.5)。')
param cpu string = '0.5'

@description('Fuseki コンテナに割り当てるメモリ (例: 1Gi)。')
param memory string = '1Gi'

@description('JVM のヒープ設定など。メモリ割当を上げた場合はここも合わせて調整する。')
param javaOptions string = '-Xmx768m -XX:+UseSerialGC'

@description('グラフの永続化方式。ephemeral は EmptyDir + 起動時再構築、azureFiles は Azure Files マウント。')
@allowed([
  'ephemeral'
  'azureFiles'
])
param graphPersistence string

@description('azureFiles のときに使う Container Apps 環境の storages 定義名。')
param fusekiStorageName string = ''

@description('Fuseki のデータセット名。SPARQL エンドポイントのパスに現れる。')
param fusekiDataset string = 'ds'

@description('load-snapshot.sh が各 TTL を読み込む名前付きグラフ IRI の接頭辞。config.ttl が unionDefaultGraph を有効にしているため、既定グラフではなく名前付きグラフへ読み込む必要がある。')
param graphIriBase string = 'urn:ontology:graph'

@description('正本 TTL を置く Blob のプレフィックス。infra/modules/api.bicep の blobPrefix と同じ値を main.bicep から渡すこと(値がずれると、API が書き込んだ場所を load-snapshot.sh が読みに行けなくなる)。')
param blobPrefix string = 'approved/'

@description('オントロジー正本を格納する Blob エンドポイント URL。')
param storageAccountUrl string

@description('オントロジー正本の Blob コンテナ名。')
param ontologyBlobContainer string

@description('Fuseki admin パスワードの Key Vault シークレット URI。')
param fusekiPasswordSecretUri string

@description('Application Insights の接続文字列。')
param applicationInsightsConnectionString string

@description('ログレベル。')
param logLevel string = 'INFO'

// azd deploy がイメージを差し替えるまでの間に使う暫定イメージ。
// 初回 provision ではこのイメージで起動するため Fuseki 本体は立ち上がらないが、
// ARM デプロイ自体は成功する (リビジョンが unhealthy になるだけ)。
var placeholderImage = 'mcr.microsoft.com/k8se/quickstart:latest'
var resolvedImage = empty(imageName) ? placeholderImage : imageName

var useAzureFiles = graphPersistence == 'azureFiles' && !empty(fusekiStorageName)
var databasesMountPath = '/fuseki/databases'
var volumeName = 'fuseki-databases'

// ephemeral: レプリカスコープの一時ボリューム (EmptyDir)。レプリカ生存期間中は保持され、
// コンテナ再起動をまたいで残る。容量は 0.5 vCPU で 2 GiB、1 vCPU で 4 GiB。
// azureFiles: 環境に定義した SMB 共有をマウントする (オプトイン。ストア自体を正本にしたい場合)。
var volumes = useAzureFiles
  ? [
      {
        name: volumeName
        storageType: 'AzureFile'
        storageName: fusekiStorageName
        // nobrl はバイトレンジロックを無効化する。TDB2 の観点では「ロックによる
        // 保護が効かなくなる」設定であり、次の2条件が同時に成り立つ前提でのみ許容できる:
        //   1. maxReplicas: 1 で書き込み者が1つに固定されている
        //   2. トリプルストアが正本ではなく再構築可能な射影である (ADR-0002)
        // レプリカを increase する場合、nobrl のままでは安全でない。読み取り水平
        // スケールを行うなら ephemeral (レプリカごとに自分のコピーを持つ) か、
        // AKS + Managed Disk への昇格を選ぶこと。
        mountOptions: 'nobrl'
      }
    ]
  : [
      {
        name: volumeName
        storageType: 'EmptyDir'
      }
    ]

var sharedEnv = [
  {
    // azureFiles は「ストア自体を正本にしたい」構成なので、毎回 Blob から
    // 作り直してストア側の更新を失わないようにする。ephemeral では毎回作り直す。
    name: 'PRESERVE_EXISTING_TDB'
    value: useAzureFiles ? 'true' : 'false'
  }
  {
    name: 'FUSEKI_DATASET'
    value: fusekiDataset
  }
  {
    name: 'FUSEKI_BASE'
    value: '/fuseki'
  }
  {
    // containers/fuseki/load-snapshot.sh と entrypoint.sh が読む TDB2 の作成先。
    name: 'TDB_LOCATION'
    value: '${databasesMountPath}/${fusekiDataset}'
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
    // ADR-0006 のバージョン付き名前付きグラフの接頭辞。スクリプト側にも同じ既定値が
    // あるが、グラフ IRI の体系はインフラ側で明示しておく。
    name: 'GRAPH_IRI_BASE'
    value: graphIriBase
  }
  {
    // containers/fuseki/load-snapshot.sh が読み込み対象を絞り込む接頭辞。
    // api.bicep の BLOB_PREFIX(API の書き込み先)と一致していないと、
    // API が publish したバージョンをローダが永久に見つけられなくなる。
    name: 'BLOB_PREFIX'
    value: blobPrefix
  }
  {
    name: 'AZURE_CLIENT_ID'
    value: identityClientId
  }
  {
    name: 'LOG_LEVEL'
    value: logLevel
  }
]

resource fuseki 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  // azd deploy はこのタグでデプロイ先コンテナアプリを特定する。
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
        // 設計原則: Fuseki は外部に一切出さない。読み書きは Core API / MCP 経由のみ。
        external: false
        targetPort: 3030
        transport: 'auto'
        // 環境内からの http:// アクセス (SPARQL_QUERY_ENDPOINT 等) を許可する。
        allowInsecure: true
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: [
        {
          name: 'fuseki-admin-password'
          keyVaultUrl: fusekiPasswordSecretUri
          identity: identityId
        }
      ]
      registries: [
        {
          server: containerRegistryLoginServer
          identity: identityId
        }
      ]
    }
    template: {
      // Blob から最新スナップショットを取得し tdb2.tdbloader で EmptyDir (または
      // Azure Files) にロードするのは entrypoint.sh(メインコンテナ内)。init
      // コンテナは使わない。azd は provision → deploy の順に実行し、deploy が
      // 差し替えるのはメインコンテナのイメージだけである。init コンテナのイメージは
      // Bicep 経由でしか更新されないため、init を使うと `azd up` 一回ではタグが揃わず、
      // 利用者が `azd provision` をもう一度実行する必要が生じていた。
      // 正本からの再構築は containers/fuseki/entrypoint.sh がメインコンテナ内で
      // 行うので、コードとイメージタグは構造的に常に一致する。
      // Startup プローブ (下記) が再構築の完了までトラフィックを流さない。
      containers: [
        {
          name: 'fuseki'
          image: resolvedImage
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: concat(sharedEnv, [
            {
              name: 'JAVA_OPTIONS'
              value: javaOptions
            }
            {
              // entrypoint.sh がこの値から shiro.ini を生成し admin API を保護する。
              name: 'FUSEKI_ADMIN_PASSWORD'
              secretRef: 'fuseki-admin-password'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: applicationInsightsConnectionString
            }
          ])
          volumeMounts: [
            {
              volumeName: volumeName
              mountPath: databasesMountPath
            }
          ]
          probes: [
            {
              // ロード完了 (= Fuseki が応答可能) までトラフィックを流さない。
              // failureThreshold x periodSeconds = 最大 5 分の再構築時間を許容する。
              type: 'Startup'
              httpGet: {
                path: '/$/ping'
                port: 3030
                scheme: 'HTTP'
              }
              initialDelaySeconds: 10
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 30
              successThreshold: 1
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/$/ping'
                port: 3030
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
        // Phase 1 は単一レプリカ固定。複数レプリカにすると内部 ingress の
        // ロードバランスにより射影書き込みが 1 レプリカにしか届かず、複製間が乖離する。
        minReplicas: 1
        maxReplicas: 1
      }
      volumes: volumes
    }
  }
}

output name string = fuseki.name
output internalFqdn string = fuseki.properties.configuration.ingress.fqdn
output dataset string = fusekiDataset
// `{dataset}` はリテラルのプレースホルダとして残す(Bicep の文字列補間は `${}` なので
// 素の `{dataset}` は展開されずそのまま出力される)。ontology_core.config.Settings の
// SPARQL_QUERY_ENDPOINT 等の既定値と同じ形式で、`FusekiStore._resolve` がリクエストごとに
// 名前空間名へ置換する。ここを `${fusekiDataset}`(固定の "ds")にすると、Task 7 以降
// "ds" は空の予約データセットのため、すべてのクエリが常に 0 件を返し、名前空間の
// 隔離も機能しなくなる。
output queryEndpoint string = 'http://${fuseki.properties.configuration.ingress.fqdn}/{dataset}/sparql'
output updateEndpoint string = 'http://${fuseki.properties.configuration.ingress.fqdn}/{dataset}/update'
output gspEndpoint string = 'http://${fuseki.properties.configuration.ingress.fqdn}/{dataset}/data'
output adminEndpoint string = 'http://${fuseki.properties.configuration.ingress.fqdn}/$/'
