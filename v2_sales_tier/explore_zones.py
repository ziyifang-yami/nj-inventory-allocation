"""
探索库位维度：找到 location_no 在哪张表，然后确定 FC vs SFC 的正确划分
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

# 1. inventory_transaction 的实际列名
q("""
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'yamibuy_inventory'
  AND TABLE_NAME = 'inventory_transaction'
ORDER BY ORDINAL_POSITION
""", "1. inventory_transaction 列结构")

# 2. inventory_transaction_ex 的列名
q("""
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'yamibuy_inventory'
  AND TABLE_NAME = 'inventory_transaction_ex'
ORDER BY ORDINAL_POSITION
""", "2. inventory_transaction_ex 列结构")

# 3. 找 yamibuy_inventory 里有 location 相关列的表
q("""
SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'yamibuy_inventory'
  AND (COLUMN_NAME LIKE '%location%' OR COLUMN_NAME LIKE '%zone%' OR COLUMN_NAME LIKE '%bin%')
ORDER BY TABLE_NAME, ORDINAL_POSITION
""", "3. yamibuy_inventory 里含 location/zone/bin 的列")

# 4. wh_inventory_transaction 的列（WMS库）
q("""
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'yamibuy_wh'
  AND TABLE_NAME = 'wh_inventory_transaction'
ORDER BY ORDINAL_POSITION
LIMIT 30
""", "4. wh_inventory_transaction 列结构")

# 5. wh_lr_product_location 的列（我们已经在用）
q("""
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'yamibuy_wh'
  AND TABLE_NAME = 'wh_lr_product_location'
ORDER BY ORDINAL_POSITION
LIMIT 30
""", "5. wh_lr_product_location 列结构")

conn.close()
