"""验证 xysc_wearhouse_goods.volume 单位，以及与库位 g_volume 的一致性"""
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

# 1. 商品尺寸字段注释
q("""
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'yamibuy_master'
  AND TABLE_NAME = 'xysc_wearhouse_goods'
  AND COLUMN_NAME IN ('length','width','height','volume','weight')
""", "1. xysc_wearhouse_goods 尺寸字段注释")

# 2. 取几个具体商品对比：length×width×height 和 volume 的关系
q("""
SELECT goods_name, length, width, height,
       length*width*height AS lxwxh,
       volume,
       ROUND(volume / NULLIF(length*width*height, 0), 2) AS ratio
FROM yamibuy_master.xysc_wearhouse_goods
WHERE length > 0 AND width > 0 AND height > 0 AND volume > 0
  AND length < 100 AND width < 100 AND height < 100
ORDER BY RAND()
LIMIT 15
""", "2. length×width×height vs volume 比值（随机15个）")

# 3. 具体商品验证：法式软面包（刚才看到的 341 in³）
# length=8.90, width=13.25, height=2.90 → 341 in³
# 如果单位是 cm：8.9×13.25×2.9 = 342 cm³ = 0.000342 m³ ≠ 341 in³
# 8.9 in = 22.6 cm, 所以单位确实是 inches
q("""
SELECT g.goods_name, g.length, g.width, g.height, g.volume,
       g.length * g.width * g.height AS lxwxh,
       ROUND(g.volume / NULLIF(g.length*g.width*g.height, 0), 3) AS ratio,
       -- 如果 length/width/height 是 inch，换算成 cm
       ROUND(g.length * 2.54, 1) AS len_cm,
       ROUND(g.width  * 2.54, 1) AS wid_cm,
       ROUND(g.height * 2.54, 1) AS hgt_cm,
       ROUND(g.length * g.width * g.height * 16.387, 0) AS vol_cm3
FROM yamibuy_master.xysc_wearhouse_goods g
JOIN yamibuy_im.im_item i ON i.goods_id = g.goods_id
WHERE i.item_number IN ('1016009641','1016005261','1016074121','1016200821')
ORDER BY g.volume DESC
""", "3. 已知爆品尺寸换算验证（是否 inches）")

# 4. 验证 g_volume = qty × sku_volume 的准确性（用单一 item 库位）
q("""
SELECT wsl.location_no,
       wit.item_number,
       wit.quantity,
       wwg.volume AS sku_vol,
       wit.quantity * wwg.volume AS calc_vol,
       wsl.g_volume,
       wsl.g_volume - wit.quantity * wwg.volume AS diff,
       wsl.total_inv
FROM yamibuy_wh.wh_storage_location wsl
JOIN yamibuy_wh.wh_inventory_transaction wit
  ON wit.location_no = wsl.location_no AND wit.warehouse_number = wsl.warehouse_number
JOIN yamibuy_im.im_item ii ON ii.item_number = wit.item_number
JOIN yamibuy_master.xysc_wearhouse_goods wwg ON wwg.goods_id = ii.goods_id
WHERE wsl.warehouse_number = '002'
  AND wsl.item_count = 1
  AND wsl.location_type IN (3, 4)
  AND wsl.location_no NOT LIKE 'S%%'
  AND wit.quantity > 0
  AND wwg.volume > 0
  AND ABS(wsl.g_volume - wit.quantity * wwg.volume) < 1
LIMIT 10
""", "4. 单品种库位：g_volume ≈ qty × sku_vol 的样本验证")

conn.close()
