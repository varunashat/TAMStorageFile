#!/usr/bin/env bash
# Turn on Microsoft Entra sign-in when IT created the app registration for you.
# Usage (Cloud Shell, in tam360-cloud):  APPID=<application id> SECRET=<client secret> bash auth.sh
set -euo pipefail
RG="${RG:-rg-tam360-calc}"; PREFIX="${PREFIX:-tam360calc}"; APP="$PREFIX-app"
TENANT=$(az account show --query tenantId -o tsv)
az containerapp auth microsoft update -g "$RG" -n "$APP" --client-id "$APPID" --client-secret "$SECRET" \
  --issuer "https://login.microsoftonline.com/$TENANT/v2.0" --yes -o none
az containerapp auth update -g "$RG" -n "$APP" --enabled true --unauthenticated-client-action RedirectToLoginPage \
  --redirect-provider azureactivedirectory -o none
az containerapp ingress enable -g "$RG" -n "$APP" --type external --target-port 8000 --transport auto -o none
echo "Sign-in enabled: https://$(az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.fqdn -o tsv)"
