// TAM 360 cloud Calculator agent: Container Apps + Azure Files + registry + managed identity.
// Deployed twice by deploy.sh: first with deployApp=false (registry for the image build), then with the image.
@description('Azure region, e.g. uaenorth')
param location string = resourceGroup().location
@description('Short lowercase prefix for resource names')
@minLength(3)
@maxLength(12)
param prefix string = 'tam360calc'
param foundryEndpoint string
param foundryDeployment string
param defaultRegion string = 'UAE North'
param deployApp bool = false
param image string = ''

var suffix = uniqueString(resourceGroup().id)
var acrName = take(replace('${prefix}${suffix}', '-', ''), 50)

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-id'
  location: location
}

// AcrPull for the app identity
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, 'acrpull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take('${replace(prefix, '-', '')}${suffix}st', 24)
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { minimumTlsVersion: 'TLS1_2', allowBlobPublicAccess: false, supportsHttpsTrafficOnly: true }
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  name: '${storage.name}/default/calcdata'
  properties: { shareQuota: 5 }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
  }
}

resource envStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: env
  name: 'calcdata'
  properties: {
    azureFile: { accountName: storage.name, accountKey: storage.listKeys().keys[0].value, shareName: 'calcdata', accessMode: 'ReadWrite' }
  }
  dependsOn: [ share ]
}

resource app 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: '${prefix}-app'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      registries: [ { server: acr.properties.loginServer, identity: identity.id } ]
    }
    template: {
      containers: [
        {
          name: 'calc-worker'
          image: image
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: [
            { name: 'FOUNDRY_ENDPOINT', value: foundryEndpoint }
            { name: 'FOUNDRY_DEPLOYMENT', value: foundryDeployment }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'DEFAULT_REGION', value: defaultRegion }
            { name: 'DATA_DIR', value: '/data' }
          ]
          volumeMounts: [ { volumeName: 'data', mountPath: '/data' } ]
          probes: [ { type: 'Liveness', httpGet: { path: '/healthz', port: 8000 }, initialDelaySeconds: 20, periodSeconds: 30 } ]
        }
      ]
      volumes: [ { name: 'data', storageType: 'AzureFile', storageName: envStorage.name } ]
      scale: { minReplicas: 1, maxReplicas: 1, rules: [ { name: 'http', http: { metadata: { concurrentRequests: '50' } } } ] }
    }
  }
  dependsOn: [ acrPull ]
}

output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output identityPrincipalId string = identity.properties.principalId
output appName string = deployApp ? app.name : ''
output appUrl string = deployApp ? 'https://${app.properties.configuration.ingress.fqdn}' : ''
