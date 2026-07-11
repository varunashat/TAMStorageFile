#!/usr/bin/env bash
set -Eeuo pipefail

SUBSCRIPTION_ID="46b6491f-e203-4e75-a882-355fbc782320"
RG="rg-youtube-ai-prod"
AOAI="aoai-ytauto-cb9288fc"
DEPLOYMENT_NAME="gpt-4.1-mini"
BASE="$HOME/youtube-ai-deployment"
LOG="$BASE/model-fix.log"

mkdir -p "$BASE"
exec 9>"$BASE/model-fix.lock"
if ! flock -n 9; then
  echo "MODEL FIX ALREADY RUNNING"
  exit 0
fi
exec >>"$LOG" 2>&1
trap 'echo "ERROR on line $LINENO"' ERR

echo "=================================================="
echo "MODEL FIX AND DEPLOYMENT RESUME"
echo "Started: $(date -Is)"
echo "=================================================="

az account set --subscription "$SUBSCRIPTION_ID"
pkill -TERM -f '[d]eploy-all.sh' 2>/dev/null || true
pkill -TERM -f '^bash /tmp/tmp\.' 2>/dev/null || true
sleep 3

if az cognitiveservices account deployment show --name "$AOAI" --resource-group "$RG" --deployment-name "$DEPLOYMENT_NAME" --output none 2>/dev/null; then
  echo "Compatibility deployment already exists."
else
  MODELS_JSON="$(mktemp)"
  CANDIDATES="$(mktemp)"
  az cognitiveservices account list-models --name "$AOAI" --resource-group "$RG" --output json > "$MODELS_JSON"

  python3 - "$MODELS_JSON" > "$CANDIDATES" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    items = json.load(f)
rows = []
for item in items:
    model = item.get("model") or item
    name = model.get("name") or item.get("name")
    version = model.get("version") or item.get("version")
    fmt = str(model.get("format") or item.get("format") or "").lower()
    state = " ".join(str(x or "") for x in [item.get("lifecycleStatus"), item.get("status"), model.get("lifecycleStatus"), model.get("status")]).lower()
    caps = item.get("capabilities") or model.get("capabilities") or {}
    if fmt and fmt != "openai":
        continue
    if not name or not version or not str(name).startswith("gpt-"):
        continue
    lowered = str(name).lower()
    if any(token in lowered for token in ["image", "audio", "realtime", "transcribe", "embedding"]):
        continue
    if "deprecat" in state or "retir" in state:
        continue
    if isinstance(caps, dict) and caps:
        flags = [caps.get("chatCompletion"), caps.get("chat_completions"), caps.get("responses")]
        present = [f for f in flags if f is not None]
        if present and all(f is False for f in present):
            continue
    priority = 50
    for idx, prefix in enumerate(["gpt-5-mini", "gpt-5-nano", "gpt-5", "gpt-4.1-mini", "gpt-4o-mini", "gpt-4.1", "gpt-4o"]):
        if str(name).startswith(prefix):
            priority = idx
            break
    rows.append((priority, str(name), str(version)))
for _, name, version in sorted(set(rows), key=lambda x: (x[0], x[1], x[2])):
    print(f"{name}\t{version}")
PY

  SUCCESS=0
  while IFS=$'\t' read -r MODEL_NAME MODEL_VERSION; do
    [ -n "${MODEL_NAME:-}" ] || continue
    for SKU in GlobalStandard DataZoneStandard Standard; do
      echo "Trying model=$MODEL_NAME version=$MODEL_VERSION sku=$SKU"
      if az cognitiveservices account deployment create --name "$AOAI" --resource-group "$RG" --deployment-name "$DEPLOYMENT_NAME" --model-format OpenAI --model-name "$MODEL_NAME" --model-version "$MODEL_VERSION" --sku-name "$SKU" --sku-capacity 1 --only-show-errors --output none; then
        echo "MODEL DEPLOYED: $MODEL_NAME $MODEL_VERSION via $SKU"
        SUCCESS=1
        break 2
      fi
    done
  done < "$CANDIDATES"
  rm -f "$MODELS_JSON" "$CANDIDATES"
  if [ "$SUCCESS" -ne 1 ]; then
    echo "ERROR: No currently deployable chat model succeeded."
    exit 1
  fi
fi

az cognitiveservices account deployment show --name "$AOAI" --resource-group "$RG" --deployment-name "$DEPLOYMENT_NAME" --query '{Deployment:name,Model:properties.model.name,Version:properties.model.version,Status:properties.provisioningState,SKU:sku.name}' --output table

if [ ! -f "$BASE/deploy-all.sh" ]; then
  echo "ERROR: $BASE/deploy-all.sh is missing."
  exit 1
fi
if [ -f "$BASE/deployment.log" ]; then
  mv "$BASE/deployment.log" "$BASE/deployment-before-resume-$(date +%Y%m%d-%H%M%S).log"
fi

echo "MODEL FIX READY"
echo "Resuming full deployment..."
exec flock -n "$BASE/deploy.lock" bash "$BASE/deploy-all.sh"
