"""
确认 sellable bin (type=3/4) 和 stock (type=?) 的具体含义
通过库位编号模式和 wh_lr_product_location 里的注释推断
"""
import os
import pymysql
import pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST", "rds.g3.yamibuy.net"),
    user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASS"),
    port=3306, charset="utf8mb4",
)

def q(sql, label=""):
    if label:
        print(f"\n{'='*60}\n{label}\n{'='*60}")
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    print(df.to_string(index=False))
    return df

# 1. 主力 location_type（3和4）的库位编号样本
q("""
SELECT location_type, location_no, size_id, restockable, zone_id
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_type IN (3, 4)
ORDER BY location_type, location_no
LIMIT 30
""", "1. location_type 3 和 4 的库位编号样本")

# 2. 查一下 wh_storage_location_type 表（如果存在）
q("""
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'yamibuy_wh'
  AND TABLE_NAME LIKE '%location_type%'
""", "2. 查找 location_type 字典表")

# 3. 直接查 wh_storage_location_type 或类似字典表
q("""
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'yamibuy_wh'
  AND (TABLE_NAME LIKE '%type%' OR TABLE_NAME LIKE '%dict%' OR TABLE_NAME LIKE '%config%')
ORDER BY TABLE_NAME
""", "3. yamibuy_wh 里可能的字典/配置表")

# 4. type 3 里的 DC/B/C 前缀库位 vs type 4 的库位
q("""
SELECT location_type,
       LEFT(location_no, 1) AS loc_prefix,
       COUNT(*) AS cnt,
       MIN(location_no) AS sample_min,
       MAX(location_no) AS sample_max
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_type IN (3, 4, 5, 6)
GROUP BY location_type, loc_prefix
ORDER BY location_type, cnt DESC
""", "4. type 3/4/5/6 的库位前缀分布")

# 5. wh_lr_product_location 只对哪些 location_type 有记录
# （这个表是 WMS 在管理的 sellable bin）
q("""
SELECT wsl.location_type,
       COUNT(DISTINCT lpl.item_number) AS wms_managed_skus,
       SUM(wit.quantity) AS qty
FROM yamibuy_wh.wh_lr_product_location lpl
JOIN yamibuy_wh.wh_inventory_transaction wit
  ON wit.item_number = lpl.item_number
  AND wit.warehouse_number = lpl.warehouse_number
JOIN yamibuy_wh.wh_storage_location wsl
  ON wsl.location_no = wit.location_no
  AND wsl.warehouse_number = wit.warehouse_number
WHERE lpl.warehouse_number = '002'
  AND lpl.is_deleted = 0
  AND lpl.status = 1
  AND wit.quantity > 0
GROUP BY wsl.location_type
ORDER BY qty DESC
""", "5. WMS 在管 SKU (wh_lr_product_location) 对应的 location_type")

# 6. 关键：type 3 和 type 4 各自的库位编号模式及注释
q("""
SELECT location_type,
       location_no,
       size_id,
       zone_id,
       restockable,
       comment
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_type = 30
LIMIT 5
""", "6. location_type=30 (LQ) 详情")

q("""
SELECT location_type, location_no, comment, zone_id
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_type IN (1, 2, 22)
LIMIT 10
""", "7. type 1/2/22 详情（收货区相关）")

conn.close()
