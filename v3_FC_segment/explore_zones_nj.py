"""探索 NJ wh=002 的 zone 分布"""
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

# 1. zone_id 分布（wh_storage_location）
q("""
SELECT zone_id,
       COUNT(*) AS locations,
       COUNT(DISTINCT LEFT(location_no,1)) AS loc_prefixes
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND location_type IN (3, 4)
GROUP BY zone_id
ORDER BY zone_id
""", "1. zone_id 分布（location_type 3/4）")

# 2. zone_id 1 和 2 各有哪些库位前缀
q("""
SELECT zone_id, LEFT(location_no,1) AS prefix,
       location_type,
       COUNT(*) AS cnt
FROM yamibuy_wh.wh_storage_location
WHERE warehouse_number = '002'
  AND zone_id IN (1, 2)
  AND location_type IN (3, 4)
GROUP BY zone_id, prefix, location_type
ORDER BY zone_id, prefix
""", "2. Zone 1/2 的库位前缀")

# 3. zone 1/2 各有多少库存 SKU
q("""
SELECT wsl.zone_id,
       COUNT(DISTINCT wit.item_number) AS skus,
       SUM(wit.quantity) AS qty
FROM yamibuy_wh.wh_inventory_transaction wit
JOIN yamibuy_wh.wh_storage_location wsl
  ON wsl.location_no = wit.location_no
 AND wsl.warehouse_number = wit.warehouse_number
WHERE wit.warehouse_number = '002'
  AND wit.quantity > 0
  AND wsl.location_type IN (3, 4)
  AND wsl.zone_id IN (1, 2)
  AND (wit.item_number LIKE '1%%' OR wit.item_number LIKE '5%%')
GROUP BY wsl.zone_id
""", "3. Zone 1/2 库存 SKU 和数量")

# 4. 有多少 SKU 同时在 zone1 和 zone2
q("""
SELECT COUNT(*) AS skus_in_both
FROM (
    SELECT wit.item_number
    FROM yamibuy_wh.wh_inventory_transaction wit
    JOIN yamibuy_wh.wh_storage_location wsl
      ON wsl.location_no = wit.location_no AND wsl.warehouse_number = wit.warehouse_number
    WHERE wit.warehouse_number = '002' AND wit.quantity > 0
      AND wsl.location_type IN (3,4) AND wsl.zone_id = 1
      AND (wit.item_number LIKE '1%%' OR wit.item_number LIKE '5%%')
    INTERSECT
    SELECT wit.item_number
    FROM yamibuy_wh.wh_inventory_transaction wit
    JOIN yamibuy_wh.wh_storage_location wsl
      ON wsl.location_no = wit.location_no AND wsl.warehouse_number = wit.warehouse_number
    WHERE wit.warehouse_number = '002' AND wit.quantity > 0
      AND wsl.location_type IN (3,4) AND wsl.zone_id = 2
      AND (wit.item_number LIKE '1%%' OR wit.item_number LIKE '5%%')
) t
""", "4. 同时在 Zone1 & Zone2 的 SKU 数")

conn.close()
