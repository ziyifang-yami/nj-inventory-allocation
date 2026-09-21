"""
NJ Inventory Allocation — Data Loader
=======================================
从 Athena + MySQL 读取所需数据。
查询逻辑来自：
  - weekly metrics: BY_weekly_ops_automation_update44.py
  - 表结构探索: check_tables.py / explore_athena.py
"""

import os
import time
import boto3
import pandas as pd
import pymysql
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ──────────────────────────────────────────────────────────
# Athena (boto3 direct — more reliable than pyathena)
# ──────────────────────────────────────────────────────────
REGION    = os.getenv("ATHENA_REGION", "us-west-2")
WORKGROUP = os.getenv("ATHENA_WORK_GROUP", "ziyi.fang")
S3_OUT    = os.getenv("ATHENA_S3_STAGING",
                      "s3://aws-athena-query-results-us-west-2-654654218498/") + "nj-allocation/"
AWS_PROFILE = os.getenv("AWS_PROFILE", "default")

_athena_client = None

def _get_athena():
    global _athena_client
    if _athena_client is None:
        session = boto3.Session(profile_name=AWS_PROFILE)
        _athena_client = session.client("athena", region_name=REGION)
    return _athena_client


def run_athena(sql: str, database: str = "yamibuy_wh", timeout: int = 120) -> pd.DataFrame:
    """Execute Athena SQL and return DataFrame."""
    client = _get_athena()
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
            reason = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"Athena query {state}: {reason}")
        time.sleep(2)
    else:
        raise TimeoutError(f"Athena query timed out after {timeout}s")

    # Paginate results
    rows_all = []
    paginator = client.get_paginator("get_query_results")
    for page in paginator.paginate(QueryExecutionId=qid):
        rows_all.extend(page["ResultSet"]["Rows"])

    if not rows_all:
        return pd.DataFrame()
    cols = [c["VarCharValue"] for c in rows_all[0]["Data"]]
    data = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows_all[1:]]
    return pd.DataFrame(data, columns=cols)


# ──────────────────────────────────────────────────────────
# MySQL (WMS live data)
# ──────────────────────────────────────────────────────────
_mysql_conn = None

def _get_mysql():
    global _mysql_conn
    if _mysql_conn is None or not _mysql_conn.open:
        _mysql_conn = pymysql.connect(
            host=os.getenv("MYSQL_HOST", "rds.g3.yamibuy.net"),
            user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASS"),
            port=3306,
            charset="utf8mb4",
        )
    return _mysql_conn


def run_mysql(sql: str) -> pd.DataFrame:
    """Execute MySQL SQL and return DataFrame."""
    conn = _get_mysql()
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


# ══════════════════════════════════════════════════════════
# 1. SKU Segment  (source: dwb_trade.trade_item_sale_agg_daily)
#    nj_segment values: Hit / Head / Middle / Longtail / TBD / 0-Star
# ══════════════════════════════════════════════════════════

def load_sku_segments(warehouse: str = "002") -> pd.DataFrame:
    """
    Latest NJ segment per SKU.
    Returns: item_number, nj_segment, la_segment, bu, day_id
    Source: dwb_trade.trade_item_sale_agg_daily (most recent day_id)
    """
    sql = f"""
    SELECT item_number, nj_segment, la_segment, bu, day_id
    FROM dwb_trade.trade_item_sale_agg_daily
    WHERE warehouse_number = '{warehouse}'
      AND day_id = (
          SELECT MAX(day_id) FROM dwb_trade.trade_item_sale_agg_daily
          WHERE warehouse_number = '{warehouse}'
      )
    GROUP BY item_number, nj_segment, la_segment, bu, day_id
    """
    return run_athena(sql, database="dwb_trade")


# ══════════════════════════════════════════════════════════
# 2. Current Inventory Snapshot  (source: yamibuy_inventory.inventory_record)
#    Same formula used in weekly metrics for AvgUnits & SKU counts
# ══════════════════════════════════════════════════════════

