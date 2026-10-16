#!/bin/bash
# Quick debug: simulate exactly the LOC_CLEAR logic for B00911
cd /opt/inv-allocation

.venv/bin/python << 'PYEOF'
import sqlite3, os, pymysql
from dotenv import load_dotenv
load_dotenv('.env')

# Step 1: What's in fc2sfc_tasks for B00911?
db = sqlite3.connect('results/sfc_cache.db')
rows = db.execute("SELECT list_id, item_number, qty_to_transfer FROM fc2sfc_tasks WHERE location_no='B00911'").fetchall()
print(f"DB: B00911 tasks: {rows}")

# Step 2: What's in _df_fc_lots for B00911?
# This is what load_fc_lot_data returns — only transfer SKUs
# Let's check what SKUs triggered fc2sfc
triggers = db.execute("SELECT trigger_type, item_number FROM fc2sfc_triggers LIMIT 5").fetchall()
print(f"Sample triggers: {triggers[:5]}")

# Check if 5030123121 is in triggers
r = db.execute("SELECT * FROM fc2sfc_triggers WHERE item_number='5030123121'").fetchone()
print(f"5030123121 in triggers: {r}")

r2 = db.execute("SELECT * FROM fc2sfc_triggers WHERE item_number='1149090231'").fetchone()
print(f"1149090231 in triggers: {r2}")

db.close()

# Step 3: Now run the exact _loc_total_skus query for B00911
conn = pymysql.connect(host=os.getenv('MYSQL_HOST'), user=os.getenv('MYSQL_USER'), 
                       password=os.getenv('MYSQL_PASS'), port=int(os.getenv('MYSQL_PORT','3306')), charset='utf8mb4')
with conn.cursor() as cur:
    cur.execute("""
        SELECT li.location_no, COUNT(DISTINCT li.item_number) AS total_skus
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON wsl.location_no = li.location_no
            AND wsl.warehouse_number = li.warehouse_number
        WHERE li.warehouse_number = '002'
          AND li.quantity > 0
          AND wsl.location_type IN (3, 4)
          AND wsl.zone_id != 2
          AND li.location_no = 'B00911'
        GROUP BY li.location_no
    """)
    r = cur.fetchone()
    print(f"MySQL total_skus for B00911 = {r}")

# Step 4: load_fc_lot_data result for B00911
with conn.cursor() as cur:
    cur.execute("""
        SELECT li.item_number, li.location_no, SUM(li.quantity) as qty
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
        LEFT JOIN yamibuy_wh.wh_lot_master lm ON lm.lot_id=li.lot_id
        LEFT JOIN yamibuy_wh.wh_item_setting wis ON wis.item_number=li.item_number AND wis.warehouse_number='002'
        WHERE li.warehouse_number='002' AND li.quantity>0
          AND wsl.location_type IN (3,4) AND wsl.zone_id!=2
          AND li.location_no='B00911'
        GROUP BY li.item_number, li.location_no, wsl.location_type, lm.lot_id, lm.receive_time, wis.spec
        HAVING SUM(li.quantity)>0
    """)
    rows = cur.fetchall()
    print(f"load_fc_lot_data rows for B00911: {rows}")
conn.close()
PYEOF
