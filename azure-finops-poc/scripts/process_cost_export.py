import csv
import gzip
import io
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from azure.storage.blob import ContainerClient

STORAGE_ACCOUNT = os.environ["AZURE_STORAGE_ACCOUNT"]
CONTAINER = os.environ["AZURE_STORAGE_CONTAINER"]
SAS_TOKEN = os.environ["AZURE_STORAGE_SAS_TOKEN"].lstrip("?")
PREFIX = os.environ.get("AZURE_BLOB_PREFIX", "etihad-dashboard/")
OUTPUT = Path("azure-finops-poc/data/azure_cost.json")

COST_COLUMNS = ["CostInBillingCurrency", "PreTaxCost", "Cost", "costInBillingCurrency"]
DATE_COLUMNS = ["Date", "UsageDate", "date"]


def get_value(row, names, default=""):
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return default


def parse_float(value):
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return 0.0


def normalize_date(value):
    if not value:
        return "Unknown"
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except Exception:
            pass
    return text[:10]


def latest_csv_blob(container_client):
    blobs = [b for b in container_client.list_blobs(name_starts_with=PREFIX) if b.name.lower().endswith((".csv", ".csv.gz"))]
    if not blobs:
        raise RuntimeError(f"No CSV files found under prefix: {PREFIX}")
    return sorted(blobs, key=lambda b: b.last_modified or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[0]


def read_csv_rows(container_client, blob_name):
    raw = container_client.download_blob(blob_name).readall()
    if blob_name.lower().endswith(".gz"):
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8-sig", errors="replace")
    return csv.DictReader(io.StringIO(text))


def add_cost(bucket, key, cost):
    if key:
        bucket[key] += cost


def main():
    account_url = f"https://{STORAGE_ACCOUNT}.blob.core.windows.net"
    container_client = ContainerClient(account_url=account_url, container_name=CONTAINER, credential=SAS_TOKEN)

    blob = latest_csv_blob(container_client)
    rows = read_csv_rows(container_client, blob.name)

    total_cost = 0.0
    subscriptions = set()
    resource_groups = set()
    resources = set()
    by_subscription = defaultdict(float)
    by_service = defaultdict(float)
    by_rg = defaultdict(float)
    by_region = defaultdict(float)
    by_day = defaultdict(float)
    top_resources = defaultdict(lambda: {"cost": 0.0, "resourceName": "", "resourceId": "", "subscriptionName": "", "resourceGroup": "", "service": ""})

    for row in rows:
        cost = parse_float(get_value(row, COST_COLUMNS, "0"))
        total_cost += cost

        subscription = get_value(row, ["SubscriptionName", "subscriptionName"], "Unassigned")
        subscription_id = get_value(row, ["SubscriptionId", "subscriptionId"], "")
        rg = get_value(row, ["ResourceGroup", "resourceGroupName", "ResourceGroupName"], "Unassigned")
        service = get_value(row, ["MeterCategory", "ServiceFamily", "ServiceName", "ConsumedService"], "Unassigned")
        region = get_value(row, ["ResourceLocation", "ResourceLocationNormalized", "location"], "Unassigned")
        resource_id = get_value(row, ["ResourceId", "resourceId", "InstanceId"], "")
        resource_name = get_value(row, ["ResourceName", "resourceName", "InstanceName"], resource_id.split("/")[-1] if resource_id else "Unknown")
        date = normalize_date(get_value(row, DATE_COLUMNS, ""))

        if subscription_id:
            subscriptions.add(subscription_id)
        elif subscription:
            subscriptions.add(subscription)
        if rg:
            resource_groups.add(rg)
        if resource_id:
            resources.add(resource_id)

        add_cost(by_subscription, subscription, cost)
        add_cost(by_service, service, cost)
        add_cost(by_rg, rg, cost)
        add_cost(by_region, region, cost)
        add_cost(by_day, date, cost)

        resource_key = resource_id or f"{subscription}|{rg}|{resource_name}"
        item = top_resources[resource_key]
        item.update({"resourceName": resource_name, "resourceId": resource_id, "subscriptionName": subscription, "resourceGroup": rg, "service": service})
        item["cost"] += cost

    def top_list(bucket, limit=20):
        return [{"name": k, "cost": round(v, 2)} for k, v in sorted(bucket.items(), key=lambda x: x[1], reverse=True)[:limit]]

    output = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "sourceBlob": blob.name,
        "summary": {
            "totalCost": round(total_cost, 2),
            "subscriptionCount": len(subscriptions),
            "resourceGroupCount": len(resource_groups),
            "resourceCount": len(resources),
        },
        "costBySubscription": top_list(by_subscription),
        "costByService": top_list(by_service),
        "costByResourceGroup": top_list(by_rg),
        "costByRegion": top_list(by_region),
        "dailyTrend": [{"date": k, "cost": round(v, 2)} for k, v in sorted(by_day.items())],
        "topResources": sorted(top_resources.values(), key=lambda x: x["cost"], reverse=True)[:20],
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT} from {blob.name}")


if __name__ == "__main__":
    main()
