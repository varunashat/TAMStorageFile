#!/usr/bin/env bash
set -Eeuo pipefail

SUBSCRIPTION_ID="46b6491f-e203-4e75-a882-355fbc782320"
TARGET_RG="rg-youtube-ai-prod"
AGENT_RG="rg-chatgpt-azure-agent"
AGENT_APP="aca-chatgpt-agent-f1962e"
ACR_NAME="acrcgptf1962e"

SUFFIX="cb9288fc"
STORAGE_ACCOUNT="stytauto${SUFFIX}"
KEY_VAULT="kv-ytauto-${SUFFIX}"
WORKER_IDENTITY="id-youtube-worker-${SUFFIX}"
AOAI_NAME="aoai-ytauto-${SUFFIX}"
SPEECH_NAME="speech-ytauto-${SUFFIX}"
LOG_WORKSPACE="log-ytauto-${SUFFIX}"
ACA_ENV="cae-ytauto-${SUFFIX}"
JOB_NAME="job-youtube-ai-video"
MODEL_DEPLOYMENT="script-model"

TOPIC_QUEUE="youtube-topics"
STATUS_QUEUE="youtube-status"
DEADLETTER_QUEUE="youtube-deadletter"

trap 'echo "Deployment failed on line ${LINENO}." >&2' ERR

az account set --subscription "$SUBSCRIPTION_ID"
LOCATION="$(az group show --name "$TARGET_RG" --query location --output tsv)"
echo "Deploying Azure YouTube AI Factory in ${LOCATION}"

for provider in \
  Microsoft.CognitiveServices \
  Microsoft.App \
  Microsoft.OperationalInsights \
  Microsoft.ContainerRegistry \
  Microsoft.Storage \
  Microsoft.KeyVault \
  Microsoft.ManagedIdentity
do
  state="$(az provider show --namespace "$provider" --query registrationState --output tsv 2>/dev/null || true)"
  if [[ "$state" != "Registered" ]]; then
    echo "Registering ${provider}"
    az provider register --namespace "$provider" --wait --only-show-errors
  fi
done

assign_role() {
  local principal_id="$1"
  local role_name="$2"
  local scope="$3"

  local count
  count="$(
    az role assignment list \
      --assignee "$principal_id" \
      --scope "$scope" \
      --query "[?roleDefinitionName=='${role_name}'] | length(@)" \
      --output tsv 2>/dev/null || echo 0
  )"

  if [[ "$count" != "0" ]]; then
    echo "Role already assigned: ${role_name}"
    return
  fi

  for attempt in 1 2 3 4 5 6
  do
    if az role assignment create \
      --assignee-object-id "$principal_id" \
      --assignee-principal-type ServicePrincipal \
      --role "$role_name" \
      --scope "$scope" \
      --only-show-errors \
      --output none
    then
      echo "Assigned role: ${role_name}"
      return
    fi
    echo "Waiting for role assignment propagation (${attempt}/6)"
    sleep 10
  done

  echo "Unable to assign role ${role_name}" >&2
  return 1
}

if ! az storage account show -n "$STORAGE_ACCOUNT" -g "$TARGET_RG" -o none 2>/dev/null; then
  az storage account create \
    -n "$STORAGE_ACCOUNT" \
    -g "$TARGET_RG" \
    -l "$LOCATION" \
    --sku Standard_LRS \
    --kind StorageV2 \
    --min-tls-version TLS1_2 \
    --https-only true \
    --allow-blob-public-access false \
    --only-show-errors \
    -o none
fi

STORAGE_KEY="$(
  az storage account keys list \
    -n "$STORAGE_ACCOUNT" \
    -g "$TARGET_RG" \
    --query "[0].value" \
    -o tsv
)"

for queue_name in "$TOPIC_QUEUE" "$STATUS_QUEUE" "$DEADLETTER_QUEUE"
do
  az storage queue create \
    --name "$queue_name" \
    --account-name "$STORAGE_ACCOUNT" \
    --account-key "$STORAGE_KEY" \
    --only-show-errors \
    -o none
