"""
找 sellable bin 和 stock 库位的过滤条件
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

# 1. wh_storage_location 的列结构（库位主表，有 location_type）
q("""
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'yamibuy_wh'
  AND TABLE_NAME = 'wh_storage_location'
ORDER BY ORDINAL_POSITION
""", "1. wh_storage_location 列结构")

# 2. location_type 的取值和含义
q("""
SELECT location_type,
       COUNT(*) AS location_count,
       COUNT(DISTINCT location_no) AS distinct_locs
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
GROUP BY location_type
ORDER BY location_count DESC
""", "2. wh=002 的 location_type 取值分布")

# 3. 结合 wh_inventory_transaction 看各 location_type 的库存量
q("""
SELECT wsl.location_type,
       wsl.location_no,
       COUNT(DISTINCT wit.item_number) AS skus,
       SUM(wit.quantity) AS qty
FROM yamibuy_wh.wh_inventory_transaction wit
JOIN yamibuy_wh.wh_storage_location wsl
  ON wsl.location_no = wit.location_no
  AND wsl.warehouse_number = wit.warehouse_number
WHERE wit.warehouse_number = '002'
  AND wit.quantity > 0
GROUP BY wsl.location_type, wsl.location_no
ORDER BY wsl.location_type, qty DESC
LIMIT 40
""", "3. 各 location_type 的库位样本（按库存量前40）")

# 4. 汇总各 location_type 的总 SKU 数和库存
q("""
SELECT wsl.location_type,
       COUNT(DISTINCT wit.item_number) AS skus,
       SUM(wit.quantity) AS qty,
       COUNT(DISTINCT wit.location_no) AS locations
FROM yamibuy_wh.wh_inventory_transaction wit
JOIN yamibuy_wh.wh_storage_location wsl
  ON wsl.location_no = wit.location_no
  AND wsl.warehouse_number = wit.warehouse_number
WHERE wit.warehouse_number = '002'
  AND wit.quantity > 0
GROUP BY wsl.location_type
ORDER BY qty DESC
""", "4. 各 location_type 汇总（wh=002）")

# 5. 有多少 wh_inventory_transaction 的 location_no 在 wh_storage_location 里找不到
q("""
SELECT COUNT(DISTINCT wit.item_number) AS skus_no_location_master,
       SUM(wit.quantity) AS qty_no_location_master
FROM yamibuy_wh.wh_inventory_transaction wit
LEFT JOIN yamibuy_wh.wh_storage_location wsl
  ON wsl.location_no = wit.location_no
  AND wsl.warehouse_number = wit.warehouse_number
WHERE wit.warehouse_number = '002'
  AND wit.quantity > 0
  AND wsl.location_no IS NULL
""", "5. 找不到 location_type 的库存（wh_storage_location 里没有对应记录）")

# 6. 找不到的那些 location_no 样本
q("""
SELECT wit.location_no,
       COUNT(DISTINCT wit.item_number) AS skus,
       SUM(wit.quantity) AS qty
FROM yamibuy_wh.wh_inventory_transaction wit
LEFT JOIN yamibuy_wh.wh_storage_location wsl
  ON wsl.location_no = wit.location_no
  AND wsl.warehouse_number = wit.warehouse_number
WHERE wit.warehouse_number = '002'
  AND wit.quantity > 0
  AND wsl.location_no IS NULL
GROUP BY wit.location_no
ORDER BY qty DESC
LIMIT 20
""", "6. 找不到 location_type 的 location_no 样本")

conn.close()
