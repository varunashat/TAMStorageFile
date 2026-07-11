#!/usr/bin/env bash
set -Eeuo pipefail

STORAGE_ACCOUNT="stytautocb9288fc"
RESOURCE_GROUP="rg-youtube-ai-prod"
QUEUE_NAME="youtube-topics"
REQUEST_FILE="azure-youtube-factory/topic-request.json"
CLIENT_ID="7575c675-fb43-4cf0-a349-5ae0d6f04914"

if [[ ! -f "$REQUEST_FILE" ]]; then
  echo "No topic request file was found; skipping queue submission."
  exit 0
fi

PRINCIPAL_ID="$(az ad sp show --id "$CLIENT_ID" --query id --output tsv)"
STORAGE_ID="$(az storage account show --resource-group "$RESOURCE_GROUP" --name "$STORAGE_ACCOUNT" --query id --output tsv)"

ASSIGNMENT_COUNT="$(az role assignment list --assignee-object-id "$PRINCIPAL_ID" --scope "$STORAGE_ID" --query "[?roleDefinitionName=='Storage Queue Data Message Sender'] | length(@)" --output tsv)"
if [[ "$ASSIGNMENT_COUNT" == "0" ]]; then
  az role assignment create \
    --assignee-object-id "$PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "Storage Queue Data Message Sender" \
    --scope "$STORAGE_ID" \
    --output none
  sleep 30
fi

PAYLOAD="$(jq -c . "$REQUEST_FILE")"
az storage message put \
  --queue-name "$QUEUE_NAME" \
  --account-name "$STORAGE_ACCOUNT" \
  --auth-mode login \
  --content "$PAYLOAD" \
  --time-to-live 604800 \
  --only-show-errors \
  --output none

echo "Video request submitted to Azure queue: $QUEUE_NAME"