def load_inventory_snapshot(warehouse: str = "002") -> pd.DataFrame:
    """
    Latest inventory snapshot per SKU per warehouse.
    total_qty = available + allocated + reserved + receive + in_transit + transship_in + transship_out
    Source: yamibuy_inventory.inventory_record (latest dt partition)

    Returns: item_number, warehouse_number, total_qty, available_qty, dt
    """
    sql = f"""
    SELECT ir.item_number,
           ir.warehouse_number,
           (ir.available_qty + ir.allocated_qty + ir.reserved_qty
            + ir.receive_qty + COALESCE(ir.in_transit_qty, 0)
            + COALESCE(ir.transship_in_qty, 0) + COALESCE(ir.transship_out_qty, 0)) AS total_qty,
           ir.available_qty,
           ir.allocated_qty,
           ir.reserved_qty,
           ir.receive_qty,
           ir.dt
    FROM yamibuy_inventory.inventory_record ir
    WHERE ir.warehouse_number = '{warehouse}'
      AND ir.dt = (
          SELECT MAX(dt) FROM yamibuy_inventory.inventory_record
          WHERE warehouse_number = '{warehouse}'
      )
      AND ir.virtual_qty = 0
    """
    return run_athena(sql, database="yamibuy_inventory")


def load_inventory_both_wh() -> pd.DataFrame:
    """
    Load FC (001) and SFC (002) inventory side-by-side per SKU.
    Returns: item_number, fc_qty, sfc_qty (latest snapshot)
    """
    sql = """
    SELECT item_number,
           MAX(CASE WHEN warehouse_number = '001' THEN total_qty ELSE 0 END) AS fc_qty,
           MAX(CASE WHEN warehouse_number = '002' THEN total_qty ELSE 0 END) AS sfc_qty
    FROM (
        SELECT ir.item_number, ir.warehouse_number,
               (ir.available_qty + ir.allocated_qty + ir.reserved_qty
                + ir.receive_qty + COALESCE(ir.in_transit_qty, 0)
                + COALESCE(ir.transship_in_qty, 0) + COALESCE(ir.transship_out_qty, 0)) AS total_qty
        FROM yamibuy_inventory.inventory_record ir
        WHERE ir.warehouse_number IN ('001', '002')
          AND ir.virtual_qty = 0
          AND ir.dt = (SELECT MAX(dt) FROM yamibuy_inventory.inventory_record
                       WHERE warehouse_number = '002')
    ) t
    GROUP BY item_number
    """
    return run_athena(sql, database="yamibuy_inventory")


# ══════════════════════════════════════════════════════════
# 3. Active Selling Days  (source: dwb_trade.trade_item_sale_agg_daily)
#    PPT framework: "≥5 active selling days" is a key threshold
# ══════════════════════════════════════════════════════════

def load_active_selling_days(warehouse: str = "002", lookback_days: int = 30) -> pd.DataFrame:
    """
    Count of distinct days with ≥1 order per SKU in the past N days.
    Returns: item_number, active_days, total_orders, total_sales_units
    """
    sql = f"""
    SELECT item_number,
           COUNT(DISTINCT day_id)  AS active_days,
           SUM(orders)             AS total_orders,
           SUM(sales)              AS total_sales_units
    FROM dwb_trade.trade_item_sale_agg_daily
    WHERE warehouse_number = '{warehouse}'
      AND orders > 0
      AND day_id >= DATE_FORMAT(DATE_ADD('day', -{lookback_days},
                    DATE(CURRENT_TIMESTAMP AT TIME ZONE 'America/Los_Angeles')), '%Y-%m-%d')
    GROUP BY item_number
    """
    return run_athena(sql, database="dwb_trade")


# ══════════════════════════════════════════════════════════
# 4. Inventory Turns  (Athena — same as weekly metrics query_turns)
#    ITR = Sales Cost (30d) / Avg Inventory Cost (30d)
#    Then annualised: ITR * (365/30)
# ══════════════════════════════════════════════════════════

