"""
V4 DB Schema Validator
======================
Uses the actual MySQL connection (from .env) to run SHOW FULL COLUMNS
on the tables with NEW fields needed for v4.

Tables already validated in v3 are skipped.
Run from v4_DC_allocation/ or the project root.

Usage:
    python check_db_schema.py
"""
import os, sys
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env (check current dir then parent) ─────────────────
for env_path in [Path(".") / ".env", Path("..") / ".env"]:
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        print(f"Loaded .env from: {env_path.resolve()}")
        break

import pymysql

def connect():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"),
        port=3306,
        charset="utf8mb4",
        connect_timeout=10,
    )

def show_columns(conn, db, table, fields_of_interest):
    print(f"\n{'='*60}")
    print(f"TABLE: {db}.{table}")
    print('='*60)
    with conn.cursor() as cur:
        cur.execute(f"SHOW FULL COLUMNS FROM `{db}`.`{table}`")
        rows = cur.fetchall()
        cols_desc = [d[0] for d in cur.description]
    # Build dict: field_name → row
    col_map = {r[0]: dict(zip(cols_desc, r)) for r in rows}
    for f in fields_of_interest:
        if f in col_map:
            c = col_map[f]
            print(f"  ✅ {f}")
            print(f"     Type    : {c.get('Type','?')}")
            print(f"     Null    : {c.get('Null','?')}")
            print(f"     Default : {c.get('Default','?')}")
            print(f"     Comment : {c.get('Comment','(none)') or '(none)'}")
        else:
            print(f"  ❌ {f} — NOT FOUND in {db}.{table}")
            print(f"     Available: {sorted(col_map.keys())}")

# ── Tables already validated in v3 (SKIP) ────────────────────
# wh_storage_location_item, wh_inventory_transaction,
# wh_storage_location (location_type/size_id), wh_location_size,
# im_item (business_type), wh_lot_inventory, wh_lot_master,
# wh_inbound_batch (qc_ExpiredDate), im_item_extend (need_minimal_exp),
# xysc_wearhouse_goods (volume)

print("\n🔍 V4 DB Schema Validation")
print("   Only checking NEW fields not validated in v3.\n")
print("   Connection: MYSQL_HOST=" + os.getenv("MYSQL_HOST","(not set)"))

try:
    conn = connect()
    print(f"✅ MySQL connected to {os.getenv('MYSQL_HOST')}\n")
except Exception as e:
    print(f"❌ MySQL connection failed: {e}")
    sys.exit(1)

# 1. im_item_extend — NEW field: case_qty (Stock loc rounding in SFC→FC Req 7.5)
show_columns(conn, "yamibuy_im", "im_item_extend", [
    "need_minimal_exp",  # v3-validated, sanity check
    "case_qty",          # NEW for v4 — Stock location case rounding
    "minimal_exp",
    "inbound_point",
    "trash_point",
])

# 2. wh_inbound_batch — NEW field: in_dtm (inv_age for P3 priority Req 5.5, 6.7)
show_columns(conn, "yamibuy_wh", "wh_inbound_batch", [
    "qc_ExpiredDate",    # v3-validated, sanity check
    "in_dtm",            # NEW for v4 — inventory age (recv_date)
    "inbound_number",
])

# 3. wh_storage_location — confirm zone_id=2 → SFC (used in ideal script)
show_columns(conn, "yamibuy_wh", "wh_storage_location", [
    "location_type",     # v3-validated: 3=Stock, 4=Bin
    "size_id",           # v3-validated: NOT IN(13,33)
    "zone_id",           # NEW verify: zone_id=2 matches S%/DC% prefix?
    "location_no",
])

conn.close()
print("\n\n✅ Done. Update db-enum-values SKILL.md with any new confirmed values.")
print("   Path: c:\\DEV\\wecom-bot-kiro-cli\\workspace\\skills\\db-enum-values\\SKILL.md")
