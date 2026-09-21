"""验证 g_volume 和 ls.volume 的单位"""
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

# 1. wh_storage_location 里 g_volume 的注释
q("""
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'yamibuy_wh'
  AND TABLE_NAME = 'wh_storage_location'
  AND COLUMN_NAME IN ('g_volume','max_volume','min_volume','g_weight','volume','size_id')
""", "1. wh_storage_location 体积相关字段注释")

# 2. wh_location_size 里 volume 的注释 + 单位换算验证
# 如果 width_inch × height_inch × depth_inch = volume，说明 volume 是 in³
q("""
SELECT size_id, value, width_inch, height_inch, depth_inch, volume,
       width_inch * height_inch * depth_inch AS calc_vol,
       ROUND(volume / NULLIF(width_inch * height_inch * depth_inch, 0), 2) AS ratio
FROM yamibuy_wh.wh_location_size
WHERE width_inch > 0 AND height_inch > 0 AND depth_inch > 0
LIMIT 15
""", "2. wh_location_size volume vs w×h×d（确认in³）")

# 3. 同一库位：g_volume vs 实际计算（件数 × SKU in³体积）
# 单独看几个库位的比值
q("""
SELECT
  wsl.location_no,
  wsl.location_type,
  wsl.size_id,
  ls.volume           AS loc_capacity_in3,
  wsl.g_volume        AS g_volume_raw,
  SUM(wit.quantity)   AS total_qty,
  SUM(wit.quantity * wwg.volume) AS calc_vol_in3,
  ROUND(wsl.g_volume / NULLIF(SUM(wit.quantity * wwg.volume), 0), 2) AS g_div_calc
FROM yamibuy_wh.wh_storage_location wsl
JOIN yamibuy_wh.wh_inventory_transaction wit
  ON wit.location_no = wsl.location_no AND wit.warehouse_number = wsl.warehouse_number
LEFT JOIN yamibuy_wh.wh_location_size ls ON ls.size_id = wsl.size_id
LEFT JOIN yamibuy_im.im_item ii ON ii.item_number = wit.item_number
LEFT JOIN yamibuy_master.xysc_wearhouse_goods wwg ON wwg.goods_id = ii.goods_id
WHERE wsl.warehouse_number = '002'
  AND wsl.location_type IN (3, 4)
  AND wsl.location_no NOT LIKE 'S%%'
  AND wit.quantity > 0
  AND wwg.volume > 0
  AND ls.volume > 0
  AND wsl.item_count = 1
GROUP BY wsl.location_no, wsl.location_type, wsl.size_id, ls.volume, wsl.g_volume
HAVING total_qty > 0
ORDER BY ABS(ROUND(wsl.g_volume / NULLIF(SUM(wit.quantity * wwg.volume), 0), 2) - 1)
LIMIT 20
""", "3. 单品种库位：g_volume vs 实际计算，比值分布")

conn.close()
