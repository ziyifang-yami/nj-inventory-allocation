import subprocess

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=60)
    return r.stdout.strip()

pycode = """
import pymysql, os
from dotenv import load_dotenv
load_dotenv('/opt/inv-allocation/.env')

conn = pymysql.connect(
    host=os.getenv('MYSQL_HOST'), user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASS'), port=int(os.getenv('MYSQL_PORT','3306')), charset='utf8mb4'
)
with conn.cursor() as cur:
    # What is ACTUALLY in B00911 right now (raw wh_lot_inventory, qty>0)
    cur.execute('''
        SELECT li.item_number, SUM(li.quantity) as qty, li.location_no
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
        WHERE li.warehouse_number=\'002\' AND wsl.zone_id!=2
          AND wsl.location_type IN (3,4)
          AND li.location_no=\'B00911\'
        GROUP BY li.item_number, li.location_no
    ''')
    rows = cur.fetchall()
    print("ALL rows in B00911 (including qty<=0):")
    for r in rows:
        print(f"  item={r[0]}  qty={r[1]}  loc={r[2]}")

    # Now with qty>0 only (what _loc_live_skus uses)
    cur.execute('''
        SELECT li.item_number, SUM(li.quantity) as qty
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
        WHERE li.warehouse_number=\'002\' AND wsl.zone_id!=2
          AND wsl.location_type IN (3,4)
          AND li.location_no=\'B00911\'
          AND li.quantity>0
        GROUP BY li.item_number HAVING SUM(li.quantity)>0
    ''')
    rows2 = cur.fetchall()
    print("qty>0 only (what _loc_live_skus sees):")
    for r in rows2:
        print(f"  item={r[0]}  qty={r[1]}")

    # Where is 1149090231 actually located now?
    cur.execute('''
        SELECT li.location_no, SUM(li.quantity) as qty
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
        WHERE li.warehouse_number=\'002\' AND wsl.zone_id!=2
          AND wsl.location_type IN (3,4)
          AND li.item_number=\'1149090231\'
          AND li.quantity>0
        GROUP BY li.location_no HAVING SUM(li.quantity)>0
    ''')
    rows3 = cur.fetchall()
    print("Where is 1149090231 (qty>0):")
    for r in rows3:
        print(f"  loc={r[0]}  qty={r[1]}")

conn.close()
"""

upload = subprocess.run(SSH + ['cat > /tmp/chk_b00911.py'],
    input=pycode, capture_output=True, text=True, timeout=15)
print(run('/opt/inv-allocation/.venv/bin/python /tmp/chk_b00911.py'))

import time; time.sleep(60)
print("done")
