"""
shared/athena.py — Athena 查询工具
====================================
统一的 Athena 连接和查询函数，所有方案共享。
"""

import os
import time
import boto3
import pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

REGION    = os.getenv("ATHENA_REGION", "us-west-2")
WORKGROUP = os.getenv("ATHENA_WORK_GROUP", "ziyi.fang")
S3_BASE   = os.getenv("ATHENA_S3_STAGING",
                      "s3://aws-athena-query-results-us-west-2-654654218498/")

_client = None


def _get_client():
    global _client
    if _client is None:
        session = boto3.Session(profile_name=os.getenv("AWS_PROFILE", "default"))
        _client = session.client("athena", region_name=REGION)
    return _client


def run_athena(sql: str, database: str, label: str = "",
               s3_prefix: str = "nj-alloc/", timeout: int = 120) -> pd.DataFrame:
    """
    Execute a SQL query on Athena and return a DataFrame.

    Parameters
    ----------
    sql       : Presto/Trino SQL string
    database  : Athena database name
    label     : Human-readable label for progress output
    s3_prefix : Sub-path under S3_BASE for query results
    timeout   : Seconds to wait before raising TimeoutError
    """
    if label:
        print(f"  [Athena] {label}...", flush=True)

    client = _get_client()
    resp = client.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=WORKGROUP,
        ResultConfiguration={"OutputLocation": S3_BASE + s3_prefix},
    )
    qid = resp["QueryExecutionId"]

    for _ in range(timeout // 2):
        st = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
        if st == "SUCCEEDED":
            break
        if st in ("FAILED", "CANCELLED"):
            reason = (client.get_query_execution(QueryExecutionId=qid)
                      ["QueryExecution"]["Status"].get("StateChangeReason", ""))
            raise RuntimeError(f"Athena {st}: {reason}")
        time.sleep(2)
    else:
        raise TimeoutError(f"Athena query timed out after {timeout}s: {label}")

    rows_all = []
    paginator = client.get_paginator("get_query_results")
    for page in paginator.paginate(QueryExecutionId=qid):
        rows_all.extend(page["ResultSet"]["Rows"])

    if not rows_all:
        return pd.DataFrame()

    cols = [c["VarCharValue"] for c in rows_all[0]["Data"]]
    data = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows_all[1:]]
    df = pd.DataFrame(data, columns=cols)

    if label:
        print(f"    → {len(df):,} rows", flush=True)
    return df


def load_active_selling_days(warehouse: str = "002",
                              lookback_days: int = 30) -> pd.DataFrame:
    """
    Active selling days + avg daily orders per SKU (past N days).

    Returns
    -------
    DataFrame: item_number, active_days, avg_daily_orders
    """
    sql = f"""
    SELECT item_number,
           COUNT(DISTINCT day_id)             AS active_days,
           CAST(SUM(orders) AS DOUBLE) / 30.0 AS avg_daily_orders
    FROM trade_item_sale_agg_daily
    WHERE warehouse_number = '{warehouse}'
      AND orders > 0
      AND day_id >= DATE_FORMAT(
            DATE_ADD('day', -{lookback_days},
              DATE(CURRENT_TIMESTAMP AT TIME ZONE 'America/Los_Angeles')),
            '%Y-%m-%d')
    GROUP BY item_number
    """
    df = run_athena(sql, "dwb_trade",
                    label=f"Active days + avg_daily_orders ({lookback_days}d, wh={warehouse})")
    df["active_days"]      = pd.to_numeric(df["active_days"],      errors="coerce").fillna(0)
    df["avg_daily_orders"] = pd.to_numeric(df["avg_daily_orders"], errors="coerce").fillna(0)
    return df
