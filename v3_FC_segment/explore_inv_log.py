"""
探索 inventory transaction log 表结构
"""
import os
import pymysql
import pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

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

# 1. 找 yamibuy_wh 里有 log/history/inbound 相关的表
q("""
SELECT TABLE_NAME, TABLE_ROWS
FROM information_schema.TABLES
WHERE TABLE_SCHEMA IN ('yamibuy_wh', 'yamibuy_inventory')
  AND (TABLE_NAME LIKE '%log%' OR TABLE_NAME LIKE '%history%'
       OR TABLE_NAME LIKE '%inbound%' OR TABLE_NAME LIKE '%receive%'
       OR TABLE_NAME LIKE '%receipt%' OR TABLE_NAME LIKE '%transaction%')
ORDER BY TABLE_SCHEMA, TABLE_NAME
""", "1. 相关表列表")

# 2. wh_inventory_transaction_log 的列（如果存在）
q("""
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'yamibuy_wh'
  AND TABLE_NAME = 'wh_inventory_transaction_log'
ORDER BY ORDINAL_POSITION
LIMIT 20
""", "2. wh_inventory_transaction_log 列结构")

# 3. yamibuy_inventory 里的 transaction 相关表列结构
q("""
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'yamibuy_inventory'
  AND TABLE_NAME = 'inventory_transaction_log'
ORDER BY ORDINAL_POSITION
LIMIT 20
""", "3. inventory_transaction_log 列结构")

conn.close()
