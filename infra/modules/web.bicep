// Web フロントエンド (React + Vite + Fluent UI) をホストする Static Web Apps を構築するモジュール。
// azd deploy が azd-service-name タグでこのリソースを特定し、ビルド成果物 (dist) を配置する。

@description('Static Web Apps リソースの名前。')
param name string

@description('Static Web Apps を配置するリージョン。対応リージョンが限られるため location とは別に指定する。')
param location string

@description('全リソースに付与する共通タグ。')
param tags object

@description('azd deploy のターゲット識別に使うサービス名。')
param serviceName string = 'web'

resource web 'Microsoft.Web/staticSites@2024-11-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': serviceName })
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    // azd がローカルビルドの成果物をアップロードするため、GitHub 連携は行わない。
    provider: 'Custom'
    stagingEnvironmentPolicy: 'Enabled'
    allowConfigFileUpdates: true
    enterpriseGradeCdnStatus: 'Disabled'
  }
}

output name string = web.name
output uri string = 'https://${web.properties.defaultHostname}'
output defaultHostname string = web.properties.defaultHostname
