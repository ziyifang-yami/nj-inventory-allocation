"""
探索关键表 schema — 用 boto3 直接执行，加 LIMIT 避免大表超时
Run: python explore_schema.py
"""
import time
import warnings
import boto3
import pandas as pd
import io
warnings.filterwarnings("ignore")

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

import os

REGION    = os.getenv("ATHENA_REGION", "us-west-2")
WORKGROUP = os.getenv("ATHENA_WORK_GROUP", "ziyi.fang")
S3_OUT    = os.getenv("ATHENA_S3_STAGING", "s3://aws-athena-query-results-us-west-2-654654218498/")

session = boto3.Session(profile_name="default")
client  = session.client("athena", region_name=REGION)
s3      = session.client("s3")


def run_query(sql: str, database: str = "default", timeout: int = 60) -> pd.DataFrame:
    """Execute Athena query and return DataFrame."""
    resp = client.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=WORKGROUP,
        ResultConfiguration={"OutputLocation": S3_OUT + "explore/"},
    )
    qid = resp["QueryExecutionId"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get_query_execution(QueryExecutionId=qid)
        state  = status["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            reason = status["QueryExecution"]["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"Query {state}: {reason}")
        time.sleep(2)
    else:
        raise TimeoutError(f"Query timed out after {timeout}s")

    # Get result
    result = client.get_query_results(QueryExecutionId=qid, MaxResults=10)
    rows   = result["ResultSet"]["Rows"]
    if not rows:
        return pd.DataFrame()
    headers = [c["VarCharValue"] for c in rows[0]["Data"]]
    data    = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows[1:]]
    return pd.DataFrame(data, columns=headers)


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
        df = run_query(f"SELECT * FROM {tbl} LIMIT 2", database=db, timeout=45)
        print(f"Columns ({len(df.columns)}): {list(df.columns)}")
        print()
        for _, row in df.iterrows():
            for k, v in row.items():
                print(f"  {k}: {v}")
            print("  ---")
    except Exception as e:
        print(f"  ERROR: {e}")
