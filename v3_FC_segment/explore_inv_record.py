"""探索 inventory_record 的 dt 字段范围和格式"""
import os, time, boto3, pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

REGION    = "us-west-2"
WORKGROUP = os.getenv("ATHENA_WORK_GROUP", "ziyi.fang")
S3_OUT    = os.getenv("ATHENA_S3_STAGING",
                      "s3://aws-athena-query-results-us-west-2-654654218498/") + "nj-v3-explore/"

session = boto3.Session(profile_name="default")
athenac = session.client("athena", region_name=REGION)

def run_athena(sql, database, label="", timeout=60):
    print(f"  [Athena] {label}...", flush=True)
    resp = athenac.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=WORKGROUP,
        ResultConfiguration={"OutputLocation": S3_OUT},
    )
    qid = resp["QueryExecutionId"]
    for _ in range(timeout // 2):
        st = athenac.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
        if st == "SUCCEEDED": break
        if st in ("FAILED", "CANCELLED"):
            raise RuntimeError(athenac.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"].get("StateChangeReason",""))
        time.sleep(2)
    rows_all = []
    for page in athenac.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
        rows_all.extend(page["ResultSet"]["Rows"])
    cols = [c["VarCharValue"] for c in rows_all[0]["Data"]]
    data = [[c.get("VarCharValue","") for c in r["Data"]] for r in rows_all[1:]]
    df = pd.DataFrame(data, columns=cols)
    print(df.to_string(index=False)); return df

# 1. dt 范围和格式
run_athena("""
SELECT MIN(dt) AS min_dt, MAX(dt) AS max_dt, COUNT(DISTINCT dt) AS days
FROM inventory_record
WHERE warehouse_number = '002'
""", "yamibuy_inventory", "inventory_record dt range (wh=002)")

# 2. 某个 SKU 样本 — 有没有每天一条
run_athena("""
SELECT dt, available_qty, allocated_qty, reserved_qty
FROM inventory_record
WHERE warehouse_number = '002'
  AND item_number = '1016005261'
ORDER BY dt DESC
LIMIT 10
""", "yamibuy_inventory", "sample SKU daily inventory")
