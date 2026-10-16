import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

sku = '5174058421'

# Sales cache
print(f"=== {sku} in sales cache ===")
print(run(f"sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT item_number,inv_days_30,active_order_days,order_lines_30d,avg_daily_orders FROM sales WHERE item_number='{sku}';\""))

# TBD?
print(f"\n=== {sku} in tbd_skus ===")
print(run(f"sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT COUNT(*) FROM tbd_skus WHERE item_number='{sku}';\""))

# In task_list T1/T2
print(f"\n=== {sku} in SFC→FC task_list ===")
print(run(f"sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id,location_no,qty_to_transfer,fc_d2s,avg_daily_orders FROM task_list WHERE item_number='{sku}';\""))

# MySQL: actual inventory + inbound date
script = f"""
cd /opt/inv-allocation
.venv/bin/python3 - << 'EOF'
import pymysql, os
from dotenv import load_dotenv
load_dotenv('/opt/inv-allocation/.env')
conn = pymysql.connect(host=os.getenv('MYSQL_HOST'), user=os.getenv('MYSQL_USER'),
                       password=os.getenv('MYSQL_PASS'), port=int(os.getenv('MYSQL_PORT','3306')), charset='utf8mb4')
with conn.cursor() as cur:
    cur.execute('''
        SELECT li.location_no, SUM(li.quantity) as qty,
               MIN(DATE(FROM_UNIXTIME(NULLIF(ib.in_dtm,0)))) as first_inbound
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
        LEFT JOIN yamibuy_wh.wh_lot_master lm ON lm.lot_id=li.lot_id
        LEFT JOIN yamibuy_wh.wh_inbound_batch ib ON ib.lot_id=li.lot_id
        WHERE li.warehouse_number='002' AND li.item_number='5174058421'
          AND li.quantity>0 AND wsl.location_type IN (3,4)
        GROUP BY li.location_no
    ''')
    rows = cur.fetchall()
    print(f"MySQL inventory for 5174058421:")
    for r in rows:
        print(f"  loc={r[0]} qty={r[1]} first_inbound={r[2]}")
conn.close()
EOF
"""
print(f"\n=== MySQL inventory for {sku} ===")
print(run(script))

time.sleep(60)
