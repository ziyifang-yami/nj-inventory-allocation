"""
验证 wh_storage_location.g_volume 是否准确
方法：用 wh_inventory_transaction 的实际库存件数 × SKU 体积，对比 g_volume
"""
import os, pymysql, pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
)

def q(sql, label=""):
    if label: print(f"\n{'='*60}\n{label}\n{'='*60}")
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    print(df.to_string(index=False))
    return df

# 1. 比较 g_volume 与 实际库存件数×SKU体积（type=3 Stock，FC）
q("""
SELECT
  COUNT(*) AS locations,
  ROUND(SUM(wsl.g_volume)) AS sum_g_volume,
  ROUND(SUM(wit.quantity * wwg.volume)) AS sum_calc_volume,
  ROUND(SUM(wsl.g_volume) / NULLIF(SUM(wit.quantity * wwg.volume), 0), 2) AS ratio
FROM yamibuy_wh.wh_storage_location wsl
JOIN yamibuy_wh.wh_inventory_transaction wit
  ON wit.location_no = wsl.location_no
 AND wit.warehouse_number = wsl.warehouse_number
JOIN yamibuy_im.im_item ii ON ii.item_number = wit.item_number
JOIN yamibuy_master.xysc_wearhouse_goods wwg ON wwg.goods_id = ii.goods_id
WHERE wsl.warehouse_number = '002'
  AND wsl.location_type = 3
  AND wsl.location_no NOT LIKE 'S%%'
  AND wit.quantity > 0
  AND wwg.volume > 0
""", "1. g_volume vs 实际计算体积（type=3 Stock FC）")

# 2. 同样验证 type=4 Bin
q("""
SELECT
  COUNT(*) AS locations,
  ROUND(SUM(wsl.g_volume)) AS sum_g_volume,
  ROUND(SUM(wit.quantity * wwg.volume)) AS sum_calc_volume,
  ROUND(SUM(wsl.g_volume) / NULLIF(SUM(wit.quantity * wwg.volume), 0), 2) AS ratio
FROM yamibuy_wh.wh_storage_location wsl
JOIN yamibuy_wh.wh_inventory_transaction wit
  ON wit.location_no = wsl.location_no
 AND wit.warehouse_number = wsl.warehouse_number
JOIN yamibuy_im.im_item ii ON ii.item_number = wit.item_number
JOIN yamibuy_master.xysc_wearhouse_goods wwg ON wwg.goods_id = ii.goods_id
WHERE wsl.warehouse_number = '002'
  AND wsl.location_type = 4
  AND wsl.location_no NOT LIKE 'S%%'
  AND wit.quantity > 0
  AND wwg.volume > 0
""", "2. g_volume vs 实际计算体积（type=4 Bin FC）")

# 3. 用实际计算的体积重新算利用率
q("""
SELECT
  wsl.location_type,
  ROUND(SUM(wit.quantity * wwg.volume))             AS actual_used_vol_in3,
  ROUND(SUM(ls.volume))                             AS total_capacity_in3,
  ROUND(SUM(wit.quantity * wwg.volume)
        / NULLIF(SUM(ls.volume), 0) * 100, 1)       AS actual_utilization_pct
FROM yamibuy_wh.wh_storage_location wsl
JOIN yamibuy_wh.wh_inventory_transaction wit
  ON wit.location_no = wsl.location_no
 AND wit.warehouse_number = wsl.warehouse_number
LEFT JOIN yamibuy_wh.wh_location_size ls ON ls.size_id = wsl.size_id
LEFT JOIN yamibuy_im.im_item ii ON ii.item_number = wit.item_number
LEFT JOIN yamibuy_master.xysc_wearhouse_goods wwg ON wwg.goods_id = ii.goods_id
WHERE wsl.warehouse_number = '002'
  AND wsl.location_type IN (3, 4)
  AND wsl.location_no NOT LIKE 'S%%'
  AND wit.quantity > 0
  AND wwg.volume > 0
  AND ls.volume > 0
GROUP BY wsl.location_type
""", "3. 实际计算体积的 FC 利用率")

# 4. SFC 对比
q("""
SELECT
  ROUND(SUM(wit.quantity * wwg.volume))             AS actual_used_vol_in3,
  ROUND(SUM(ls.volume))                             AS total_capacity_in3,
  ROUND(SUM(wit.quantity * wwg.volume)
        / NULLIF(SUM(ls.volume), 0) * 100, 1)       AS actual_utilization_pct
FROM yamibuy_wh.wh_storage_location wsl
JOIN yamibuy_wh.wh_inventory_transaction wit
  ON wit.location_no = wsl.location_no
 AND wit.warehouse_number = wsl.warehouse_number
LEFT JOIN yamibuy_wh.wh_location_size ls ON ls.size_id = wsl.size_id
LEFT JOIN yamibuy_im.im_item ii ON ii.item_number = wit.item_number
LEFT JOIN yamibuy_master.xysc_wearhouse_goods wwg ON wwg.goods_id = ii.goods_id
WHERE wsl.warehouse_number = '002'
  AND wsl.location_type = 4
  AND wsl.location_no LIKE 'S%%'
  AND wit.quantity > 0
  AND wwg.volume > 0
  AND ls.volume > 0
""", "4. 实际计算体积的 SFC 利用率")

conn.close()
