#!/usr/bin/env bash
# One-command deployment of the TAM 360 cloud Calculator agent. Run in Azure Cloud Shell (Bash):
#   git clone -b tam360-cloud https://github.com/varunashat/TAMStorageFile && cd TAMStorageFile/tam360-cloud && bash deploy.sh
set -euo pipefail
SUB="${SUB:-03a0c620-9c56-4e80-abb4-e91c65b17286}"
RG="${RG:-rg-tam360-calc}"
LOC="${LOC:-uaenorth}"
PREFIX="${PREFIX:-tam360calc}"
FOUNDRY_NAME="${FOUNDRY_NAME:-arcseccopilot-ai-3xsmhoyzycob6}"
FOUNDRY_ENDPOINT="${FOUNDRY_ENDPOINT:-https://arcseccopilot-ai-3xsmhoyzycob6.services.ai.azure.com}"
FOUNDRY_DEPLOYMENT="${FOUNDRY_DEPLOYMENT:-gpt-6.1-sol}"
TAG="v$(date +%Y%m%d%H%M)"
say(){ printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
val(){ python3 -c "import sys,json;print(json.load(sys.stdin)['$1']['value'])"; }

say "Subscription $SUB"
az account set --subscription "$SUB"
TENANT=$(az account show --query tenantId -o tsv)
az extension add --name containerapp --upgrade --only-show-errors >/dev/null
for ns in Microsoft.App Microsoft.ContainerRegistry Microsoft.OperationalInsights Microsoft.Storage; do
  az provider register -n "$ns" --wait --only-show-errors >/dev/null
done

say "Resource group $RG ($LOC)"
az group create -n "$RG" -l "$LOC" --only-show-errors -o none

say "Step 1/5 · registry, storage, identity, Container Apps environment"
OUT=$(az deployment group create -g "$RG" -f infra/main.bicep --only-show-errors \
  -p prefix="$PREFIX" foundryEndpoint="$FOUNDRY_ENDPOINT" foundryDeployment="$FOUNDRY_DEPLOYMENT" deployApp=false \
  --query properties.outputs -o json)
ACR=$(echo "$OUT" | val acrName)
LOGIN=$(echo "$OUT" | val acrLoginServer)
PRINCIPAL=$(echo "$OUT" | val identityPrincipalId)

say "Step 2/5 · building the container image in Azure ($LOGIN/calc-worker:$TAG)"
az acr build -r "$ACR" -t "calc-worker:$TAG" calc-worker --only-show-errors -o none

say "Step 3/5 · Foundry access for the app identity (Cognitive Services OpenAI User)"
FOUNDRY_ID=$(az cognitiveservices account list --query "[?name=='$FOUNDRY_NAME'].id | [0]" -o tsv)
if [ -z "$FOUNDRY_ID" ]; then echo "Foundry resource $FOUNDRY_NAME not found in this subscription. Set FOUNDRY_NAME and re-run."; exit 1; fi
az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services OpenAI User" --scope "$FOUNDRY_ID" --only-show-errors -o none || echo "(role assignment already exists)"

say "Step 4/5 · deploying the app"
OUT=$(az deployment group create -g "$RG" -f infra/main.bicep --only-show-errors \
  -p prefix="$PREFIX" foundryEndpoint="$FOUNDRY_ENDPOINT" foundryDeployment="$FOUNDRY_DEPLOYMENT" deployApp=true \
     image="$LOGIN/calc-worker:$TAG" --query properties.outputs -o json)
APP=$(echo "$OUT" | val appName)
URL=$(echo "$OUT" | val appUrl)

say "Step 5/5 · Microsoft Entra sign-in (only your organisation can open the app)"
set +e
APPID=$(az ad app list --display-name "TAM 360 Calculator agent" --query "[0].appId" -o tsv)
if [ -z "$APPID" ]; then
  APPID=$(az ad app create --display-name "TAM 360 Calculator agent" --sign-in-audience AzureADMyOrg \
    --web-redirect-uris "$URL/.auth/login/aad/callback" --enable-id-token-issuance true --query appId -o tsv)
else
  az ad app update --id "$APPID" --web-redirect-uris "$URL/.auth/login/aad/callback" --enable-id-token-issuance true
fi
SECRET=""
[ -n "$APPID" ] && SECRET=$(az ad app credential reset --id "$APPID" --display-name easyauth --years 2 --query password -o tsv 2>/dev/null)
if [ -z "$APPID" ] || [ -z "$SECRET" ]; then
  az containerapp ingress disable -g "$RG" -n "$APP" -o none
  echo "Could not create the Entra app registration (your account may not be allowed to)."
  echo "For safety the app's public address is DISABLED until sign-in is configured."
  echo "Ask IT for an app registration with redirect URI $URL/.auth/login/aad/callback, then run:"
  echo "  APPID=<application id> SECRET=<client secret> bash auth.sh"
  exit 2
fi
az ad sp create --id "$APPID" -o none 2>/dev/null
set -e
az containerapp auth microsoft update -g "$RG" -n "$APP" --client-id "$APPID" --client-secret "$SECRET" \
  --issuer "https://login.microsoftonline.com/$TENANT/v2.0" --yes -o none
az containerapp auth update -g "$RG" -n "$APP" --enabled true --unauthenticated-client-action RedirectToLoginPage \
  --redirect-provider azureactivedirectory -o none

say "Done. Open: $URL"
echo "1) Sign in with your Noventiq account."
echo "2) Click 'Sign in to the calculator account', then 'Open live browser view', and sign in there with the dedicated calculator Microsoft account (once)."
echo "3) Type the services and click 'Build & get share link'."
