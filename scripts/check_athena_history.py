"""
Check the earliest available date in Athena inventory_record and trade_item_sale_agg_daily.
Run locally with valid AWS credentials.
"""
import os, sys, time
from pathlib import Path

# Load env
from dotenv import load_dotenv
for p in [Path(".env"), Path("../.env")]:
    if p.exists():
        load_dotenv(p); break

import boto3

aws_profile = os.getenv("AWS_PROFILE", "prod-ziyi.fang-406921510350")
session = boto3.Session(profile_name=aws_profile)
client  = session.client("athena", region_name="us-west-2")

workgroup = os.getenv("ATHENA_WORK_GROUP", "aiagent")
s3_out    = os.getenv("ATHENA_S3_STAGING", "s3://yami-athena-query-result/") + "aiagent/debug/"

SQL = """
SELECT
    'inventory_record' AS tbl,
    MIN(dt)            AS earliest,
    MAX(dt)            AS latest,
    COUNT(DISTINCT dt) AS distinct_dates,
    COUNT(DISTINCT item_number) AS distinct_skus
FROM yamibuy_inventory.inventory_record
WHERE warehouse_number = '002'
  AND (item_number LIKE '1%' OR item_number LIKE '5%')

UNION ALL

SELECT
    'trade_item_sale_agg_daily' AS tbl,
    MIN(CAST(day_id AS VARCHAR)) AS earliest,
    MAX(CAST(day_id AS VARCHAR)) AS latest,
    COUNT(DISTINCT day_id)       AS distinct_dates,
    COUNT(DISTINCT item_number)  AS distinct_skus
FROM dwb_trade.trade_item_sale_agg_daily
WHERE warehouse_number = '002'
  AND (item_number LIKE '1%' OR item_number LIKE '5%')
"""

print(f"Running Athena query (profile={aws_profile})...")
resp = client.start_query_execution(
    QueryString=SQL,
    QueryExecutionContext={"Database": "yamibuy_inventory"},
    WorkGroup=workgroup,
    ResultConfiguration={"OutputLocation": s3_out},
)
qid = resp["QueryExecutionId"]

for i in range(60):
    time.sleep(3)
    state = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
    print(f"  [{i+1}] {state}")
    if state == "SUCCEEDED":
        break
    if state in ("FAILED", "CANCELLED"):
        reason = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"].get("StateChangeReason","")
        print(f"FAILED: {reason}"); sys.exit(1)

rows = []
for page in client.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
    rows.extend(page["ResultSet"]["Rows"])

cols = [c["VarCharValue"] for c in rows[0]["Data"]]
print(f"\n{' | '.join(cols)}")
print("-" * 80)
for row in rows[1:]:
    vals = [c.get("VarCharValue","") for c in row["Data"]]
    print(" | ".join(vals))
