"""
V3 FC Segment — NJ 全量分级分析
================================
分级规则（简化版）：
  TOP    : rolling_active_order_days >= 21 AND rolling_order_lines >= 100
  TAIL   : rolling_order_lines < 8（含无销售记录，暂按 TAIL 处理）
  MIDDLE : 其余

Sales 数据口径：
  rolling 30 有效销售日 = 取该 SKU 历史上最近 30 个有销售的天

Output:
  results/segment_summary_<ts>.csv
  results/segment_sku_detail_<ts>.csv
"""

import os, sys, time
import boto3
import pymysql
import pandas as pd
import numpy as np
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

OUT_DIR = Path(__file__).parent / "results"
OUT_DIR.mkdir(exist_ok=True)

REGION    = "us-west-2"
WORKGROUP = os.getenv("ATHENA_WORK_GROUP", "ziyi.fang")
S3_OUT    = os.getenv("ATHENA_S3_STAGING",
                      "s3://yami-athena-query-result/") + "ziyi.fang/nj-v3-segment/"

session = boto3.Session(profile_name="default")
athenac = session.client("athena", region_name=REGION)

def run_athena(sql, database, label="", timeout=180):
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
        if st == "SUCCEEDED":
            break
        if st in ("FAILED", "CANCELLED"):
            reason = athenac.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"Athena {st}: {reason}")
        time.sleep(2)
    else:
        raise TimeoutError(f"Athena timed out: {label}")
    rows_all = []
    paginator = athenac.get_paginator("get_query_results")
    for page in paginator.paginate(QueryExecutionId=qid):
        rows_all.extend(page["ResultSet"]["Rows"])
    if not rows_all:
        return pd.DataFrame()
    cols = [c["VarCharValue"] for c in rows_all[0]["Data"]]
    data = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows_all[1:]]
    df = pd.DataFrame(data, columns=cols)
    print(f"    → {len(df):,} rows", flush=True)
    return df

def run_mysql(sql, label=""):
    print(f"  [MySQL] {label}...", flush=True)
    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "rds.g3.yamibuy.net"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"),
        port=3306, charset="utf8mb4",
    )
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    conn.close()
    df = pd.DataFrame(rows, columns=cols)
    print(f"    → {len(df):,} rows", flush=True)
    return df

print("=" * 65)
print("V3 FC Segment — NJ Full Segment Analysis")
print(f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 65)

# ══════════════════════════════════════════════════════════════
# 1. Rolling 30 有库存的天，统计 AOD + OL (Athena)
#    逻辑：
#    - 从 inventory_record 取 wh=002 每个 SKU available_qty > 0 的日期
#    - 取最近 30 个有库存的日期（ROW_NUMBER DESC）
#    - 在这 30 天里 LEFT JOIN trade_item_sale_agg_daily 统计 AOD 和 OL
# ══════════════════════════════════════════════════════════════
SQL_SALES = """
WITH inv_days AS (
    SELECT
        item_number,
        dt AS inv_date,
        ROW_NUMBER() OVER (PARTITION BY item_number ORDER BY dt DESC) AS rn
    FROM yamibuy_inventory.inventory_record
    WHERE warehouse_number = '002'
      AND virtual_qty = 0
      AND available_qty > 0
      AND (item_number LIKE '1%' OR item_number LIKE '5%')
),
inv_30 AS (
    SELECT item_number, inv_date
    FROM inv_days
    WHERE rn <= 30
),
sales AS (
    SELECT item_number, day_id,
           SUM(orders) AS daily_orders,
           SUM(sales)  AS daily_sales
    FROM dwb_trade.trade_item_sale_agg_daily
    WHERE warehouse_number = '002'
      AND orders > 0
      AND (item_number LIKE '1%' OR item_number LIKE '5%')
    GROUP BY item_number, day_id
)
SELECT
    i.item_number,
    COUNT(DISTINCT i.inv_date)                    AS inv_days_30,
    COUNT(DISTINCT s.day_id)                      AS active_order_days,
    COALESCE(CAST(SUM(s.daily_orders) AS DOUBLE), 0)        AS order_lines_30d,
    COALESCE(CAST(SUM(s.daily_orders) AS DOUBLE) / 30.0, 0) AS avg_daily_orders,
    COALESCE(SUM(s.daily_sales), 0)               AS sales_units_30d
FROM inv_30 i
LEFT JOIN sales s
    ON s.item_number = i.item_number
   AND s.day_id = i.inv_date
GROUP BY i.item_number
"""
df_sales = run_athena(SQL_SALES, "yamibuy_inventory", "Rolling 30 inv-days sales (NJ)", timeout=300)
for col in ["active_order_days", "order_lines_30d", "avg_daily_orders", "sales_units_30d", "inv_days_30"]:
    df_sales[col] = pd.to_numeric(df_sales[col], errors="coerce").fillna(0)

