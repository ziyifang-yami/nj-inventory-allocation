#!/bin/bash
cd /opt/inv-allocation
.venv/bin/python - <<'EOF'
import os, sqlite3
from dotenv import load_dotenv
import pymysql

load_dotenv(dotenv_path=".env")

loc = "B00911"

db = sqlite3.connect('results/sfc_cache.db')
rows = db.execute("SELECT list_id, item_number, qty_to_transfer, rule FROM fc2sfc_tasks WHERE location_no=?", (loc,)).fetchall()
print(f"\n=== SQLite fc2sfc_tasks for {loc} ===")
for r in rows:
    print(f"  list={r[0]}, item={r[1]}, qty={r[2]}, rule={r[3]}")
if not rows:
    print("  (no rows)")

conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASS"), port=int(os.getenv("MYSQL_PORT","3306")),
    charset="utf8mb4", connect_timeout=10)
with conn.cursor() as cur:
    cur.execute("""
        SELECT li.item_number, li.quantity
        FROM yamibuy_wh.wh_lot_inventory li
        WHERE li.warehouse_number='002' AND li.location_no=%s AND li.quantity>0
    """, (loc,))
    mysql_rows = cur.fetchall()
conn.close()

print(f"\n=== MySQL wh_lot_inventory for {loc} ===")
for r in mysql_rows:
    print(f"  item={r[0]}, qty={r[1]}")
print(f"  Total distinct SKUs: {len(set(r[0] for r in mysql_rows))}")
db.close()
EOF