def load_inventory_turns(warehouse: str = "002", lookback_days: int = 30) -> pd.DataFrame:
    """
    Inventory Turns (annualised) for the past N days.
    Formula: SUM(shipped_cost) / AVG(daily_inv_cost) * (365/days)
    Source: yamibuy_inventory.inventory_record + yamibuy_master.xysc_order_info
    Returns: warehouse_number, avg_inventory_cost, total_sales_cost, turns_annualised
    """
    sql = f"""
    WITH inv AS (
        SELECT ir.warehouse_number,
               DATE_FORMAT(FROM_UNIXTIME(ir.record_dtm, 'America/Los_Angeles'), '%Y-%m-%d') AS dt,
               SUM((ir.available_qty + ir.allocated_qty + ir.reserved_qty + ir.receive_qty)
                   * piac.avg_cost) AS inv_cost
        FROM yamibuy_inventory.inventory_record ir
        LEFT JOIN yamibuy_po.po_item_avg_cost piac
               ON piac.item_number = ir.item_number AND piac.warehouse_number = ir.warehouse_number
        WHERE ir.warehouse_number = '{warehouse}'
          AND ir.virtual_qty = 0
          AND DATE_FORMAT(FROM_UNIXTIME(ir.record_dtm, 'America/Los_Angeles'), '%Y-%m-%d')
              >= DATE_FORMAT(DATE_ADD('day', -{lookback_days},
                 DATE(CURRENT_TIMESTAMP AT TIME ZONE 'America/Los_Angeles')), '%Y-%m-%d')
        GROUP BY 1, 2
    ),
    avg_inv AS (
        SELECT warehouse_number,
               SUM(inv_cost) / COUNT(DISTINCT dt) AS avg_inv_cost
        FROM inv
        GROUP BY warehouse_number
    ),
    sales AS (
        SELECT a.warehouse_number,
               SUM(b.cost * b.goods_number) AS sales_cost
        FROM yamibuy_master.xysc_order_info a
        LEFT JOIN yamibuy_master.xysc_order_goods b ON a.order_id = b.order_id
        WHERE a.warehouse_number = '{warehouse}'
          AND a.pay_status > 1
          AND a.vendor_id = 0
          AND a.source_flag != 13
          AND DATE(FROM_UNIXTIME(a.shipping_time, 'America/Los_Angeles'))
              >= DATE_ADD('day', -{lookback_days},
                 DATE(CURRENT_TIMESTAMP AT TIME ZONE 'America/Los_Angeles'))
        GROUP BY 1
    )
    SELECT a.warehouse_number,
           a.avg_inv_cost,
           s.sales_cost,
           ROUND(s.sales_cost / NULLIF(a.avg_inv_cost, 0) * (365.0 / {lookback_days}), 2) AS turns_annualised
    FROM avg_inv a
    LEFT JOIN sales s ON a.warehouse_number = s.warehouse_number
    """
    return run_athena(sql, database="yamibuy_master")


# ══════════════════════════════════════════════════════════
# 5. Volume (CBM) per SKU  (MySQL — from weekly metrics query_ibb)
#    volume from yamibuy_master.xysc_wearhouse_goods
# ══════════════════════════════════════════════════════════

def load_sku_volume(warehouse: str = "002") -> pd.DataFrame:
    """
    Per-SKU volume (CBM) and current inventory depth from WMS location data.
    Source: wh_storage_location_item + xysc_wearhouse_goods (MySQL)
    Returns: item_number, volume_cbm, quantity, total_volume_cbm, segment, business_type, status
    """
    sql = f"""
    SELECT wsli.item_number,
           wwg.volume                        AS volume_cbm,
           wit.quantity,
           IFNULL(wwg.volume * wit.quantity, 0) AS total_volume_cbm,
           CASE WHEN pps.segment = 6 THEN '0-Star'
                WHEN pps.segment = 1 THEN '1-Hit'
                WHEN pps.segment = 2 THEN '2-Head'
                WHEN pps.segment = 3 THEN '3-Middle'
                WHEN pps.segment = 4 THEN '4-Longtail'
                ELSE 'TBD' END              AS segment,
           CASE WHEN ii.business_type = 5 THEN 'FBY' ELSE 'Owned' END AS business_type,
           CASE WHEN ii.status = 'A' THEN 'On Sale' ELSE 'Not On Sale' END AS status,
           ii.avg_cost,
           IFNULL(ii.avg_cost * wit.quantity, 0) AS total_cost,
           c1.category_name                 AS domain
    FROM yamibuy_wh.wh_storage_location_item wsli
    LEFT JOIN yamibuy_im.im_item ii
           ON ii.item_number = wsli.item_number
    LEFT JOIN yamibuy_master.xysc_wearhouse_goods wwg
           ON wwg.goods_id = ii.goods_id
    LEFT JOIN yamibuy_wh.wh_inventory_transaction wit
           ON wit.item_number = wsli.item_number
          AND wit.warehouse_number = wsli.warehouse_number
          AND wit.location_no = wsli.location_no
    LEFT JOIN yamibuy_bigdata.po_product_segment pps
           ON pps.warehouse_number = wsli.warehouse_number
          AND pps.item_number = ii.item_number
    LEFT JOIN yamibuy_wh.wh_storage_location wsl
           ON wsl.location_no = wsli.location_no
          AND wsl.warehouse_number = wsli.warehouse_number
    LEFT JOIN yamibuy_im.im_category c3 ON c3.category_id = ii.category_id
    LEFT JOIN yamibuy_im.im_category c2 ON c2.category_id = c3.parent_category_id
    LEFT JOIN yamibuy_im.im_category c1 ON c1.category_id = c2.parent_category_id
    WHERE wsli.warehouse_number = '{warehouse}'
      AND wit.quantity IS NOT NULL
      AND wit.quantity != 0
      AND wsl.location_type IN (3, 4)
      AND wsl.size_id NOT IN (13, 33)
    """
    return run_mysql(sql)


