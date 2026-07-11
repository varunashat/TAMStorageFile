#!/usr/bin/env bash
set -Eeuo pipefail

SUBSCRIPTION_ID="46b6491f-e203-4e75-a882-355fbc782320"
TARGET_RG="rg-youtube-ai-prod"
AGENT_RG="rg-chatgpt-azure-agent"
AGENT_APP="aca-chatgpt-agent-f1962e"
STORAGE_ACCOUNT="stytautocb9288fc"
TOPIC_QUEUE="youtube-topics"
STATUS_QUEUE="youtube-status"
DEADLETTER_QUEUE="youtube-deadletter"
JOB_NAME="job-youtube-ai-video"

az account set --subscription "$SUBSCRIPTION_ID"

echo "Applying all queue environment aliases to the Custom GPT API..."

az containerapp update \
  --resource-group "$AGENT_RG" \
  --name "$AGENT_APP" \
  --set-env-vars \
    "AZURE_STORAGE_ACCOUNT=${STORAGE_ACCOUNT}" \
    "AZURE_STORAGE_ACCOUNT_NAME=${STORAGE_ACCOUNT}" \
    "STORAGE_ACCOUNT=${STORAGE_ACCOUNT}" \
    "STORAGE_ACCOUNT_NAME=${STORAGE_ACCOUNT}" \
    "YOUTUBE_STORAGE_ACCOUNT=${STORAGE_ACCOUNT}" \
    "YOUTUBE_STORAGE_ACCOUNT_NAME=${STORAGE_ACCOUNT}" \
    "YOUTUBE_TOPIC_QUEUE=${TOPIC_QUEUE}" \
    "YOUTUBE_QUEUE_NAME=${TOPIC_QUEUE}" \
    "TOPIC_QUEUE=${TOPIC_QUEUE}" \
    "STORAGE_QUEUE_NAME=${TOPIC_QUEUE}" \
    "YOUTUBE_STATUS_QUEUE=${STATUS_QUEUE}" \
    "STATUS_QUEUE=${STATUS_QUEUE}" \
    "STATUS_QUEUE_NAME=${STATUS_QUEUE}" \
    "YOUTUBE_DEADLETTER_QUEUE=${DEADLETTER_QUEUE}" \
    "DEADLETTER_QUEUE=${DEADLETTER_QUEUE}" \
    "DEADLETTER_QUEUE_NAME=${DEADLETTER_QUEUE}" \
    "YOUTUBE_JOB_RESOURCE_GROUP=${TARGET_RG}" \
    "YOUTUBE_JOB_NAME=${JOB_NAME}" \
    "CONTAINER_JOB_RESOURCE_GROUP=${TARGET_RG}" \
    "CONTAINER_JOB_NAME=${JOB_NAME}" \
  --only-show-errors \
  --output none

active_revision="$(
  az containerapp revision list \
    --resource-group "$AGENT_RG" \
    --name "$AGENT_APP" \
    --query "[?properties.active==\`true\`].name | [0]" \
    --output tsv
)"

if [[ -n "$active_revision" ]]; then
  az containerapp revision restart \
    --resource-group "$AGENT_RG" \
    --revision "$active_revision" \
    --only-show-errors \
    --output none
fi

configured_count="$(
  az containerapp show \
    --resource-group "$AGENT_RG" \
    --name "$AGENT_APP" \
    --query "length(properties.template.containers[0].env[?name=='AZURE_STORAGE_ACCOUNT_NAME' || name=='YOUTUBE_QUEUE_NAME' || name=='YOUTUBE_TOPIC_QUEUE'])" \
    --output tsv
)"

if [[ "$configured_count" != "3" ]]; then
  echo "Required queue environment aliases were not applied." >&2
  exit 1
fi

echo "CUSTOM GPT QUEUE ENVIRONMENT READY"
