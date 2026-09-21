"""验证 SFC 是否有 Stock (type=3) 库位"""
import os, pymysql, pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
)

def q(sql, label=""):
    if label: print(f"\n{'='*55}\n{label}\n{'='*55}")
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    print(df.to_string(index=False))
    return df

# 1. wh=002 所有 location_type=3 (Stock) 的库位前缀分布
q("""
SELECT LEFT(location_no, 1) AS prefix,
       COUNT(*) AS locations,
       SUM(total_inv) AS total_inv
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_type = 3
GROUP BY prefix
ORDER BY total_inv DESC
""", "1. wh=002 Stock (type=3) 库位前缀分布")

# 2. S 前缀的库位有没有 type=3 的 Stock
q("""
SELECT location_type, COUNT(*) AS cnt, SUM(total_inv) AS total_inv
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_no LIKE 'S%%'
GROUP BY location_type
ORDER BY location_type
""", "2. S前缀库位的 location_type 分布")

# 3. 如果 SFC 有 Stock，容量是多少
q("""
SELECT
  wsl.location_type,
  COUNT(*) AS locations,
  SUM(ls.volume) AS total_capacity_in3,
  SUM(ls.volume * ls.fill_rate) AS allowable_in3
FROM yamibuy_wh.wh_storage_location wsl
LEFT JOIN yamibuy_wh.wh_location_size ls ON ls.size_id = wsl.size_id
WHERE wsl.warehouse_number = '002'
  AND wsl.location_no LIKE 'S%%'
  AND ls.volume > 0
GROUP BY wsl.location_type
""", "3. SFC 各 type 容量（如有 Stock 则显示）")

conn.close()