# ══════════════════════════════════════════════════════════
# 6. D2S — Days to Stockout  (calculated from inventory + daily sales rate)
#    D2S = current_qty / avg_daily_sales  (using past 30d)
# ══════════════════════════════════════════════════════════

def load_d2s(warehouse: str = "002", lookback_days: int = 30) -> pd.DataFrame:
    """
    D2S = total_qty / (sum_orders_last_N_days / N)
    Returns: item_number, warehouse_number, total_qty, avg_daily_orders, d2s
    Merges inventory snapshot + active selling days.
    """
    inv = load_inventory_snapshot(warehouse=warehouse)
    sales = load_active_selling_days(warehouse=warehouse, lookback_days=lookback_days)

    inv["total_qty"] = pd.to_numeric(inv["total_qty"], errors="coerce").fillna(0)
    sales["total_orders"] = pd.to_numeric(sales["total_orders"], errors="coerce").fillna(0)

    df = inv.merge(sales[["item_number", "total_orders", "active_days"]], on="item_number", how="left")
    df["total_orders"] = df["total_orders"].fillna(0)
    df["active_days"]  = df["active_days"].fillna(0)
    df["avg_daily_orders"] = df["total_orders"] / lookback_days
    df["d2s"] = (df["total_qty"] / df["avg_daily_orders"].replace(0, float("nan"))).round(1)
    return df[["item_number", "warehouse_number", "total_qty", "avg_daily_orders", "active_days", "d2s", "dt"]]


# ══════════════════════════════════════════════════════════
# 7. Full allocation dataset  (joins all above)
# ══════════════════════════════════════════════════════════

def load_allocation_dataset(warehouse: str = "002", lookback_days: int = 30) -> pd.DataFrame:
    """
    Master dataset for allocation engine:
    SKU | segment | d2s | active_days | fc_qty | sfc_qty | volume_cbm | total_volume | business_type
    """
    print("Loading segments...")
    seg = load_sku_segments(warehouse=warehouse)
    seg = seg[["item_number", "nj_segment", "la_segment", "bu"]].drop_duplicates("item_number")

    print("Loading D2S...")
    d2s = load_d2s(warehouse=warehouse, lookback_days=lookback_days)

    print("Loading both-WH inventory...")
    inv_both = load_inventory_both_wh()

    # Merge
    df = d2s.merge(seg, on="item_number", how="left")
    df = df.merge(inv_both, on="item_number", how="left")

    df["nj_segment"] = df["nj_segment"].fillna("TBD")
    df["fc_qty"]     = pd.to_numeric(df.get("fc_qty", 0), errors="coerce").fillna(0)
    df["sfc_qty"]    = pd.to_numeric(df.get("sfc_qty", 0), errors="coerce").fillna(0)
    df["d2s"]        = pd.to_numeric(df["d2s"], errors="coerce")
    df["active_days"]= pd.to_numeric(df["active_days"], errors="coerce").fillna(0)

    return df
