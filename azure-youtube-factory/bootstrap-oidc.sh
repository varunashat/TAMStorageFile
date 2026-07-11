#!/usr/bin/env bash
set -Eeuo pipefail

SUBSCRIPTION_ID="46b6491f-e203-4e75-a882-355fbc782320"
TARGET_RG="rg-youtube-ai-prod"
AGENT_RG="rg-chatgpt-azure-agent"
AGENT_APP="aca-chatgpt-agent-f1962e"
ACR_NAME="acrcgptf1962e"
KEY_VAULT="kv-ytauto-cb9288fc"
IDENTITY_NAME="id-github-youtube-deployer-cb9288fc"
FEDERATED_NAME="github-tamstoragefile-main"
GITHUB_SUBJECT="repo:varunashat/TAMStorageFile:ref:refs/heads/main"

az account set --subscription "$SUBSCRIPTION_ID"
LOCATION="$(az group show -n "$TARGET_RG" --query location -o tsv)"

if ! az identity show -n "$IDENTITY_NAME" -g "$TARGET_RG" -o none 2>/dev/null; then
  az identity create \
    -n "$IDENTITY_NAME" \
    -g "$TARGET_RG" \
    -l "$LOCATION" \
    --only-show-errors \
    -o none
fi

if ! az identity federated-credential show \
  --name "$FEDERATED_NAME" \
  --identity-name "$IDENTITY_NAME" \
  --resource-group "$TARGET_RG" \
  -o none 2>/dev/null
then
  az identity federated-credential create \
    --name "$FEDERATED_NAME" \
    --identity-name "$IDENTITY_NAME" \
    --resource-group "$TARGET_RG" \
    --issuer "https://token.actions.githubusercontent.com" \
    --subject "$GITHUB_SUBJECT" \
    --audiences "api://AzureADTokenExchange" \
    --only-show-errors \
    -o none
fi

PRINCIPAL_ID="$(az identity show -n "$IDENTITY_NAME" -g "$TARGET_RG" --query principalId -o tsv)"
CLIENT_ID="$(az identity show -n "$IDENTITY_NAME" -g "$TARGET_RG" --query clientId -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
TARGET_RG_ID="$(az group show -n "$TARGET_RG" --query id -o tsv)"
ACR_ID="$(az acr show -n "$ACR_NAME" -g "$AGENT_RG" --query id -o tsv)"
KV_ID="$(az keyvault show -n "$KEY_VAULT" -g "$TARGET_RG" --query id -o tsv)"
AGENT_APP_ID="$(az containerapp show -n "$AGENT_APP" -g "$AGENT_RG" --query id -o tsv)"

assign_role() {
  local role_name="$1"
  local scope="$2"
  local count
  count="$(az role assignment list --assignee "$PRINCIPAL_ID" --scope "$scope" --query "[?roleDefinitionName=='${role_name}'] | length(@)" -o tsv 2>/dev/null || echo 0)"
  if [[ "$count" == "0" ]]; then
    az role assignment create \
      --assignee-object-id "$PRINCIPAL_ID" \
      --assignee-principal-type ServicePrincipal \
      --role "$role_name" \
      --scope "$scope" \
      --only-show-errors \
      -o none
  fi
}

assign_role "Contributor" "$TARGET_RG_ID"
assign_role "User Access Administrator" "$TARGET_RG_ID"
assign_role "Contributor" "$ACR_ID"
assign_role "Key Vault Secrets Officer" "$KV_ID"
assign_role "Contributor" "$AGENT_APP_ID"

mkdir -p "$HOME/youtube-ai-deployment"
cat > "$HOME/youtube-ai-deployment/github-oidc-values.txt" <<EOF
AZURE_CLIENT_ID=$CLIENT_ID
AZURE_TENANT_ID=$TENANT_ID
AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION_ID
EOF
chmod 600 "$HOME/youtube-ai-deployment/github-oidc-values.txt"

echo "=============================================="
echo "GITHUB OIDC READY"
echo "AZURE_CLIENT_ID=$CLIENT_ID"
echo "AZURE_TENANT_ID=$TENANT_ID"
echo "AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION_ID"
echo "Saved to: ~/youtube-ai-deployment/github-oidc-values.txt"
echo "=============================================="