# ══════════════════════════════════════════════════════════════
# 2. Unit Volume (MySQL)
# ══════════════════════════════════════════════════════════════
SQL_VOLUME = """
SELECT i.item_number, wwg.volume AS unit_volume_cbm
FROM yamibuy_im.im_item i
JOIN yamibuy_master.xysc_wearhouse_goods wwg ON i.goods_id = wwg.goods_id
WHERE (i.item_number LIKE '1%' OR i.item_number LIKE '5%')
  AND wwg.volume > 0
"""
df_vol = run_mysql(SQL_VOLUME, "Unit volume (CBM)")
df_vol["unit_volume_cbm"] = pd.to_numeric(df_vol["unit_volume_cbm"], errors="coerce").fillna(0)

# ══════════════════════════════════════════════════════════════
# 3. FC + SFC Inventory (MySQL, location_type 3/4, wh=002)
# ══════════════════════════════════════════════════════════════
print("  [MySQL] FC + SFC inventory (wh=002, location_type 3/4, 1P+FBY)...", flush=True)
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
df_inv = pd.DataFrame(rows, columns=cols)
for col in ["fc_qty", "sfc_qty"]:
    df_inv[col] = pd.to_numeric(df_inv[col], errors="coerce").fillna(0)
df_inv = df_inv[(df_inv["fc_qty"] > 0) | (df_inv["sfc_qty"] > 0)].copy()
print(f"    → {len(df_inv):,} SKUs", flush=True)

# ══════════════════════════════════════════════════════════════
# 4. Merge
# ══════════════════════════════════════════════════════════════
print("\nMerging datasets...", flush=True)
df = df_inv.merge(df_sales, on="item_number", how="left")
df = df.merge(df_vol,       on="item_number", how="left")

df["active_order_days"] = df["active_order_days"].fillna(0)
df["order_lines_30d"]   = df["order_lines_30d"].fillna(0)
df["avg_daily_orders"]  = df["avg_daily_orders"].fillna(0)
df["sales_units_30d"]   = df["sales_units_30d"].fillna(0)
df["inv_days_30"]       = df["inv_days_30"].fillna(0)
df["unit_volume_cbm"]   = df["unit_volume_cbm"].fillna(0)

# ══════════════════════════════════════════════════════════════
# 5. Segment Classification
#
#   TOP    : active_order_days >= 21 AND order_lines_30d >= 100
#   TAIL   : order_lines_30d < 8 (含无销售记录)
#   MIDDLE : 其余
# ══════════════════════════════════════════════════════════════
def classify(row):
    aod = row["active_order_days"]
    ol  = row["order_lines_30d"]
    if aod >= 21 and ol >= 100:
        return "TOP"
    if ol < 8:
        return "TAIL"
    return "MIDDLE"

df["segment"] = df.apply(classify, axis=1)

# Derived metrics
df["avg_daily_sales"]  = df["sales_units_30d"] / 30
df["daily_sales_cbm"]  = df["avg_daily_sales"] * df["unit_volume_cbm"]
df["fc_volume_cbm"]    = df["fc_qty"]  * df["unit_volume_cbm"]
df["sfc_volume_cbm"]   = df["sfc_qty"] * df["unit_volume_cbm"]

print(f"  → Total SKUs: {len(df):,}")
print(f"  → Segment distribution:")
print(df["segment"].value_counts().to_string())

# ══════════════════════════════════════════════════════════════
# 6. Summary
# ══════════════════════════════════════════════════════════════
totals = {
    "sku":      len(df),
    "ol":       df["order_lines_30d"].sum(),
    "units":    df["sales_units_30d"].sum(),
    "cbm":      df["daily_sales_cbm"].sum(),
}

