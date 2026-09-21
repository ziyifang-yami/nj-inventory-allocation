"""
诊断 SKU 维度问题：
1. item_number 是什么格式？有没有重复/异常值？
2. 库存数据里有哪些 fc_qty/sfc_qty 很小或者是 0 的情况？
3. allocation_result.csv 里 59,018 个 SKU 的分布是否合理？
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

# ── 1. item_number 格式检查：长度分布
q("""
SELECT LENGTH(item_number) AS id_len,
       COUNT(*) AS cnt
FROM yamibuy_inventory.inventory_transaction
WHERE warehouse_number IN ('001','002')
GROUP BY id_len
ORDER BY cnt DESC
LIMIT 10
""", "1. item_number 长度分布（inventory_transaction）")

# ── 2. 是否有非数字 item_number（例如带字母的 bundle/kit SKU）
q("""
SELECT 
  SUM(CASE WHEN item_number REGEXP '^[0-9]+$' THEN 1 ELSE 0 END) AS numeric_only,
  SUM(CASE WHEN item_number NOT REGEXP '^[0-9]+$' THEN 1 ELSE 0 END) AS has_alpha,
  COUNT(DISTINCT item_number) AS total_distinct
FROM yamibuy_inventory.inventory_transaction
WHERE warehouse_number IN ('001','002')
  AND (available_qty + reserved_qty + allocated_qty + receive_qty) > 0
""", "2. item_number 是否有非纯数字（bundle/kit等）")

# ── 3. 非纯数字的 item_number 样本
q("""
SELECT item_number, warehouse_number,
       available_qty, reserved_qty, allocated_qty, receive_qty
FROM yamibuy_inventory.inventory_transaction
WHERE warehouse_number IN ('001','002')
  AND (available_qty + reserved_qty + allocated_qty + receive_qty) > 0
  AND item_number NOT REGEXP '^[0-9]+$'
LIMIT 20
""", "3. 非纯数字 item_number 样本（前20条）")

# ── 4. 库存量极小的 SKU（fc_qty 在 1-5 之间），是否有大量噪音
print("\n" + "="*60)
print("4. 读 allocation_result.csv 分析 fc_qty 分布")
print("="*60)
result = pd.read_csv("allocation_result.csv")
print(f"总行数: {len(result):,}")
print(f"\nfc_qty 分布：")
bins = [0, 0, 1, 5, 10, 50, 100, 500, float('inf')]
labels = ['=0', '1', '2-5', '6-10', '11-50', '51-100', '101-500', '500+']
result['fc_bin'] = pd.cut(result['fc_qty'], bins=bins, labels=labels, right=True)
print(result['fc_bin'].value_counts().sort_index().to_string())

print(f"\nsfc_qty 分布：")
result['sfc_bin'] = pd.cut(result['sfc_qty'], bins=bins, labels=labels, right=True)
print(result['sfc_bin'].value_counts().sort_index().to_string())

# ── 5. fc_qty=0 的 SKU（只有 sfc_qty）的情况
fc_zero = result[result['fc_qty'] == 0]
print(f"\n5. fc_qty=0 的 SKU：{len(fc_zero):,} 个（只有 SFC 库存，没有 FC 库存）")
print(fc_zero['action'].value_counts().to_string())

# ── 6. wms_status=0 的分布
no_wms = result[result['wms_status'] == 0]
print(f"\n6. wms_status=0（无 WMS 记录）SKU：{len(no_wms):,} 个")
print(no_wms['action'].value_counts().to_string())
print(f"   fc_qty>0 其中: {(no_wms['fc_qty']>0).sum():,}")
print(f"   sfc_qty>0 其中: {(no_wms['sfc_qty']>0).sum():,}")

# ── 7. 按 group_tier + fc_qty/sfc_qty 大致分布
print("\n7. 各 tier 的平均 fc_qty / sfc_qty")
print(result.groupby('group_tier')[['fc_qty','sfc_qty']].agg(['mean','median','count']).round(1).to_string())

conn.close()
print("\n✅ 诊断完成")
