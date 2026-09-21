"""
Check ALL wh_location_size rows to see full description/value combinations
"""
import os, pymysql, pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path("..") / ".env")
conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
)

print("=== ALL wh_location_size rows ===")
with conn.cursor() as cur:
    cur.execute("""
        SELECT size_id, value, storage_type, description,
               width_inch, height_inch, depth_inch,
               volume, fill_rate, storage_form
        FROM yamibuy_wh.wh_location_size
        ORDER BY storage_type, description, size_id
    """)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
df = pd.DataFrame(rows, columns=cols)
pd.set_option("display.max_columns", None)
pd.set_option("display.width", 220)
print(df.to_string(index=False))
print(f"\nTotal: {len(df)} rows")

conn.close()
