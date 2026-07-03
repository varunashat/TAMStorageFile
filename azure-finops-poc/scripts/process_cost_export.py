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
OUTPUT = Path("azure-finops-poc/data/azure_cost.json")

# Format: key|Display Name|blob-prefix, key|Display Name|blob-prefix
CLIENT_CONFIG = os.environ.get(
    "AZURE_CLIENT_CONFIG",
    "etihad|Etihad Airways|etihad-dashboard/,difc|DIFC|difc/,national-bonds|National Bonds|national-bonds/",
)

COST_COLUMNS = ["CostInBillingCurrency", "PreTaxCost", "Cost", "costInBillingCurrency"]
DATE_COLUMNS = ["Date", "UsageDate", "date"]


def parse_client_config(raw):
    clients = []
    for item in raw.split(","):
        parts = [p.strip() for p in item.split("|")]
        if len(parts) != 3:
            continue
        key, name, prefix = parts
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        clients.append({"key": key, "name": name, "prefix": prefix})
    if not clients:
        raise RuntimeError("No valid clients configured in AZURE_CLIENT_CONFIG")
    return clients


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


def month_from_date(value):
    date_text = normalize_date(value)
    if date_text and len(date_text) >= 7 and date_text[4:5] == "-":
        return date_text[:7]
    return "Unknown"


