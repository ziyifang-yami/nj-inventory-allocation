"""
用 yamibuy_wh.wh_inventory_transaction 查 location_no 前缀，
确认 S开头 = SFC 库位的逻辑，并对比当前代码用 warehouse_number 的差异
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

# 1. wh_inventory_transaction 的库位前缀 × warehouse 分布
q("""
SELECT warehouse_number,
       LEFT(location_no, 1)        AS loc_prefix,
       COUNT(DISTINCT location_no) AS distinct_locs,
       COUNT(DISTINCT item_number) AS distinct_skus,
       SUM(quantity)               AS total_qty
FROM yamibuy_wh.wh_inventory_transaction
WHERE warehouse_number IN ('001', '002')
  AND quantity > 0
GROUP BY warehouse_number, loc_prefix
ORDER BY warehouse_number, total_qty DESC
""", "1. wh_inventory_transaction: warehouse × location_no 前缀分布")

# 2. 在 zone 001 里，S开头 vs 非S开头的库存量
q("""
SELECT 
  warehouse_number,
  CASE WHEN location_no LIKE 'S%' THEN 'S-prefix (SFC区域)' ELSE 'non-S (FC区域)' END AS zone_type,
  COUNT(DISTINCT location_no) AS locations,
  COUNT(DISTINCT item_number) AS skus,
  SUM(quantity)               AS qty
FROM yamibuy_wh.wh_inventory_transaction
WHERE warehouse_number IN ('001', '002')
  AND quantity > 0
GROUP BY warehouse_number, zone_type
ORDER BY warehouse_number, zone_type
""", "2. S前缀 vs 非S前缀 库存量对比")

# 3. S前缀库位的样本（看看长什么样）
q("""
SELECT warehouse_number, location_no, COUNT(DISTINCT item_number) AS skus, SUM(quantity) AS qty
FROM yamibuy_wh.wh_inventory_transaction
WHERE location_no LIKE 'S%' AND quantity > 0
GROUP BY warehouse_number, location_no
ORDER BY qty DESC
LIMIT 15
""", "3. S前缀库位样本（前15，按库存量）")

# 4. 非S前缀库位在 wh=002 里的情况（这些按旧逻辑算成 sfc，但实际是 fc）
q("""
SELECT location_no, COUNT(DISTINCT item_number) AS skus, SUM(quantity) AS qty
FROM yamibuy_wh.wh_inventory_transaction
WHERE warehouse_number = '002'
  AND location_no NOT LIKE 'S%'
  AND quantity > 0
GROUP BY location_no
ORDER BY qty DESC
LIMIT 15
""", "4. wh=002 中非S前缀库位（被旧逻辑错误归为SFC的FC库存）")

# 5. 核心：按正确逻辑 重算每个 item 的 fc_qty / sfc_qty
#    FC = wh 001 非S开头  +  wh 002 非S开头
#    SFC = wh 001 S开头   +  wh 002 S开头
q("""
SELECT
  SUM(CASE WHEN location_no NOT LIKE 'S%' THEN quantity ELSE 0 END) AS fc_qty_correct,
  SUM(CASE WHEN location_no     LIKE 'S%' THEN quantity ELSE 0 END) AS sfc_qty_correct,
  -- 旧逻辑（inventory_transaction 按 warehouse_number）
  (SELECT SUM(available_qty+reserved_qty+allocated_qty+receive_qty)
   FROM yamibuy_inventory.inventory_transaction
   WHERE warehouse_number='001'
     AND (available_qty+reserved_qty+allocated_qty+receive_qty)>0) AS fc_qty_old,
  (SELECT SUM(available_qty+reserved_qty+allocated_qty+receive_qty)
   FROM yamibuy_inventory.inventory_transaction
   WHERE warehouse_number='002'
     AND (available_qty+reserved_qty+allocated_qty+receive_qty)>0) AS sfc_qty_old
FROM yamibuy_wh.wh_inventory_transaction
WHERE warehouse_number IN ('001','002') AND quantity > 0
""", "5. 总量对比：旧逻辑（wh_number） vs 正确逻辑（location_no前缀）")

# 6. 差异：wh=001 里的 S开头库位（这些被旧逻辑算成FC，但实际是SFC）
q("""
SELECT SUM(quantity) AS wh001_S_qty, COUNT(DISTINCT item_number) AS wh001_S_skus
FROM yamibuy_wh.wh_inventory_transaction
WHERE warehouse_number = '001' AND location_no LIKE 'S%' AND quantity > 0
""", "6. wh=001 里 S开头库位的库存（旧逻辑误算为FC的SFC库存）")

q("""
SELECT SUM(quantity) AS wh002_nonS_qty, COUNT(DISTINCT item_number) AS wh002_nonS_skus
FROM yamibuy_wh.wh_inventory_transaction
WHERE warehouse_number = '002' AND location_no NOT LIKE 'S%' AND quantity > 0
""", "7. wh=002 里非S开头库位的库存（旧逻辑误算为SFC的FC库存）")

conn.close()