done

for container_name in scripts audio images thumbnails videos metadata
do
  az storage container create \
    --name "$container_name" \
    --account-name "$STORAGE_ACCOUNT" \
    --account-key "$STORAGE_KEY" \
    --public-access off \
    --only-show-errors \
    -o none
done
unset STORAGE_KEY

if ! az keyvault show -n "$KEY_VAULT" -g "$TARGET_RG" -o none 2>/dev/null; then
  az keyvault create \
    -n "$KEY_VAULT" \
    -g "$TARGET_RG" \
    -l "$LOCATION" \
    --enable-rbac-authorization true \
    --only-show-errors \
    -o none
fi

if ! az identity show -n "$WORKER_IDENTITY" -g "$TARGET_RG" -o none 2>/dev/null; then
  az identity create \
    -n "$WORKER_IDENTITY" \
    -g "$TARGET_RG" \
    -l "$LOCATION" \
    --only-show-errors \
    -o none
fi

WORKER_ID="$(
  az identity show -n "$WORKER_IDENTITY" -g "$TARGET_RG" --query id -o tsv
)"
WORKER_PRINCIPAL_ID="$(
  az identity show -n "$WORKER_IDENTITY" -g "$TARGET_RG" --query principalId -o tsv
)"
STORAGE_ID="$(
  az storage account show -n "$STORAGE_ACCOUNT" -g "$TARGET_RG" --query id -o tsv
)"
KV_ID="$(
  az keyvault show -n "$KEY_VAULT" -g "$TARGET_RG" --query id -o tsv
)"
ACR_ID="$(
  az acr show -n "$ACR_NAME" -g "$AGENT_RG" --query id -o tsv
)"
ACR_LOGIN_SERVER="$(
  az acr show -n "$ACR_NAME" -g "$AGENT_RG" --query loginServer -o tsv
)"

assign_role "$WORKER_PRINCIPAL_ID" "Storage Queue Data Contributor" "$STORAGE_ID"
assign_role "$WORKER_PRINCIPAL_ID" "Storage Blob Data Contributor" "$STORAGE_ID"
assign_role "$WORKER_PRINCIPAL_ID" "Key Vault Secrets User" "$KV_ID"
assign_role "$WORKER_PRINCIPAL_ID" "AcrPull" "$ACR_ID"

if ! az cognitiveservices account show -n "$AOAI_NAME" -g "$TARGET_RG" -o none 2>/dev/null; then
  az cognitiveservices account create \
    -n "$AOAI_NAME" \
    -g "$TARGET_RG" \
    -l "$LOCATION" \
    --kind OpenAI \
    --sku S0 \
    --custom-domain "$AOAI_NAME" \
    --yes \
    --only-show-errors \
    -o none
fi

AOAI_ID="$(
  az cognitiveservices account show -n "$AOAI_NAME" -g "$TARGET_RG" --query id -o tsv
)"
AOAI_ENDPOINT="$(
  az cognitiveservices account show -n "$AOAI_NAME" -g "$TARGET_RG" --query properties.endpoint -o tsv
)"
assign_role "$WORKER_PRINCIPAL_ID" "Cognitive Services OpenAI User" "$AOAI_ID"

if ! az cognitiveservices account deployment show \
  -n "$AOAI_NAME" \
  -g "$TARGET_RG" \
  --deployment-name "$MODEL_DEPLOYMENT" \
  -o none 2>/dev/null
then
  models_file="$(mktemp)"
  candidates_file="$(mktemp)"

  az cognitiveservices account list-models \
    -n "$AOAI_NAME" \
    -g "$TARGET_RG" \
    -o json > "$models_file"

  python3 - "$models_file" > "$candidates_file" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    items = json.load(handle)

