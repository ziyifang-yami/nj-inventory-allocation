#!/bin/bash
cd /opt/inv-allocation

# Check what _loc_total_skus returns for B00911
.venv/bin/python -c "
import pymysql, os
from dotenv import load_dotenv
load_dotenv('.env')
conn = pymysql.connect(host=os.getenv('MYSQL_HOST'), user=os.getenv('MYSQL_USER'), password=os.getenv('MYSQL_PASS'), port=int(os.getenv('MYSQL_PORT','3306')), charset='utf8mb4')
with conn.cursor() as cur:
    # The exact _loc_total_skus query from refresh_sfc_cache.py
    cur.execute('''
        SELECT li.location_no,
               COUNT(DISTINCT li.item_number) AS total_skus
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON wsl.location_no = li.location_no
            AND wsl.warehouse_number = li.warehouse_number
        WHERE li.warehouse_number = '002'
          AND li.quantity > 0
          AND wsl.location_type IN (3, 4)
          AND wsl.zone_id != 2
        GROUP BY li.location_no
        HAVING li.location_no = 'B00911'
    ''')
    r = cur.fetchone()
    print(f'_loc_total_skus[B00911] from query = {r}')

    # Also check with HAVING clause works differently - let's do WHERE
    cur.execute('''
        SELECT COUNT(DISTINCT li.item_number) AS total_skus
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON wsl.location_no = li.location_no
            AND wsl.warehouse_number = li.warehouse_number
        WHERE li.warehouse_number = '002'
          AND li.quantity > 0
          AND wsl.location_type IN (3, 4)
          AND wsl.zone_id != 2
          AND li.location_no = 'B00911'
    ''')
    r2 = cur.fetchone()
    print(f'Direct count for B00911 = {r2}')
    
    # What SKUs are in B00911 per this query (raw wh_lot_inventory)
    cur.execute('''
        SELECT DISTINCT li.item_number, SUM(li.quantity) as qty
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON wsl.location_no = li.location_no
            AND wsl.warehouse_number = li.warehouse_number
        WHERE li.warehouse_number = '002'
          AND li.quantity > 0
          AND wsl.location_type IN (3, 4)
          AND wsl.zone_id != 2
          AND li.location_no = 'B00911'
        GROUP BY li.item_number
    ''')
    rows = cur.fetchall()
    print(f'Raw SKUs in B00911 (no filter): {rows}')

conn.close()
"
