"""
Daily cache refresh script — run via cron at midnight NJ time.
Deletes disk cache files so next app load re-queries Athena.
Usage: python refresh_cache.py
"""
import os, sys
from pathlib import Path

# Always run from repo root
SCRIPT_DIR = Path(__file__).parent
os.chdir(SCRIPT_DIR)

CACHE_CSV = Path("results/athena_cache.csv")
CACHE_TS  = Path("results/athena_cache_ts.txt")

print(f"[refresh_cache] Deleting disk cache...")
CACHE_CSV.unlink(missing_ok=True)
CACHE_TS.unlink(missing_ok=True)
print(f"[refresh_cache] Cache cleared. Next app load will re-query Athena.")

# Optionally pre-warm: run the load() logic directly so cache is ready before users arrive
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path("..") / ".env")

    import time, boto3, pandas as pd
    from datetime import datetime
    from zoneinfo import ZoneInfo

    aws_profile = os.getenv("AWS_PROFILE", "athena-prod")
    session = boto3.Session(profile_name=aws_profile)
    athenac = session.client("athena", region_name="us-west-2")
    workgroup = os.getenv("ATHENA_WORK_GROUP", "aiagent")
    s3_out = os.getenv("ATHENA_S3_STAGING", "s3://yami-athena-query-result/") + "aiagent/nj-v3-segment/"

    SQL_SALES = """
    WITH inv_days AS (
        SELECT item_number, dt AS inv_date,
               ROW_NUMBER() OVER (PARTITION BY item_number ORDER BY dt DESC) AS rn
        FROM yamibuy_inventory.inventory_record
        WHERE warehouse_number = '002'
          AND virtual_qty = 0 AND available_qty > 0
          AND (item_number LIKE '1%' OR item_number LIKE '5%')
    ),
    inv_30 AS (SELECT item_number, inv_date FROM inv_days WHERE rn <= 30),
    sales AS (
        SELECT item_number, day_id,
               SUM(orders) AS daily_orders, SUM(sales) AS daily_sales
        FROM dwb_trade.trade_item_sale_agg_daily
        WHERE warehouse_number = '002' AND orders > 0
          AND (item_number LIKE '1%' OR item_number LIKE '5%')
        GROUP BY item_number, day_id
    )
    SELECT i.item_number,
           COUNT(DISTINCT i.inv_date) AS inv_days_30,
           COUNT(DISTINCT s.day_id)   AS active_order_days,
           COALESCE(CAST(SUM(s.daily_orders) AS DOUBLE), 0)      AS order_lines_30d,
           COALESCE(CAST(SUM(s.daily_orders) AS DOUBLE)/30.0, 0) AS avg_daily_orders,
           COALESCE(SUM(s.daily_sales), 0)                       AS sales_units_30d
    FROM inv_30 i
    LEFT JOIN sales s ON s.item_number = i.item_number AND s.day_id = i.inv_date
    GROUP BY i.item_number
    """

    print("[refresh_cache] Submitting Athena query...")
    resp = athenac.start_query_execution(
        QueryString=SQL_SALES,
        QueryExecutionContext={"Database": "yamibuy_inventory"},
        WorkGroup=workgroup,
        ResultConfiguration={"OutputLocation": s3_out},
    )
    qid = resp["QueryExecutionId"]
    for _ in range(150):
        state = athenac.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED": break
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Athena {state}")
        time.sleep(2)

    rows_all = []
    for page in athenac.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
        rows_all.extend(page["ResultSet"]["Rows"])
    cols = [c["VarCharValue"] for c in rows_all[0]["Data"]]
    data = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows_all[1:]]
    df = pd.DataFrame(data, columns=cols)
    for c in ["inv_days_30","active_order_days","order_lines_30d","avg_daily_orders","sales_units_30d"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    CACHE_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CACHE_CSV, index=False)
    now_nj = datetime.now(ZoneInfo("America/New_York"))
    CACHE_TS.write_text(datetime.now().isoformat())
    print(f"[refresh_cache] ✅ Cache refreshed at {now_nj.strftime('%Y-%m-%d %H:%M ET')} — {len(df):,} SKUs")

except Exception as e:
    print(f"[refresh_cache] ⚠️  Pre-warm failed: {e}")
    print("[refresh_cache] Cache files deleted — app will re-query on next load.")
    sys.exit(0)