preference = {
    "gpt-4o-mini": 0,
    "gpt-4.1-mini": 1,
    "gpt-4.1": 2,
    "gpt-4o": 3,
    "gpt-5-mini": 4,
    "gpt-5-nano": 5,
}

candidates = []
for item in items:
    model = item.get("model") or {}
    name = str(model.get("name") or "")
    version = str(model.get("version") or "")
    fmt = str(model.get("format") or "").lower()
    lifecycle = str(
        item.get("lifecycleStatus")
        or model.get("lifecycleStatus")
        or ""
    ).lower()
    capabilities = item.get("capabilities") or model.get("capabilities") or {}

    if fmt != "openai" or not name or not version:
        continue
    if not name.startswith("gpt-"):
        continue
    if "deprecat" in lifecycle or "retir" in lifecycle:
        continue

    capability_text = json.dumps(capabilities).lower()
    if capabilities and not any(
        token in capability_text
        for token in ("chat", "completion", "responses")
    ):
        continue

    candidates.append((preference.get(name, 100), name, version))

for _, name, version in sorted(
    set(candidates),
    key=lambda value: (value[0], value[1], value[2]),
):
    print(f"{name}\t{version}")
PY

  deployed="false"
  while IFS=$'\t' read -r model_name model_version
  do
    [[ -n "$model_name" && -n "$model_version" ]] || continue

    for sku_name in GlobalStandard DataZoneStandard Standard
    do
      echo "Trying ${model_name} ${model_version} with ${sku_name}"
      if az cognitiveservices account deployment create \
        -n "$AOAI_NAME" \
        -g "$TARGET_RG" \
        --deployment-name "$MODEL_DEPLOYMENT" \
        --model-format OpenAI \
        --model-name "$model_name" \
        --model-version "$model_version" \
        --sku-name "$sku_name" \
        --sku-capacity 1 \
        --only-show-errors \
        -o none
      then
        deployed="true"
        break 2
      fi

      az cognitiveservices account deployment delete \
        -n "$AOAI_NAME" \
        -g "$TARGET_RG" \
        --deployment-name "$MODEL_DEPLOYMENT" \
        --yes \
        --only-show-errors \
        -o none 2>/dev/null || true
    done
  done < "$candidates_file"

  rm -f "$models_file" "$candidates_file"

  if [[ "$deployed" != "true" ]]; then
    echo "No deployable chat model was available in ${LOCATION}." >&2
    exit 1
  fi
fi

if ! az cognitiveservices account show -n "$SPEECH_NAME" -g "$TARGET_RG" -o none 2>/dev/null; then
  az cognitiveservices account create \
    -n "$SPEECH_NAME" \
    -g "$TARGET_RG" \
    -l "$LOCATION" \
    --kind SpeechServices \
    --sku S0 \
    --custom-domain "$SPEECH_NAME" \
    --yes \
    --only-show-errors \
    -o none
fi

SPEECH_ID="$(
  az cognitiveservices account show -n "$SPEECH_NAME" -g "$TARGET_RG" --query id -o tsv
)"
assign_role "$WORKER_PRINCIPAL_ID" "Cognitive Services User" "$SPEECH_ID"

AOAI_KEY="$(
  az cognitiveservices account keys list \
    -n "$AOAI_NAME" \
    -g "$TARGET_RG" \
    --query key1 \
    -o tsv
)"
az keyvault secret set \
  --vault-name "$KEY_VAULT" \
  --name aoai-key \
  --value "$AOAI_KEY" \
  --only-show-errors \
  -o none
unset AOAI_KEY

SPEECH_KEY="$(
  az cognitiveservices account keys list \
    -n "$SPEECH_NAME" \
    -g "$TARGET_RG" \
    --query key1 \
    -o tsv
)"
az keyvault secret set \
  --vault-name "$KEY_VAULT" \
  --name speech-key \
  --value "$SPEECH_KEY" \
  --only-show-errors \
  -o none
