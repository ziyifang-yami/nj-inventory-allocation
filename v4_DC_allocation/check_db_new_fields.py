"""
V4 DB New Fields Validator — Round 2
=====================================
Validates fields needed for the updated SFC→FC logic:
1. wh_lot_master.receive_time — correct inv_age source for non-LEGACY lots
2. im_item_extend.piece_pack vs pieces_per_pack — correct case_qty field
3. wh_lot_master.lot_id — LEGACY lot flag (lot_id = 1)
4. wh_inbound_batch.qc_ExpiredDate — EXP date (already known, sanity check)
5. Sample query: MIN(fc_exp_date) per SKU from FC lots

Run from: c:/Project-Z/NJ_Inventory_Allocation
"""
import os, sys
from pathlib import Path
from dotenv import load_dotenv

for env_path in [Path(".") / ".env", Path("..") / ".env"]:
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        break

import pymysql

def conn():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"), port=3306,
        charset="utf8mb4", connect_timeout=10,
    )

def show_cols(c, db, tbl, fields):
    print(f"\n{'='*60}\nTABLE: {db}.{tbl}\n{'='*60}")
    with c.cursor() as cur:
        cur.execute(f"SHOW FULL COLUMNS FROM `{db}`.`{tbl}`")
        rows = cur.fetchall()
        desc = [d[0] for d in cur.description]
    col_map = {r[0]: dict(zip(desc, r)) for r in rows}
    for f in fields:
        if f in col_map:
            x = col_map[f]
            print(f"  ✅ {f}  Type={x.get('Type')}  Null={x.get('Null')}  Comment={x.get('Comment') or '(none)'}")
        else:
            print(f"  ❌ {f} NOT FOUND. Available: {sorted(col_map.keys())[:20]}")

c = conn()
print(f"✅ Connected to {os.getenv('MYSQL_HOST')}\n")

# 1. wh_lot_master — receive_time and lot_id
show_cols(c, "yamibuy_wh", "wh_lot_master", [
    "lot_id",
    "receive_time",
    "source_number",
])

# 2. im_item_extend — piece_pack vs pieces_per_pack
show_cols(c, "yamibuy_im", "im_item_extend", [
    "piece_pack",
    "pieces_per_pack",
    "need_minimal_exp",
])

# 3. wh_inbound_batch — EXP and in_dtm sanity check
show_cols(c, "yamibuy_wh", "wh_inbound_batch", [
    "qc_ExpiredDate",
    "in_dtm",
    "inbound_number",
])

# 4. Sample: MIN fc_exp_date per SKU (top 5 SKUs with EXP)
print("\n" + "="*60)
print("SAMPLE: MIN(fc_exp_date) per SKU from FC lots (top 5)")
print("="*60)
with c.cursor() as cur:
    cur.execute("""
        SELECT li.item_number,
               MIN(CASE WHEN ib.qc_ExpiredDate > 0
                        THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END) AS min_fc_exp,
               COUNT(DISTINCT li.location_no) AS loc_count
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
        LEFT JOIN yamibuy_wh.wh_lot_master lm ON lm.lot_id=li.lot_id
        LEFT JOIN yamibuy_wh.wh_inbound_batch ib ON ib.inbound_number=lm.source_number
        WHERE li.warehouse_number='002' AND li.quantity>0
          AND wsl.location_type IN (3,4)
          AND li.location_no NOT LIKE 'S%'
          AND li.location_no NOT LIKE 'DC%'
        GROUP BY li.item_number
        HAVING min_fc_exp IS NOT NULL
        ORDER BY min_fc_exp ASC
        LIMIT 5
    """)
    rows = cur.fetchall()
    for r in rows:
        print(f"  item={r[0]}  min_fc_exp={r[1]}  loc_count={r[2]}")

# 5. Sample: receive_time vs in_dtm for LEGACY vs normal lots
print("\n" + "="*60)
print("SAMPLE: receive_time vs in_dtm for LEGACY (lot_id=1) and normal lots")
print("="*60)
with c.cursor() as cur:
    cur.execute("""
        SELECT lm.lot_id,
               CASE WHEN lm.lot_id=1 THEN 'LEGACY' ELSE 'NORMAL' END AS lot_type,
               DATE(FROM_UNIXTIME(lm.receive_time)) AS receive_date,
               lm.source_number
        FROM yamibuy_wh.wh_lot_master lm
        WHERE lm.lot_id <= 20
        LIMIT 10
    """)
    rows = cur.fetchall()
    for r in rows:
        print(f"  lot_id={r[0]}  type={r[1]}  receive_date={r[2]}  in_dtm_date={r[3]}")

c.close()
print("\n✅ Done.")
