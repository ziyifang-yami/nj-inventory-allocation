"""验证 xysc_wearhouse_goods.volume 的单位"""
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

# 1. 查 wearhouse_goods 的列，看有没有单位说明
q("""
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'yamibuy_master'
  AND TABLE_NAME = 'xysc_wearhouse_goods'
  AND COLUMN_NAME IN ('volume','length','width','height','weight')
ORDER BY ORDINAL_POSITION
""", "1. xysc_wearhouse_goods 体积相关字段定义")

# 2. 取几个 TOP 爆品的实际体积数值 + 商品名，手动对照
q("""
SELECT i.item_number, g.goods_name,
       wwg.length, wwg.width, wwg.height, wwg.volume,
       wwg.length * wwg.width * wwg.height AS calc_vol
FROM yamibuy_im.im_item i
JOIN yamibuy_master.xysc_wearhouse_goods wwg ON i.goods_id = wwg.goods_id
JOIN yamibuy_master.xysc_goods g ON g.goods_id = i.goods_id
WHERE i.item_number IN (
    '1016005261', '1016009641', '1016200821',
    '1016074121', '1016262751', '1016277641'
)
  AND wwg.volume > 0
ORDER BY wwg.volume DESC
""", "2. 爆品体积样本（对比 length*width*height vs volume）")

# 3. 体积分布（整体）
q("""
SELECT
  ROUND(LOG10(volume)) AS log10_vol,
  COUNT(*) AS cnt,
  MIN(volume) AS min_v,
  MAX(volume) AS max_v,
  AVG(volume) AS avg_v
FROM yamibuy_master.xysc_wearhouse_goods
WHERE volume > 0
GROUP BY log10_vol
ORDER BY log10_vol
""", "3. volume 数量级分布（log10）")

conn.close()
