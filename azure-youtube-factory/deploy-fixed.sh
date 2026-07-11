#!/usr/bin/env bash
set -Eeuo pipefail

SUBSCRIPTION_ID="46b6491f-e203-4e75-a882-355fbc782320"
TARGET_RG="rg-youtube-ai-prod"
AOAI_NAME="aoai-ytauto-cb9288fc"
MODEL_DEPLOYMENT="script-model"

az account set --subscription "$SUBSCRIPTION_ID"

echo "Ensuring a deployable Azure OpenAI chat model exists..."

existing_state="$(
  az cognitiveservices account deployment show \
    -n "$AOAI_NAME" \
    -g "$TARGET_RG" \
    --deployment-name "$MODEL_DEPLOYMENT" \
    --query properties.provisioningState \
    -o tsv 2>/dev/null || true
)"

if [[ "$existing_state" == "Succeeded" ]]; then
  echo "Model deployment already succeeded: $MODEL_DEPLOYMENT"
else
  if [[ -n "$existing_state" ]]; then
    echo "Removing incomplete model deployment with state: $existing_state"
    az cognitiveservices account deployment delete \
      -n "$AOAI_NAME" \
      -g "$TARGET_RG" \
      --deployment-name "$MODEL_DEPLOYMENT" \
      --yes \
      --only-show-errors \
      -o none || true
  fi

  deployed="false"
  candidates=(
    "gpt-4o-mini|2024-07-18|GlobalStandard"
    "gpt-5-mini|2025-08-07|GlobalStandard"
    "gpt-5.4-mini|2026-03-17|GlobalStandard"
    "gpt-5.5|2026-04-24|GlobalStandard"
    "gpt-chat-latest|2026-06-24|GlobalStandard"
    "gpt-4o|2024-11-20|GlobalStandard"
    "gpt-4o-mini|2024-07-18|Standard"
  )

  for candidate in "${candidates[@]}"; do
    IFS='|' read -r model_name model_version sku_name <<< "$candidate"
    echo "Trying model ${model_name} ${model_version} with ${sku_name}..."

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
      echo "MODEL DEPLOYED: ${model_name} ${model_version} using ${sku_name}"
      deployed="true"
      break
    fi

    az cognitiveservices account deployment delete \
      -n "$AOAI_NAME" \
      -g "$TARGET_RG" \
      --deployment-name "$MODEL_DEPLOYMENT" \
      --yes \
      --only-show-errors \
      -o none 2>/dev/null || true
  done

  if [[ "$deployed" != "true" ]]; then
    echo "ERROR: None of the supported fallback models could be deployed." >&2
    exit 1
  fi
fi

chmod +x azure-youtube-factory/deploy.sh
exec bash azure-youtube-factory/deploy.sh
