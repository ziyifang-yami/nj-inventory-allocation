"""
1. 当前 allocation_result.csv 里 item_number 前缀分布
2. 确认 wh_inventory_transaction 里有没有 business_type / item_type 字段
3. 查 im_item 表确认 business_type 字段（1P vs FBY）
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

# 1. allocation_result 里 item_number 前缀分布
print("\n" + "="*60)
print("1. allocation_result.csv — item_number 前缀分布")
print("="*60)
result = pd.read_csv("allocation_result.csv")
result["prefix"] = result["item_number"].astype(str).str[0]
dist = result.groupby("prefix").agg(
    sku_count=("item_number", "count"),
    fc_qty=("fc_qty", "sum"),
    sfc_qty=("sfc_qty", "sum"),
).reset_index().sort_values("sku_count", ascending=False)
print(dist.to_string(index=False))
p1  = result[result["item_number"].astype(str).str.startswith("1")]
p5  = result[result["item_number"].astype(str).str.startswith("5")]
other = result[~result["item_number"].astype(str).str.startswith(("1","5"))]
print(f"\n  1开头（1P）:   {len(p1):>6,} SKU  fc={p1['fc_qty'].sum():>10,.0f}  sfc={p1['sfc_qty'].sum():>10,.0f}")
print(f"  5开头（FBY）:  {len(p5):>6,} SKU  fc={p5['fc_qty'].sum():>10,.0f}  sfc={p5['sfc_qty'].sum():>10,.0f}")
print(f"  其他前缀:      {len(other):>6,} SKU  fc={other['fc_qty'].sum():>10,.0f}  sfc={other['sfc_qty'].sum():>10,.0f}")

# 2. im_item 的列结构（找 business_type / item_type）
q("""
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'yamibuy_im'
  AND TABLE_NAME = 'im_item'
  AND (COLUMN_NAME LIKE '%business%' OR COLUMN_NAME LIKE '%type%' 
       OR COLUMN_NAME LIKE '%vendor%' OR COLUMN_NAME LIKE '%consign%'
       OR COLUMN_NAME LIKE '%fby%' OR COLUMN_NAME LIKE '%owner%')
ORDER BY ORDINAL_POSITION
""", "2. im_item 里 business_type 相关列")

# 3. business_type 的实际取值分布（wh=002 在库 SKU）
q("""
SELECT ii.business_type,
       COUNT(DISTINCT wit.item_number) AS sku_count,
       SUM(wit.quantity) AS total_qty
FROM yamibuy_wh.wh_inventory_transaction wit
JOIN yamibuy_im.im_item ii ON ii.item_number = wit.item_number
WHERE wit.warehouse_number = '002'
  AND wit.quantity > 0
GROUP BY ii.business_type
ORDER BY sku_count DESC
""", "3. wh=002 在库 SKU 的 business_type 分布")

# 4. 非 1/5 开头的 item_number 样本 + business_type
q("""
SELECT wit.item_number,
       LEFT(wit.item_number, 1) AS prefix,
       ii.business_type,
       SUM(wit.quantity) AS qty
FROM yamibuy_wh.wh_inventory_transaction wit
JOIN yamibuy_im.im_item ii ON ii.item_number = wit.item_number
WHERE wit.warehouse_number = '002'
  AND wit.quantity > 0
  AND wit.item_number NOT REGEXP '^[15]'
GROUP BY wit.item_number, prefix, ii.business_type
ORDER BY qty DESC
LIMIT 20
""", "4. 非1/5开头的 item_number 样本（wh=002）")

# 5. 确认：1开头=1P，5开头=FBY 的 business_type 对应关系
q("""
SELECT LEFT(ii.item_number, 1) AS prefix,
       ii.business_type,
       COUNT(DISTINCT ii.item_number) AS sku_count
FROM yamibuy_im.im_item ii
WHERE ii.item_number REGEXP '^[0-9]'
GROUP BY prefix, ii.business_type
ORDER BY prefix, sku_count DESC
LIMIT 30
""", "5. item_number 前缀 × business_type 对应关系")

conn.close()
