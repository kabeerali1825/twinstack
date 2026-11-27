param location string = 'northeurope'
param nodeCount int = 3

resource aks 'Microsoft.ContainerService/managedClusters@2024-02-01' = {
  name: 'aks-api'
  location: location
  sku: {
    name: 'Base'
    tier: 'Standard'
  }
  properties: {
    agentPoolProfiles: [
      {
        name: 'system'
        count: nodeCount
        vmSize: 'Standard_D4s_v5'
        mode: 'System'
      }
    ]
  }
}

resource sql 'Microsoft.Sql/servers/databases@2023-05-01-preview' = {
  name: 'sql-api/orders'
  location: location
  sku: {
    name: 'GP_Gen5_4'
    tier: 'GeneralPurpose'
  }
}

resource st 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: 'stapi'
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
  }
}
