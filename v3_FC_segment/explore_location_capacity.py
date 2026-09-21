"""
探索 NJ wh=002 FC 库位容量（修正版）
不用 zone_id 过滤，改用 location_no 前缀区分 FC vs SFC
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

# 1. location_type=3 的 zone_id 分布（看看 stock 在哪个 zone）
q("""
SELECT zone_id,
       LEFT(location_no, 1) AS prefix,
       COUNT(*) AS locations,
       SUM(total_inv) AS total_inv
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_type = 3
GROUP BY zone_id, prefix
ORDER BY total_inv DESC
""", "1. location_type=3 (Stock) 的 zone_id 分布")

# 2. 所有 type，按 zone_id 汇总
q("""
SELECT zone_id,
       location_type,
       COUNT(*) AS locations,
       SUM(total_inv) AS total_inv
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_type IN (3, 4)
GROUP BY zone_id, location_type
ORDER BY zone_id, location_type
""", "2. 全部 type 3/4 按 zone_id 分布")

# 3. 正确口径：FC = 非S前缀，SFC = S前缀
# 按 FC/SFC 统计 type 3 和 type 4 的容量和利用率
q("""
SELECT
  CASE WHEN wsl.location_no LIKE 'S%%' THEN 'SFC' ELSE 'FC' END AS zone,
  wsl.location_type,
  COUNT(*)                                                       AS total_locs,
  SUM(CASE WHEN wsl.total_inv = 0 THEN 1 ELSE 0 END)            AS empty_locs,
  SUM(wsl.total_inv)                                             AS total_inv_qty,
  ROUND(SUM(wsl.g_volume))                                       AS total_used_vol_in3,
  ROUND(SUM(ls.volume))                                          AS total_capacity_in3,
  ROUND(SUM(wsl.g_volume) / NULLIF(SUM(ls.volume), 0) * 100, 1) AS utilization_pct,
  ROUND(SUM(GREATEST(ls.volume - wsl.g_volume, 0)))              AS total_free_vol_in3
FROM yamibuy_wh.wh_storage_location wsl
LEFT JOIN yamibuy_wh.wh_location_size ls ON ls.size_id = wsl.size_id
WHERE wsl.warehouse_number = '002'
  AND wsl.location_type IN (3, 4)
  AND ls.volume > 0
GROUP BY zone, wsl.location_type
ORDER BY zone, wsl.location_type
""", "3. FC vs SFC 容量利用率（按 location_no 前缀，type 3/4）")

# 4. FC type=3 各 size 规格
q("""
SELECT wsl.size_id,
       ls.value AS size_name,
       ls.width_inch, ls.height_inch, ls.depth_inch,
       ls.volume AS capacity_in3,
       COUNT(*) AS locations,
       SUM(wsl.total_inv) AS total_inv,
       SUM(CASE WHEN wsl.total_inv = 0 THEN 1 ELSE 0 END) AS empty_locs,
       ROUND(SUM(wsl.g_volume)) AS used_vol_in3,
       ROUND(SUM(GREATEST(ls.volume - wsl.g_volume, 0))) AS free_vol_in3
FROM yamibuy_wh.wh_storage_location wsl
LEFT JOIN yamibuy_wh.wh_location_size ls ON ls.size_id = wsl.size_id
WHERE wsl.warehouse_number = '002'
  AND wsl.location_type = 3
  AND wsl.location_no NOT LIKE 'S%%'
  AND ls.volume > 0
GROUP BY wsl.size_id, ls.value, ls.width_inch, ls.height_inch, ls.depth_inch, ls.volume
ORDER BY locations DESC
""", "4. FC Stock (type=3) 各 size 规格详情")

# 5. FC type=4 各 size 规格（top 10）
q("""
SELECT wsl.size_id,
       ls.value AS size_name,
       ls.width_inch, ls.height_inch, ls.depth_inch,
       ls.volume AS capacity_in3,
       COUNT(*) AS locations,
       SUM(CASE WHEN wsl.total_inv = 0 THEN 1 ELSE 0 END) AS empty_locs,
       ROUND(SUM(wsl.g_volume)) AS used_vol_in3,
       ROUND(SUM(GREATEST(ls.volume - wsl.g_volume, 0))) AS free_vol_in3
FROM yamibuy_wh.wh_storage_location wsl
LEFT JOIN yamibuy_wh.wh_location_size ls ON ls.size_id = wsl.size_id
WHERE wsl.warehouse_number = '002'
  AND wsl.location_type = 4
  AND wsl.location_no NOT LIKE 'S%%'
  AND ls.volume > 0
GROUP BY wsl.size_id, ls.value, ls.width_inch, ls.height_inch, ls.depth_inch, ls.volume
ORDER BY locations DESC
LIMIT 15
""", "5. FC Bin (type=4) 各 size 规格 top15")

conn.close()