summary_rows = []
for seg in ["TOP", "MIDDLE", "TAIL"]:
    sub = df[df["segment"] == seg]
    summary_rows.append({
        "segment":             seg,
        "sku_count":           len(sub),
        "sku_pct":             round(len(sub) / totals["sku"] * 100, 1),
        "order_lines_30d":     int(sub["order_lines_30d"].sum()),
        "order_lines_pct":     round(sub["order_lines_30d"].sum() / totals["ol"] * 100, 1) if totals["ol"] else 0,
        "sales_units_30d":     int(sub["sales_units_30d"].sum()),
        "sales_units_pct":     round(sub["sales_units_30d"].sum() / totals["units"] * 100, 1) if totals["units"] else 0,
        "daily_sales_cbm":     round(sub["daily_sales_cbm"].sum(), 1),
        "daily_sales_cbm_pct": round(sub["daily_sales_cbm"].sum() / totals["cbm"] * 100, 1) if totals["cbm"] else 0,
        "fc_qty":              int(sub["fc_qty"].sum()),
        "fc_volume_cbm":       round(sub["fc_volume_cbm"].sum(), 1),
        "sfc_qty":             int(sub["sfc_qty"].sum()),
        "sfc_volume_cbm":      round(sub["sfc_volume_cbm"].sum(), 1),
        "avg_aod":             round(sub["active_order_days"].mean(), 1),
        "avg_ol_30d":          round(sub["order_lines_30d"].mean(), 1),
        "avg_daily_sales":     round(sub["avg_daily_sales"].mean(), 2),
    })

summary = pd.DataFrame(summary_rows)

print("\n" + "=" * 65)
print("SEGMENT SUMMARY — NJ (wh=002, 1P+FBY, location_type 3/4)")
print("=" * 65)
for _, r in summary.iterrows():
    print(f"\n[{r['segment']}]")
    print(f"  SKU:             {r['sku_count']:>7,}  ({r['sku_pct']}%)")
    print(f"  30D Order Lines: {r['order_lines_30d']:>10,}  ({r['order_lines_pct']}%)")
    print(f"  30D Sales Units: {r['sales_units_30d']:>10,}  ({r['sales_units_pct']}%)")
    print(f"  Daily Sales CBM: {r['daily_sales_cbm']:>10,.1f}  ({r['daily_sales_cbm_pct']}%)")
    print(f"  FC Qty:          {r['fc_qty']:>10,}")
    print(f"  FC Volume (CBM): {r['fc_volume_cbm']:>10,.1f}")
    print(f"  SFC Qty:         {r['sfc_qty']:>10,}")
    print(f"  SFC Volume(CBM): {r['sfc_volume_cbm']:>10,.1f}")
    print(f"  Avg AOD:         {r['avg_aod']:>7.1f} days")
    print(f"  Avg OL/30d:      {r['avg_ol_30d']:>7.1f}")
    print(f"  Avg Daily Sales: {r['avg_daily_sales']:>7.2f} units/day")

# ══════════════════════════════════════════════════════════════
# 7. Save
# ══════════════════════════════════════════════════════════════
ts = datetime.now().strftime("%Y%m%d_%H%M")
detail_cols = [
    "item_number", "segment",
    "inv_days_30", "active_order_days", "order_lines_30d", "avg_daily_orders",
    "sales_units_30d", "avg_daily_sales",
    "unit_volume_cbm", "daily_sales_cbm",
    "fc_qty", "sfc_qty", "fc_volume_cbm", "sfc_volume_cbm",
]
df[detail_cols].sort_values(["segment", "order_lines_30d"], ascending=[True, False])\
    .to_csv(OUT_DIR / f"segment_sku_detail_{ts}.csv", index=False)
summary.to_csv(OUT_DIR / f"segment_summary_{ts}.csv", index=False)

print(f"\n✅ Saved:")
print(f"   {OUT_DIR}/segment_summary_{ts}.csv")
print(f"   {OUT_DIR}/segment_sku_detail_{ts}.csv  ({len(df):,} rows)")
