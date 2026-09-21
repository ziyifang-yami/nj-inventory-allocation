"""
NJ Inventory Allocation — 真实数据运行
Run: python run_allocation.py
输出: allocation_result.csv + allocation_summary.csv
"""
import time
import os
import boto3
import pymysql
import pandas as pd
from dotenv import load_dotenv
from pathlib import Path
from allocator import allocate, summarize

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

REGION    = "us-west-2"
WORKGROUP = os.getenv("ATHENA_WORK_GROUP", "ziyi.fang")
S3_OUT    = os.getenv("ATHENA_S3_STAGING",
                      "s3://aws-athena-query-results-us-west-2-654654218498/") + "nj-allocation/"

session = boto3.Session(profile_name="default")
client  = session.client("athena", region_name=REGION)


def run_athena(sql: str, database: str, label: str = "", timeout: int = 120) -> pd.DataFrame:
    print(f"  [Athena] {label}...", flush=True)
    resp = client.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=WORKGROUP,
        ResultConfiguration={"OutputLocation": S3_OUT},
    )
    qid = resp["QueryExecutionId"]
    for _ in range(timeout // 2):
        st = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
        if st == "SUCCEEDED":
            break
        if st in ("FAILED", "CANCELLED"):
            reason = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"Athena {st}: {reason}")
        time.sleep(2)
    else:
        raise TimeoutError(f"Athena timed out: {label}")

    rows_all = []
    paginator = client.get_paginator("get_query_results")
    for page in paginator.paginate(QueryExecutionId=qid):
        rows_all.extend(page["ResultSet"]["Rows"])

    if not rows_all:
        return pd.DataFrame()
    cols = [c["VarCharValue"] for c in rows_all[0]["Data"]]
    data = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows_all[1:]]
    df = pd.DataFrame(data, columns=cols)
    print(f"    → {len(df):,} rows", flush=True)
    return df


# ══════════════════════════════════════════════════════════
# Query 1: NJ Segment (latest day)
# ══════════════════════════════════════════════════════════
SQL_SEGMENT = """
SELECT item_number,
       nj_segment AS segment
FROM trade_item_sale_agg_daily
WHERE warehouse_number = '002'
  AND (item_number LIKE '1%' OR item_number LIKE '5%')
  AND day_id = (
      SELECT MAX(day_id)
      FROM trade_item_sale_agg_daily
      WHERE warehouse_number = '002'
  )
GROUP BY item_number, nj_segment
"""

# ══════════════════════════════════════════════════════════
# Query 2: Active Selling Days + avg_daily_orders (past 30d, NJ)
# ══════════════════════════════════════════════════════════
SQL_SALES = """
SELECT item_number,
       COUNT(DISTINCT day_id)                    AS active_days,
       CAST(SUM(orders) AS DOUBLE) / 30.0        AS avg_daily_orders
FROM trade_item_sale_agg_daily
WHERE warehouse_number = '002'
  AND orders > 0
  AND (item_number LIKE '1%' OR item_number LIKE '5%')
  AND day_id >= DATE_FORMAT(
        DATE_ADD('day', -30,
          DATE(CURRENT_TIMESTAMP AT TIME ZONE 'America/Los_Angeles')),
        '%Y-%m-%d')
GROUP BY item_number
"""