def latest_csv_blob(container_client, prefix):
    blobs = [
        b for b in container_client.list_blobs(name_starts_with=prefix)
        if b.name.lower().endswith((".csv", ".csv.gz"))
    ]
    if not blobs:
        return None
    return sorted(blobs, key=lambda b: b.last_modified or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[0]


def read_csv_rows(container_client, blob_name):
    raw = container_client.download_blob(blob_name).readall()
    if blob_name.lower().endswith(".gz"):
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def add_cost(bucket, key, cost):
    if key:
        bucket[key] += cost


def empty_accumulator():
    return {
        "total_cost": 0.0,
        "subscriptions": set(),
        "resource_groups": set(),
        "resources": set(),
        "by_subscription": defaultdict(float),
        "by_service": defaultdict(float),
        "by_rg": defaultdict(float),
        "by_region": defaultdict(float),
        "by_day": defaultdict(float),
        "by_month": defaultdict(float),
        "by_client": defaultdict(float),
        "top_resources": defaultdict(lambda: {
            "cost": 0.0,
            "clientKey": "",
            "clientName": "",
            "month": "",
            "resourceName": "",
            "resourceId": "",
            "subscriptionName": "",
            "resourceGroup": "",
            "service": "",
            "region": "",
        }),
    }


def ingest_rows(acc, rows, client):
    for row in rows:
        cost = parse_float(get_value(row, COST_COLUMNS, "0"))
        acc["total_cost"] += cost

        subscription = get_value(row, ["SubscriptionName", "subscriptionName"], "Unassigned")
        subscription_id = get_value(row, ["SubscriptionId", "subscriptionId"], "")
        rg = get_value(row, ["ResourceGroup", "resourceGroupName", "ResourceGroupName"], "Unassigned")
        service = get_value(row, ["MeterCategory", "ServiceFamily", "ServiceName", "ConsumedService"], "Unassigned")
        region = get_value(row, ["ResourceLocation", "ResourceLocationNormalized", "location"], "Unassigned")
        resource_id = get_value(row, ["ResourceId", "resourceId", "InstanceId"], "")
        resource_name = get_value(row, ["ResourceName", "resourceName", "InstanceName"], resource_id.split("/")[-1] if resource_id else "Unknown")
        date = normalize_date(get_value(row, DATE_COLUMNS, ""))
        month = month_from_date(date)

        if subscription_id:
            acc["subscriptions"].add(f"{client['key']}|{subscription_id}")
        elif subscription:
            acc["subscriptions"].add(f"{client['key']}|{subscription}")
        if rg:
            acc["resource_groups"].add(f"{client['key']}|{rg}")
        if resource_id:
            acc["resources"].add(f"{client['key']}|{resource_id}")

        add_cost(acc["by_client"], client["name"], cost)
        add_cost(acc["by_subscription"], subscription, cost)
        add_cost(acc["by_service"], service, cost)
        add_cost(acc["by_rg"], rg, cost)
        add_cost(acc["by_region"], region, cost)
        add_cost(acc["by_day"], date, cost)
        add_cost(acc["by_month"], month, cost)

        resource_key = f"{client['key']}|{month}|{resource_id or subscription + '|' + rg + '|' + resource_name}"
        item = acc["top_resources"][resource_key]
        item.update({
            "clientKey": client["key"],
            "clientName": client["name"],
            "month": month,
            "resourceName": resource_name,
            "resourceId": resource_id,
            "subscriptionName": subscription,
            "resourceGroup": rg,
            "service": service,
            "region": region,
        })
        item["cost"] += cost


def top_list(bucket, limit=25):
    return [{"name": k, "cost": round(v, 2)} for k, v in sorted(bucket.items(), key=lambda x: x[1], reverse=True)[:limit]]


def finalize(acc):
    month_list = sorted([m for m in acc["by_month"].keys() if m])
    return {
        "summary": {
            "totalCost": round(acc["total_cost"], 2),
            "subscriptionCount": len(acc["subscriptions"]),
            "resourceGroupCount": len(acc["resource_groups"]),
            "resourceCount": len(acc["resources"]),
        },
        "monthList": month_list,
        "costByClient": top_list(acc["by_client"]),
        "costByMonth": top_list(acc["by_month"], 36),
        "costBySubscription": top_list(acc["by_subscription"]),
        "costByService": top_list(acc["by_service"]),
        "costByResourceGroup": top_list(acc["by_rg"]),
        "costByRegion": top_list(acc["by_region"]),
        "dailyTrend": [{"date": k, "cost": round(v, 2)} for k, v in sorted(acc["by_day"].items())],
        "topResources": sorted(
            [{**v, "cost": round(v["cost"], 2)} for v in acc["top_resources"].values()],
            key=lambda x: x["cost"],
            reverse=True,
        )[:25],
    }


def main():
    account_url = f"https://{STORAGE_ACCOUNT}.blob.core.windows.net"
    container_client = ContainerClient(account_url=account_url, container_name=CONTAINER, credential=SAS_TOKEN)

    clients_cfg = parse_client_config(CLIENT_CONFIG)
    overall_acc = empty_accumulator()
    overall_month_accs = defaultdict(empty_accumulator)
    clients_output = {}
    source_blobs = []

    for client in clients_cfg:
        blob = latest_csv_blob(container_client, client["prefix"])
        if not blob:
            clients_output[client["key"]] = {
                "key": client["key"],
                "name": client["name"],
                "prefix": client["prefix"],
                "status": "No CSV found",
                "months": {},
                **finalize(empty_accumulator()),
            }
            continue

        rows = read_csv_rows(container_client, blob.name)
        client_acc = empty_accumulator()
        client_month_accs = defaultdict(empty_accumulator)

        ingest_rows(client_acc, rows, client)
        ingest_rows(overall_acc, rows, client)
        for row in rows:
            month = month_from_date(get_value(row, DATE_COLUMNS, ""))
            ingest_rows(client_month_accs[month], [row], client)
            ingest_rows(overall_month_accs[month], [row], client)

        source_blobs.append({"clientKey": client["key"], "clientName": client["name"], "blob": blob.name})

        clients_output[client["key"]] = {
            "key": client["key"],
            "name": client["name"],
            "prefix": client["prefix"],
            "status": "Loaded",
            "sourceBlob": blob.name,
            "months": {m: finalize(a) for m, a in sorted(client_month_accs.items())},
            **finalize(client_acc),
        }

    overall = finalize(overall_acc)
    overall["months"] = {m: finalize(a) for m, a in sorted(overall_month_accs.items())}
    all_months = sorted(set(overall.get("monthList", [])))
    output = {
        "version": "v3-multi-client-month-filter",
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "sourceBlob": ", ".join([x["blob"] for x in source_blobs]),
        "sourceBlobs": source_blobs,
        "clientList": [{"key": c["key"], "name": c["name"], "prefix": c["prefix"]} for c in clients_cfg],
        "monthList": all_months,
        "clients": clients_output,
        "overall": overall,
        # Backward-compatible fields for the existing dashboard. These now represent All Customers.
        **overall,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT} with {len(source_blobs)} client export(s) and {len(all_months)} month(s)")


if __name__ == "__main__":
    main()
