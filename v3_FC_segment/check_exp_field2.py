import os, pymysql, pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
)

# wh_item_setting columns
print("=== wh_item_setting columns ===")
with conn.cursor() as cur:
    cur.execute("""
        SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA='yamibuy_wh' AND TABLE_NAME='wh_item_setting'
        ORDER BY ORDINAL_POSITION
    """)
    for r in cur.fetchall():
        print(f"  {r[0]:<40} {r[1]:<20} {r[2]}")

# sample rows
print("\n=== wh_item_setting sample (wh=002) ===")
with conn.cursor() as cur:
    cur.execute("""
        SELECT * FROM yamibuy_wh.wh_item_setting
        WHERE warehouse_number='002' LIMIT 5
    """)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    print(df.to_string())

# wh_item_expire_dtm columns
print("\n=== wh_item_expire_dtm columns ===")
with conn.cursor() as cur:
    cur.execute("""
        SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA='yamibuy_wh' AND TABLE_NAME='wh_item_expire_dtm'
        ORDER BY ORDINAL_POSITION
    """)
    for r in cur.fetchall():
        print(f"  {r[0]:<40} {r[1]:<20} {r[2]}")

# sample
print("\n=== wh_item_expire_dtm sample ===")
with conn.cursor() as cur:
    cur.execute("SELECT * FROM yamibuy_wh.wh_item_expire_dtm LIMIT 5")
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    print(df.to_string())

conn.close()