unset SPEECH_KEY

if ! az monitor log-analytics workspace show \
  -n "$LOG_WORKSPACE" \
  -g "$TARGET_RG" \
  -o none 2>/dev/null
then
  az monitor log-analytics workspace create \
    -n "$LOG_WORKSPACE" \
    -g "$TARGET_RG" \
    -l "$LOCATION" \
    --only-show-errors \
    -o none
fi

if ! az containerapp env show -n "$ACA_ENV" -g "$TARGET_RG" -o none 2>/dev/null; then
  WORKSPACE_CUSTOMER_ID="$(
    az monitor log-analytics workspace show \
      -n "$LOG_WORKSPACE" \
      -g "$TARGET_RG" \
      --query customerId \
      -o tsv
  )"
  WORKSPACE_SHARED_KEY="$(
    az monitor log-analytics workspace get-shared-keys \
      -n "$LOG_WORKSPACE" \
      -g "$TARGET_RG" \
      --query primarySharedKey \
      -o tsv
  )"

  az containerapp env create \
    -n "$ACA_ENV" \
    -g "$TARGET_RG" \
    -l "$LOCATION" \
    --logs-workspace-id "$WORKSPACE_CUSTOMER_ID" \
    --logs-workspace-key "$WORKSPACE_SHARED_KEY" \
    --only-show-errors \
    -o none

  unset WORKSPACE_SHARED_KEY
fi

IMAGE_TAG="${GITHUB_RUN_NUMBER:-manual}-${GITHUB_SHA:-local}"
IMAGE_TAG="${IMAGE_TAG:0:40}"
IMAGE="${ACR_LOGIN_SERVER}/youtube-video-worker:${IMAGE_TAG}"

az acr build \
  --registry "$ACR_NAME" \
  --image "youtube-video-worker:${IMAGE_TAG}" \
  azure-youtube-factory/worker \
  --only-show-errors \
  -o none

ENV_VARS=(
  "STORAGE_ACCOUNT=${STORAGE_ACCOUNT}"
  "TOPIC_QUEUE=${TOPIC_QUEUE}"
  "STATUS_QUEUE=${STATUS_QUEUE}"
  "DEADLETTER_QUEUE=${DEADLETTER_QUEUE}"
  "KEY_VAULT_URL=https://${KEY_VAULT}.vault.azure.net/"
  "AOAI_ENDPOINT=${AOAI_ENDPOINT}"
  "AOAI_DEPLOYMENT=${MODEL_DEPLOYMENT}"
  "AOAI_API_VERSION=2024-10-21"
  "SPEECH_REGION=${LOCATION}"
  "VOICE=en-IN-PrabhatNeural"
  "YOUTUBE_PRIVACY=private"
  "VIDEO_WIDTH=1080"
  "VIDEO_HEIGHT=1920"
  "MAX_SCENES=6"
)

if az containerapp job show -n "$JOB_NAME" -g "$TARGET_RG" -o none 2>/dev/null; then
  az containerapp job update \
    -n "$JOB_NAME" \
    -g "$TARGET_RG" \
    --image "$IMAGE" \
    --cpu 1.0 \
    --memory 2.0Gi \
    --replica-timeout 3600 \
    --replica-retry-limit 1 \
    --polling-interval 30 \
    --min-executions 0 \
    --max-executions 1 \
    --scale-rule-name youtube-topic-queue \
    --scale-rule-type azure-queue \
    --scale-rule-identity "$WORKER_ID" \
    --scale-rule-metadata \
      "accountName=${STORAGE_ACCOUNT}" \
      "queueName=${TOPIC_QUEUE}" \
      "queueLength=1" \
      "cloud=AzurePublicCloud" \
    --replace-env-vars "${ENV_VARS[@]}" \
    --only-show-errors \
    -o none
