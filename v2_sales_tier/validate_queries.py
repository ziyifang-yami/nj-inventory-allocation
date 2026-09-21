"""
验证关键查询是否可以正常运行（只取 LIMIT 5 测试）
Run: python validate_queries.py
"""
import time, os, boto3
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

session = boto3.Session(profile_name="default")
client  = session.client("athena", region_name="us-west-2")
WG      = "ziyi.fang"
S3      = "s3://aws-athena-query-results-us-west-2-654654218498/validate/"

def run(sql, db="yamibuy_wh", label=""):
    print(f"\n{'─'*50}")
    print(f"[{label}]")
    resp = client.start_query_execution(
        QueryString=sql, QueryExecutionContext={"Database": db},
        WorkGroup=WG, ResultConfiguration={"OutputLocation": S3},
    )
    qid = resp["QueryExecutionId"]
    for _ in range(30):
        st = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
        if st == "SUCCEEDED": break
        if st in ("FAILED","CANCELLED"):
            r = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"].get("StateChangeReason","")
            print(f"  ❌ {st}: {r}"); return
        time.sleep(2)
    rows = client.get_query_results(QueryExecutionId=qid, MaxResults=6)["ResultSet"]["Rows"]
    cols = [c["VarCharValue"] for c in rows[0]["Data"]]
    print(f"  ✅ Columns: {cols}")
    for r in rows[1:]:
        vals = {cols[i]: c.get("VarCharValue","") for i,c in enumerate(r["Data"])}
        print(f"  → {dict(list(vals.items())[:6])}")

# 1. NJ Segment (latest day)
run("""
SELECT item_number, nj_segment, la_segment, bu, day_id
FROM trade_item_sale_agg_daily
WHERE warehouse_number = '002'
  AND day_id = (SELECT MAX(day_id) FROM trade_item_sale_agg_daily WHERE warehouse_number = '002')
GROUP BY item_number, nj_segment, la_segment, bu, day_id
LIMIT 5
""", db="dwb_trade", label="Segment (NJ latest)")

# 2. Active Selling Days (past 30d)
run("""
SELECT item_number,
       COUNT(DISTINCT day_id) AS active_days,
       SUM(orders)            AS total_orders
FROM trade_item_sale_agg_daily
WHERE warehouse_number = '002'
  AND orders > 0
  AND day_id >= DATE_FORMAT(DATE_ADD('day', -30,
      DATE(CURRENT_TIMESTAMP AT TIME ZONE 'America/Los_Angeles')), '%Y-%m-%d')
GROUP BY item_number
LIMIT 5
""", db="dwb_trade", label="Active Selling Days (30d)")

# 3. Inventory Snapshot (latest)
run("""
SELECT ir.item_number, ir.warehouse_number,
       (ir.available_qty + ir.allocated_qty + ir.reserved_qty
        + ir.receive_qty + COALESCE(ir.in_transit_qty,0)
        + COALESCE(ir.transship_in_qty,0) + COALESCE(ir.transship_out_qty,0)) AS total_qty,
       ir.dt
FROM inventory_record ir
WHERE ir.warehouse_number = '002'
  AND ir.dt = (SELECT MAX(dt) FROM inventory_record WHERE warehouse_number = '002')
  AND ir.virtual_qty = 0
LIMIT 5
""", db="yamibuy_inventory", label="Inventory Snapshot (NJ latest)")

# 4. Both-WH inventory
run("""
SELECT item_number,
       MAX(CASE WHEN warehouse_number='001' THEN total_qty ELSE 0 END) AS fc_qty,
       MAX(CASE WHEN warehouse_number='002' THEN total_qty ELSE 0 END) AS sfc_qty
FROM (
    SELECT ir.item_number, ir.warehouse_number,
           (ir.available_qty + ir.allocated_qty + ir.reserved_qty
            + ir.receive_qty + COALESCE(ir.in_transit_qty,0)
            + COALESCE(ir.transship_in_qty,0) + COALESCE(ir.transship_out_qty,0)) AS total_qty
    FROM inventory_record ir
    WHERE ir.warehouse_number IN ('001','002') AND ir.virtual_qty = 0
      AND ir.dt = (SELECT MAX(dt) FROM inventory_record WHERE warehouse_number='002')
) t
GROUP BY item_number
LIMIT 5
""", db="yamibuy_inventory", label="FC+SFC inventory side-by-side")

print("\n\n✅ All validation queries done.")
