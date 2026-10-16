import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

sku = '1159022911'

print(f"=== {sku} in tbd_skus? ===")
print(run(f"sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT COUNT(*) FROM tbd_skus WHERE item_number='{sku}';\""))

print(f"\n=== {sku} in fc2sfc_tasks ===")
print(run(f"sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id,location_no,qty_to_transfer,rule FROM fc2sfc_tasks WHERE item_number='{sku}' LIMIT 5;\""))

print(f"\n=== {sku} in fc2sfc_triggers ===")
print(run(f"sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT trigger_type,fc_qty,seg FROM fc2sfc_triggers WHERE item_number='{sku}';\""))

print(f"\n=== {sku} in sales cache (inv_days_30) ===")
print(run(f"sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT inv_days_30,active_order_days,avg_daily_orders FROM sales WHERE item_number='{sku}';\""))

# Check MySQL directly - what is the actual inv_days_30 for this SKU
script = f"""
cd /opt/inv-allocation
.venv/bin/python3 - << 'EOF'
import pymysql, os
from dotenv import load_dotenv
load_dotenv('/opt/inv-allocation/.env')
conn = pymysql.connect(host=os.getenv('MYSQL_HOST'), user=os.getenv('MYSQL_USER'),
                       password=os.getenv('MYSQL_PASS'), port=int(os.getenv('MYSQL_PORT','3306')), charset='utf8mb4')
with conn.cursor() as cur:
    # Check FC inventory for this SKU
    cur.execute('''
        SELECT wsl.zone_id, wsl.location_type, SUM(li.quantity) as qty
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
        WHERE li.warehouse_number='002' AND li.item_number='1159022911' AND li.quantity>0
          AND wsl.location_type IN (3,4)
        GROUP BY wsl.zone_id, wsl.location_type
    ''')
    rows = cur.fetchall()
    print("MySQL inventory (zone_id, loc_type, qty):")
    for r in rows:
        print(f"  zone={r[0]} loc_type={r[1]} qty={r[2]}")
conn.close()
EOF
"""
print(f"\n=== MySQL inventory for {sku} ===")
print(run(script))

time.sleep(60)
