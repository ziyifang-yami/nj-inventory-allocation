"""
Check im tables for expiry date management fields
"""
import os, pymysql, pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
)

# 1. Check columns in im_item that relate to expiry
print("=== im_item columns with 'exp' or 'shelf' or 'date' ===")
with conn.cursor() as cur:
    cur.execute("""
        SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'yamibuy_im'
          AND TABLE_NAME = 'im_item'
          AND (LOWER(COLUMN_NAME) LIKE '%exp%'
            OR LOWER(COLUMN_NAME) LIKE '%shelf%'
            OR LOWER(COLUMN_NAME) LIKE '%life%'
            OR LOWER(COLUMN_NAME) LIKE '%date%'
            OR LOWER(COLUMN_NAME) LIKE '%expire%'
            OR LOWER(COLUMN_NAME) LIKE '%period%')
        ORDER BY COLUMN_NAME
    """)
    rows = cur.fetchall()
for r in rows:
    print(f"  {r[0]:<40} {r[1]:<20} {r[2]}")

# 2. Check im_item_attribute or similar tables
print("\n=== Tables in yamibuy_im with 'item' in name ===")
with conn.cursor() as cur:
    cur.execute("""
        SELECT TABLE_NAME
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = 'yamibuy_im'
          AND TABLE_NAME LIKE '%item%'
        ORDER BY TABLE_NAME
    """)
    rows = cur.fetchall()
for r in rows:
    print(f"  {r[0]}")

# 3. Check wh tables for item attribute / shelf life
print("\n=== yamibuy_wh tables with 'item' or 'attribute' ===")
with conn.cursor() as cur:
    cur.execute("""
        SELECT TABLE_NAME
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = 'yamibuy_wh'
          AND (TABLE_NAME LIKE '%item%' OR TABLE_NAME LIKE '%attribute%'
               OR TABLE_NAME LIKE '%exp%' OR TABLE_NAME LIKE '%shelf%')
        ORDER BY TABLE_NAME
    """)
    rows = cur.fetchall()
for r in rows:
    print(f"  {r[0]}")

conn.close()
