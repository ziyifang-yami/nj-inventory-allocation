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

# DC 前缀
q("""
SELECT location_type, LEFT(location_no, 3) AS prefix3,
       COUNT(*) AS locations,
       SUM(total_inv) AS total_inv
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_no LIKE 'DC%%'
GROUP BY location_type, prefix3
ORDER BY total_inv DESC
""", "1. DC前缀库位分布")

# KYT 前缀（含 K 开头）
q("""
SELECT location_type, LEFT(location_no, 3) AS prefix3,
       COUNT(*) AS locations,
       SUM(total_inv) AS total_inv
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND (location_no LIKE 'KYT%%' OR location_no LIKE 'K%%')
GROUP BY location_type, prefix3
ORDER BY total_inv DESC
LIMIT 20
""", "2. KYT/K前缀库位分布")

# 容量
q("""
SELECT
  CASE WHEN location_no LIKE 'DC%%' THEN 'DC' ELSE 'KYT/K' END AS zone,
  location_type,
  COUNT(*) AS locations,
  SUM(ls.volume) AS total_cap_in3,
  SUM(ls.volume * ls.fill_rate) AS allowable_in3,
  SUM(wsl.total_inv) AS total_inv
FROM yamibuy_wh.wh_storage_location wsl
LEFT JOIN yamibuy_wh.wh_location_size ls ON ls.size_id = wsl.size_id
WHERE wsl.warehouse_number = '002'
  AND (wsl.location_no LIKE 'DC%%' OR wsl.location_no LIKE 'K%%')
  AND ls.volume > 0
GROUP BY zone, location_type
ORDER BY zone, location_type
""", "3. DC/K前缀容量汇总")

conn.close()
