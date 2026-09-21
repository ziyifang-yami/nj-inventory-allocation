"""
独立表 schema 探索脚本 — 不依赖 data_loader.py
Run: python check_tables.py
"""
import time
import os
import boto3
import pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

REGION    = os.getenv("ATHENA_REGION", "us-west-2")
WORKGROUP = os.getenv("ATHENA_WORK_GROUP", "ziyi.fang")
S3_OUT    = os.getenv("ATHENA_S3_STAGING", "s3://aws-athena-query-results-us-west-2-654654218498/") + "schema-check/"

session = boto3.Session(profile_name="default")
client  = session.client("athena", region_name=REGION)


def run(sql: str, database: str = "default", timeout: int = 45) -> pd.DataFrame:
    resp = client.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=WORKGROUP,
        ResultConfiguration={"OutputLocation": S3_OUT},
    )
    qid = resp["QueryExecutionId"]
    for _ in range(timeout // 2):
        state = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"{state}")
        time.sleep(2)
    else:
        raise TimeoutError()
    rows = client.get_query_results(QueryExecutionId=qid, MaxResults=10)["ResultSet"]["Rows"]
    if not rows:
        return pd.DataFrame()
    cols = [c["VarCharValue"] for c in rows[0]["Data"]]
    data = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows[1:]]
    return pd.DataFrame(data, columns=cols)


TABLES = [
    ("yamibuy_bigdata", "adf_product_segment"),
    ("yamibuy_bigdata", "po_product_segment"),
    ("yamibuy_po",      "po_product_segment"),
    ("dm_trade",        "segment_for_adf"),
    ("yamibuy_inventory", "inventory_record"),
    ("sum_trade",       "trade_goods_sales_and_inv"),
    ("dwb_trade",       "trade_item_sale_agg_daily"),
    ("dwb_trade_iris",  "inventory_record"),
]

for db, tbl in TABLES:
    print(f"\n{'='*60}")
    print(f"  {db}.{tbl}")
    print('='*60)
    try:
        df = run(f"SELECT * FROM {tbl} LIMIT 2", database=db)
        print(f"Columns ({len(df.columns)}): {list(df.columns)}")
        print()
        for _, row in df.iterrows():
            for k, v in row.items():
                if v:
                    print(f"  {k}: {v}")
            print("  ---")
    except Exception as e:
        print(f"  ERROR: {e}")
