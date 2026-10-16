#!/bin/bash
cd /opt/inv-allocation

echo "=== B00911 in fc2sfc_tasks ==="
sqlite3 results/sfc_cache.db "SELECT list_id, location_no, item_number, qty_to_transfer, rule FROM fc2sfc_tasks WHERE location_no='B00911' LIMIT 20;"

echo ""
echo "=== Total LOC_CLEAR rows/locations ==="
sqlite3 results/sfc_cache.db "SELECT list_id, COUNT(*) as rows, COUNT(DISTINCT location_no) as locs FROM fc2sfc_tasks GROUP BY list_id;"

echo ""
echo "=== Sample LOC_CLEAR rows ==="
sqlite3 results/sfc_cache.db "SELECT location_no, item_number, qty_to_transfer FROM fc2sfc_tasks WHERE list_id='LOC_CLEAR' LIMIT 10;"

echo ""
echo "=== MySQL: what is physically in B00911? ==="
.venv/bin/python -c "
import pymysql, os
from dotenv import load_dotenv
load_dotenv('.env')
conn = pymysql.connect(host=os.getenv('MYSQL_HOST'), user=os.getenv('MYSQL_USER'), password=os.getenv('MYSQL_PASS'), port=int(os.getenv('MYSQL_PORT','3306')), charset='utf8mb4')
with conn.cursor() as cur:
    cur.execute('''
        SELECT li.item_number, SUM(li.quantity) as qty,
               IFNULL(ii.business_type,0) as btype
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
        LEFT JOIN yamibuy_im.im_item ii ON ii.item_number=li.item_number
        WHERE li.warehouse_number='002' AND li.location_no='B00911' AND li.quantity>0
          AND wsl.location_type IN (3,4) AND wsl.zone_id != 2
        GROUP BY li.item_number, ii.business_type
    ''')
    rows = cur.fetchall()
print('Physical contents of B00911 (FC zone):')
for r in rows:
    print(f'  item={r[0]}  qty={r[1]}  business_type={r[2]}')
conn.close()
"
