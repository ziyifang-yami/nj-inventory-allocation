"""Check why SKU 1155015341 has no segment in the allocation system."""
import os, sys, sqlite3
os.chdir('/opt/inv-allocation')
sys.path.insert(0, '/opt/inv-allocation')

SKU = '1155015341'
db_path = '/opt/inv-allocation/results/sfc_cache.db'

print(f"=== Checking SKU {SKU} ===\n")

with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as conn:
    # 1. Check sales table
    row = conn.execute(
        "SELECT item_number, inv_days_30, order_lines_30d, active_order_days, avg_daily_orders "
        "FROM sales WHERE item_number=?", (SKU,)
    ).fetchone()
    print(f"Sales table: {row}")

    # 2. Check tbd_skus
    tbd = conn.execute("SELECT 1 FROM tbd_skus WHERE item_number=?", (SKU,)).fetchone()
    print(f"In tbd_skus: {tbd is not None}")

    # 3. Check fc_lots
    fc_rows = conn.execute(
        "SELECT item_number, location_no, qty FROM fc_lots WHERE item_number=? LIMIT 5", (SKU,)
    ).fetchall()
    print(f"fc_lots rows: {fc_rows}")

    # 4. Check sfc_lots
    sfc_rows = conn.execute(
        "SELECT item_number, location_no, qty FROM sfc_lots WHERE item_number=? LIMIT 5", (SKU,)
    ).fetchall()
    print(f"sfc_lots rows: {sfc_rows}")

# 5. Check MySQL inventory
import pymysql
from dotenv import load_dotenv
load_dotenv('/opt/inv-allocation/.env')

conn_m = pymysql.connect(
    host=os.environ['MYSQL_HOST'], user=os.environ['MYSQL_USER'],
    password=os.environ['MYSQL_PASS'], port=int(os.environ.get('MYSQL_PORT','3306')),
    charset='utf8mb4'
)
with conn_m.cursor() as cur:
    cur.execute("""
        SELECT
          SUM(CASE WHEN wsl.zone_id != 2 AND wsl.location_type IN (3,4) THEN wit.quantity ELSE 0 END) AS fc_qty,
          SUM(CASE WHEN wsl.zone_id  = 2 AND wsl.location_type IN (3,4) THEN wit.quantity ELSE 0 END) AS sfc_qty
        FROM yamibuy_wh.wh_storage_location_item wsli
        JOIN yamibuy_wh.wh_inventory_transaction wit
          ON wit.item_number=wsli.item_number AND wit.warehouse_number=wsli.warehouse_number
         AND wit.location_no=wsli.location_no
        JOIN yamibuy_wh.wh_storage_location wsl
          ON wsl.location_no=wsli.location_no AND wsl.warehouse_number=wsli.warehouse_number
        WHERE wsli.warehouse_number='002' AND wsli.item_number=%s
          AND wsl.location_type IN (3,4) AND wsl.size_id NOT IN (13,33)
    """, (SKU,))
    r = cur.fetchone()
    print(f"\nMySQL inventory: fc_qty={r[0]}, sfc_qty={r[1]}")

    # 6. Check Athena cache
conn_m.close()

# Check athena cache csv
import pandas as pd
try:
    df_a = pd.read_csv('/opt/inv-allocation/results/athena_cache.csv')
    row_a = df_a[df_a['item_number'].astype(str) == SKU]
    print(f"\nAthena cache: {row_a[['item_number','inv_days_30','order_lines_30d','active_order_days','avg_daily_orders']].to_string() if not row_a.empty else 'NOT FOUND'}")
except Exception as e:
    print(f"Athena cache error: {e}")
