"""
快速检查 FC / SFC 容量数据 — 含 description + fill_rate 细分
"""
import os, pymysql, pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
)

SQL = """
SELECT
    wsl.warehouse_number,
    CASE
        WHEN wsl.location_no NOT LIKE 'S%'
         AND wsl.location_no NOT LIKE 'DC%'
         AND wsl.location_no NOT LIKE 'K%'
        THEN 'FC'
        ELSE 'SFC'
    END AS zone,
    CASE WHEN wsl.location_type = 4 THEN 'Bin'
         WHEN wsl.location_type = 3 THEN 'Stock' END AS location_type,
    wls.storage_type,
    wls.description,
    wls.fill_rate,
    ROUND(SUM(wls.volume * wls.fill_rate), 0)              AS total_allowable_cap,
    COUNT(DISTINCT wsl.location_no)                        AS distinct_count_of_location,
    ROUND(IFNULL(SUM(inv.sku_vol), 0), 0)                  AS used_volume,
    ROUND(SUM(wls.volume * wls.fill_rate)
          - IFNULL(SUM(inv.sku_vol), 0), 0)                AS free_volume,
    ROUND(IFNULL(SUM(inv.sku_vol),0)
          / NULLIF(SUM(wls.volume * wls.fill_rate),0) * 100, 1) AS util_pct
FROM yamibuy_wh.wh_storage_location wsl
LEFT JOIN yamibuy_wh.wh_location_size wls
    ON wls.size_id = wsl.size_id
LEFT JOIN (
    SELECT wit.warehouse_number, wit.location_no,
           SUM(wwg.volume * wit.quantity) AS sku_vol
    FROM yamibuy_wh.wh_inventory_transaction wit
    JOIN yamibuy_im.im_item ii ON ii.item_number = wit.item_number
    JOIN yamibuy_master.xysc_wearhouse_goods wwg
        ON wwg.goods_id = ii.goods_id
    WHERE wit.warehouse_number = '002'
      AND wit.quantity > 0
    GROUP BY wit.warehouse_number, wit.location_no
) inv ON inv.warehouse_number = wsl.warehouse_number
     AND inv.location_no       = wsl.location_no
WHERE wsl.warehouse_number = '002'
  AND wsl.location_type IN (3, 4)
  AND wsl.size_id NOT IN (13, 33)
GROUP BY 1, 2, 3, 4, 5, 6
ORDER BY zone, location_type, storage_type, description, fill_rate
"""

with conn.cursor() as cur:
    cur.execute(SQL)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
conn.close()

df = pd.DataFrame(rows, columns=cols)
for c in ["total_allowable_cap","used_volume","free_volume"]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 220)
pd.set_option("display.float_format", "{:,.0f}".format)
print(df.to_string(index=False))

# Save for reference
df.to_csv("results/capacity_detail.csv", index=False)
print(f"\n✅ Saved: results/capacity_detail.csv  ({len(df)} rows)")

print("\n" + "="*70)
print("FC DETAIL")
print("="*70)
print(df[df["zone"]=="FC"].to_string(index=False))

print("\n" + "="*70)
print("SFC DETAIL")
print("="*70)
print(df[df["zone"]=="SFC"].to_string(index=False))

print("\n=== FC Summary by location_type ===")
fc_s = df[df["zone"]=="FC"].groupby("location_type")[
    ["total_allowable_cap","used_volume","free_volume","distinct_count_of_location"]].sum()
fc_s["util%"] = (fc_s["used_volume"]/fc_s["total_allowable_cap"]*100).round(1)
print(fc_s.to_string())

print("\n=== SFC Summary by location_type ===")
sfc_s = df[df["zone"]=="SFC"].groupby("location_type")[
    ["total_allowable_cap","used_volume","free_volume","distinct_count_of_location"]].sum()
sfc_s["util%"] = (sfc_s["used_volume"]/sfc_s["total_allowable_cap"]*100).round(1)
print(sfc_s.to_string())