# ══════════════════════════════════════════════════════════
# Query 3: FC + SFC inventory — wh=002, location_type IN (3,4), 1P+FBY only
#
# 库位划分规则（NJ wh=002 实测）：
#   FC  = location_no NOT LIKE 'S%'（bin/stock 非S库位）
#   SFC = location_no LIKE 'S%'    （S开头货架区）
# 只看 sellable 库位：location_type IN (3, 4)
# 只看 1P（1开头）和 FBY（5开头）
# ══════════════════════════════════════════════════════════
def load_live_inventory() -> pd.DataFrame:
    """
    Load FC + SFC inventory from wh_inventory_transaction (wh=002 only).

    过滤条件：
      - location_type IN (3, 4)  — sellable bin 和 stock，排除收货/暂存/在途等
      - item_number LIKE '1%' OR LIKE '5%'  — 只看 1P 和 FBY
      - FC  = location_no NOT LIKE 'S%'
      - SFC = location_no LIKE 'S%'
    """
    print("  [MySQL] FC + SFC inventory (wh=002, location_type 3/4, 1P+FBY only)...", flush=True)
    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "rds.g3.yamibuy.net"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"),
        port=3306, charset="utf8mb4",
    )
    with conn.cursor() as cur:
        cur.execute("""
            SELECT wit.item_number,
                   SUM(CASE WHEN wit.location_no NOT LIKE 'S%%' THEN wit.quantity ELSE 0 END) AS fc_qty,
                   SUM(CASE WHEN wit.location_no     LIKE 'S%%' THEN wit.quantity ELSE 0 END) AS sfc_qty
            FROM yamibuy_wh.wh_inventory_transaction wit
            JOIN yamibuy_wh.wh_storage_location wsl
              ON wsl.location_no = wit.location_no
             AND wsl.warehouse_number = wit.warehouse_number
            WHERE wit.warehouse_number = '002'
              AND wit.quantity > 0
              AND wsl.location_type IN (3, 4)
              AND (wit.item_number LIKE '1%%' OR wit.item_number LIKE '5%%')
            GROUP BY wit.item_number
        """)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    conn.close()
    df = pd.DataFrame(rows, columns=cols)
    for col in ["fc_qty", "sfc_qty"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df = df[(df["fc_qty"] > 0) | (df["sfc_qty"] > 0)].copy()
    print(f"    → {len(df):,} SKUs (1P+FBY, location_type 3/4)", flush=True)
    fc_total  = df["fc_qty"].sum()
    sfc_total = df["sfc_qty"].sum()
    print(f"    → FC  total: {fc_total:>10,.0f} units (non-S, type 3/4)", flush=True)
    print(f"    → SFC total: {sfc_total:>10,.0f} units (S-prefix, type 3/4)", flush=True)
    return df

# ──────────────────────────────────────────────────────────
print("=" * 60)
print("NJ Inventory Allocation — Loading real data")
print("=" * 60)

df_seg = run_athena(SQL_SEGMENT, "dwb_trade",  "Segment (NJ latest)")
df_sal = run_athena(SQL_SALES,   "dwb_trade",  "Active days + avg_daily_orders (30d)")
df_inv = load_live_inventory()

# ── Cast numeric columns ────────────────────────────────
for col in ["active_days", "avg_daily_orders"]:
    df_sal[col] = pd.to_numeric(df_sal[col], errors="coerce").fillna(0)

# ── Merge ───────────────────────────────────────────────
print("\nMerging datasets...", flush=True)
df = df_inv.merge(df_seg, on="item_number", how="left")
df = df.merge(df_sal, on="item_number", how="left")

df["segment"]          = df["segment"].fillna("TBD")
df["active_days"]      = df["active_days"].fillna(0)
df["avg_daily_orders"] = df["avg_daily_orders"].fillna(0)

# Keep only SKUs with any inventory (FC or SFC > 0)
df = df[(df["fc_qty"] > 0) | (df["sfc_qty"] > 0)].copy()
print(f"  → {len(df):,} SKUs with inventory", flush=True)

# ── Run allocator ───────────────────────────────────────
print("\nRunning allocation engine...", flush=True)
result  = allocate(df)
summary = summarize(result)

# ── Print summary ───────────────────────────────────────
print("\n" + "=" * 60)
print("SUMMARY BY SEGMENT + ACTION")
print("=" * 60)
print(summary.to_string(index=False))

# Action totals
print("\n── Action totals ──")
action_total = (
    result.groupby("action")
    .agg(sku_count=("item_number", "count"),
         total_transfer_qty=("transfer_qty", lambda x: x.abs().sum()))
    .reset_index()
)
print(action_total.to_string(index=False))

# ── Save outputs ────────────────────────────────────────
result.to_csv("allocation_result.csv", index=False)
summary.to_csv("allocation_summary.csv", index=False)
print(f"\n✅ Saved: allocation_result.csv ({len(result):,} rows)")
print(f"✅ Saved: allocation_summary.csv ({len(summary):,} rows)")
