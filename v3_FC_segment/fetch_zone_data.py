"""
拉取 NJ wh=002 zone 维度库存数据
FC  = zone_id=1 (non-S prefix, location_type 3/4)
SFC = zone_id=2 (S prefix, location_type 4)

输出: results/zone_inventory.csv
字段: item_number, zone, location_no, location_type, qty, unit_volume_cbm
"""
import os, pymysql, pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

OUT = Path(__file__).parent / "results" / "zone_inventory.csv"

print("Connecting to MySQL...")
conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
)

print("Querying zone inventory (FC + SFC, location_type 3/4, 1P+FBY)...")
with conn.cursor() as cur:
    cur.execute("""
        SELECT
            wit.item_number,
            CASE WHEN wsl.zone_id = 2 THEN 'SFC' ELSE 'FC' END AS zone,
            wit.location_no,
            wsl.location_type,
            wit.quantity AS qty,
            COALESCE(wwg.volume, 0) AS unit_volume_cbm
        FROM yamibuy_wh.wh_inventory_transaction wit
        JOIN yamibuy_wh.wh_storage_location wsl
          ON wsl.location_no = wit.location_no
         AND wsl.warehouse_number = wit.warehouse_number
        LEFT JOIN yamibuy_im.im_item ii
          ON ii.item_number = wit.item_number
        LEFT JOIN yamibuy_master.xysc_wearhouse_goods wwg
          ON wwg.goods_id = ii.goods_id
        WHERE wit.warehouse_number = '002'
          AND wit.quantity > 0
          AND wsl.location_type IN (3, 4)
          AND wsl.zone_id IN (1, 2)
          AND (wit.item_number LIKE '1%%' OR wit.item_number LIKE '5%%')
    """)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
conn.close()

df = pd.DataFrame(rows, columns=cols)
df["qty"]            = pd.to_numeric(df["qty"],            errors="coerce").fillna(0)
df["unit_volume_cbm"]= pd.to_numeric(df["unit_volume_cbm"],errors="coerce").fillna(0)
df["total_volume"]   = df["qty"] * df["unit_volume_cbm"]

print(f"  → {len(df):,} rows")
print(f"  FC  rows: {(df['zone']=='FC').sum():,}   SFC rows: {(df['zone']=='SFC').sum():,}")

df.to_csv(OUT, index=False)
print(f"✅ Saved: {OUT}")