else
  az containerapp job create \
    -n "$JOB_NAME" \
    -g "$TARGET_RG" \
    --environment "$ACA_ENV" \
    --trigger-type Event \
    --replica-timeout 3600 \
    --replica-retry-limit 1 \
    --replica-completion-count 1 \
    --parallelism 1 \
    --polling-interval 30 \
    --min-executions 0 \
    --max-executions 1 \
    --scale-rule-name youtube-topic-queue \
    --scale-rule-type azure-queue \
    --scale-rule-identity "$WORKER_ID" \
    --scale-rule-metadata \
      "accountName=${STORAGE_ACCOUNT}" \
      "queueName=${TOPIC_QUEUE}" \
      "queueLength=1" \
      "cloud=AzurePublicCloud" \
    --image "$IMAGE" \
    --cpu 1.0 \
    --memory 2.0Gi \
    --mi-user-assigned "$WORKER_ID" \
    --registry-server "$ACR_LOGIN_SERVER" \
    --registry-identity "$WORKER_ID" \
    --env-vars "${ENV_VARS[@]}" \
    --only-show-errors \
    -o none
fi

if az containerapp show -n "$AGENT_APP" -g "$AGENT_RG" -o none 2>/dev/null; then
  AGENT_PRINCIPAL_ID="$(
    az containerapp show \
      -n "$AGENT_APP" \
      -g "$AGENT_RG" \
      --query identity.principalId \
      -o tsv
  )"
  if [[ -n "$AGENT_PRINCIPAL_ID" && "$AGENT_PRINCIPAL_ID" != "null" ]]; then
    assign_role "$AGENT_PRINCIPAL_ID" "Storage Queue Data Contributor" "$STORAGE_ID"
  fi

  az containerapp update \
    -n "$AGENT_APP" \
    -g "$AGENT_RG" \
    --set-env-vars \
      "YOUTUBE_STORAGE_ACCOUNT=${STORAGE_ACCOUNT}" \
      "STORAGE_ACCOUNT=${STORAGE_ACCOUNT}" \
      "YOUTUBE_TOPIC_QUEUE=${TOPIC_QUEUE}" \
      "TOPIC_QUEUE=${TOPIC_QUEUE}" \
      "YOUTUBE_STATUS_QUEUE=${STATUS_QUEUE}" \
      "STATUS_QUEUE=${STATUS_QUEUE}" \
      "YOUTUBE_DEADLETTER_QUEUE=${DEADLETTER_QUEUE}" \
      "DEADLETTER_QUEUE=${DEADLETTER_QUEUE}" \
      "YOUTUBE_JOB_RESOURCE_GROUP=${TARGET_RG}" \
      "YOUTUBE_JOB_NAME=${JOB_NAME}" \
    --only-show-errors \
    -o none
fi

MODEL_INFO="$(
  az cognitiveservices account deployment show \
    -n "$AOAI_NAME" \
    -g "$TARGET_RG" \
    --deployment-name "$MODEL_DEPLOYMENT" \
    --query "{model:properties.model.name,version:properties.model.version,sku:sku.name}" \
    -o json
)"

cat > deployment-summary.md <<EOF
- ✅ Resource group: \`${TARGET_RG}\`
- ✅ Storage and processing queues configured
- ✅ Foundry/OpenAI script model deployed as \`${MODEL_DEPLOYMENT}\`
- ✅ Azure Speech configured with \`en-IN-PrabhatNeural\`
- ✅ Worker image built: \`${IMAGE}\`
- ✅ Event-driven Container Apps Job: \`${JOB_NAME}\`
- ✅ Existing ChatGPT Azure control API connected
- ⚠️ YouTube upload remains private and requires one-time Google OAuth
- Model details: \`${MODEL_INFO}\`
EOF

echo "=================================================="
echo "AZURE VIDEO FACTORY READY"
echo "Job: ${JOB_NAME}"
echo "YouTube upload: OAUTH PENDING"
echo "=================================================="
