"""
SKU 库位占用率分析
- 每个 SKU 占几个库位（FC / SFC 分开）
- 占用体积 vs 库位总容量
- 按 segment 汇总
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

# 1. 整体：每个 SKU 占几个库位（FC type 3/4，非S前缀）
q("""
SELECT
  loc_count_bucket,
  COUNT(*) AS sku_count
FROM (
  SELECT wit.item_number,
         COUNT(DISTINCT wit.location_no) AS loc_count,
         CASE
           WHEN COUNT(DISTINCT wit.location_no) = 1 THEN '1 loc'
           WHEN COUNT(DISTINCT wit.location_no) BETWEEN 2 AND 3 THEN '2-3 locs'
           WHEN COUNT(DISTINCT wit.location_no) BETWEEN 4 AND 5 THEN '4-5 locs'
           WHEN COUNT(DISTINCT wit.location_no) BETWEEN 6 AND 10 THEN '6-10 locs'
           ELSE '11+ locs'
         END AS loc_count_bucket
  FROM yamibuy_wh.wh_inventory_transaction wit
  JOIN yamibuy_wh.wh_storage_location wsl
    ON wsl.location_no = wit.location_no AND wsl.warehouse_number = wit.warehouse_number
  WHERE wit.warehouse_number = '002'
    AND wit.quantity > 0
    AND wsl.location_type IN (3, 4)
    AND wsl.location_no NOT LIKE 'S%%'
    AND (wit.item_number LIKE '1%%' OR wit.item_number LIKE '5%%')
  GROUP BY wit.item_number
) t
GROUP BY loc_count_bucket
ORDER BY MIN(CASE loc_count_bucket
  WHEN '1 loc'    THEN 1
  WHEN '2-3 locs' THEN 2
  WHEN '4-5 locs' THEN 3
  WHEN '6-10 locs'THEN 4
  ELSE 5 END)
""", "1. FC：每个 SKU 占用库位数分布")

# 2. 同上，SFC
q("""
SELECT
  loc_count_bucket,
  COUNT(*) AS sku_count
FROM (
  SELECT wit.item_number,
         COUNT(DISTINCT wit.location_no) AS loc_count,
         CASE
           WHEN COUNT(DISTINCT wit.location_no) = 1 THEN '1 loc'
           WHEN COUNT(DISTINCT wit.location_no) BETWEEN 2 AND 3 THEN '2-3 locs'
           WHEN COUNT(DISTINCT wit.location_no) BETWEEN 4 AND 5 THEN '4-5 locs'
           WHEN COUNT(DISTINCT wit.location_no) BETWEEN 6 AND 10 THEN '6-10 locs'
           ELSE '11+ locs'
         END AS loc_count_bucket
  FROM yamibuy_wh.wh_inventory_transaction wit
  JOIN yamibuy_wh.wh_storage_location wsl
    ON wsl.location_no = wit.location_no AND wsl.warehouse_number = wit.warehouse_number
  WHERE wit.warehouse_number = '002'
    AND wit.quantity > 0
    AND wsl.location_type = 4
    AND wsl.location_no LIKE 'S%%'
    AND (wit.item_number LIKE '1%%' OR wit.item_number LIKE '5%%')
  GROUP BY wit.item_number
) t
GROUP BY loc_count_bucket
ORDER BY MIN(CASE loc_count_bucket
  WHEN '1 loc'    THEN 1
  WHEN '2-3 locs' THEN 2
  WHEN '4-5 locs' THEN 3
  WHEN '6-10 locs'THEN 4
  ELSE 5 END)
""", "2. SFC：每个 SKU 占用库位数分布")

# 3. FC Stock (type=3) 库位深度：一个库位放几种 SKU
q("""
SELECT
  item_count,
  COUNT(*) AS location_count,
  SUM(total_inv) AS total_qty
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_type = 3
  AND location_no NOT LIKE 'S%%'
GROUP BY item_count
ORDER BY item_count
""", "3. FC Stock 库位深度（每个库位放几种 SKU）")

# 4. FC Bin (type=4) 库位深度
q("""
SELECT
  item_count,
  COUNT(*) AS location_count,
  SUM(total_inv) AS total_qty
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_type = 4
  AND location_no NOT LIKE 'S%%'
GROUP BY item_count
ORDER BY item_count
""", "4. FC Bin 库位深度（每个库位放几种 SKU）")

# 5. SFC Bin 库位深度
q("""
SELECT
  item_count,
  COUNT(*) AS location_count,
  SUM(total_inv) AS total_qty
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_type = 4
  AND location_no LIKE 'S%%'
GROUP BY item_count
ORDER BY item_count
""", "5. SFC Bin 库位深度（每个库位放几种 SKU）")

# 6. FC SKU 占用体积分布（商品体积 × 库存件数）
q("""
SELECT
  vol_bucket,
  COUNT(*) AS sku_count,
  SUM(qty) AS total_qty,
  ROUND(SUM(total_vol_in3)) AS total_vol_in3
FROM (
  SELECT wit.item_number,
         SUM(wit.quantity) AS qty,
         SUM(wit.quantity * wwg.volume) AS total_vol_in3,
         CASE
           WHEN SUM(wit.quantity * wwg.volume) < 1000      THEN '<1K in³'
           WHEN SUM(wit.quantity * wwg.volume) < 10000     THEN '1K-10K'
           WHEN SUM(wit.quantity * wwg.volume) < 100000    THEN '10K-100K'
           WHEN SUM(wit.quantity * wwg.volume) < 1000000   THEN '100K-1M'
           ELSE '1M+'
         END AS vol_bucket
  FROM yamibuy_wh.wh_inventory_transaction wit
  JOIN yamibuy_wh.wh_storage_location wsl
    ON wsl.location_no = wit.location_no AND wsl.warehouse_number = wit.warehouse_number
  JOIN yamibuy_im.im_item ii ON ii.item_number = wit.item_number
  JOIN yamibuy_master.xysc_wearhouse_goods wwg ON wwg.goods_id = ii.goods_id
  WHERE wit.warehouse_number = '002'
    AND wit.quantity > 0
    AND wsl.location_type IN (3, 4)
    AND wsl.location_no NOT LIKE 'S%%'
    AND (wit.item_number LIKE '1%%' OR wit.item_number LIKE '5%%')
    AND wwg.volume > 0
  GROUP BY wit.item_number
) t
GROUP BY vol_bucket
ORDER BY MIN(total_vol_in3)
""", "6. FC SKU 总占用体积分布")

conn.close()
