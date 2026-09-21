"""
容积计算脚本 — NJ wh=002
============================
基于提供的两个 SQL：
  1. 库位汇总（location-level）→ 总容量 / 已用 / 剩余 / 利用率
  2. SKU 明细（sku-level）     → 每个 SKU 占了哪些库位、占了多少体积

输出：
  results/volume_location_summary.csv   — 库位汇总
  results/volume_sku_detail.csv         — SKU 明细
  results/volume_segment_summary.csv    — 按 segment 汇总容积
"""

import os, pymysql, pandas as pd
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
OUT = Path(__file__).parent / "results"

def connect():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
    )

print("=" * 60)
print("V3 Volume Analysis — NJ wh=002")
print(f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ══════════════════════════════════════════════════════════════
# SQL 1: 库位汇总（修复重复行，聚合 wh_inventory_transaction）
# 每行 = 一个库位的汇总情况
# ══════════════════════════════════════════════════════════════
SQL_LOCATION = """
SELECT
    wsl.warehouse_number,
    CASE WHEN wsl.location_type = 4 THEN 'Bin'
         WHEN wsl.location_type = 3 THEN 'Stock' END AS location_type,
    wsl.location_no,
    wsl.max_sku,
    wsl.size_id,
    wls.value       AS size_name,
    wls.storage_type,
    wls.volume      AS location_volume,
    wls.fill_rate,
    wls.volume * wls.fill_rate AS allowable_volume,
    COUNT(DISTINCT item.item_number)        AS sku_number,
    IFNULL(SUM(item.quantity), 0)           AS sku_units,
    IFNULL(SUM(item.volume * item.quantity), 0) AS sku_total_volume
FROM yamibuy_wh.wh_storage_location wsl
LEFT JOIN yamibuy_wh.wh_location_size wls ON wls.size_id = wsl.size_id
LEFT JOIN (
    /* SKU × 库位，先把 inventory_transaction 聚合避免重复 */
    SELECT
        wsli.warehouse_number,
        wsli.location_no,
        wsli.item_number,
        wwg.volume,
        wit.quantity
    FROM yamibuy_wh.wh_storage_location_item wsli
    LEFT JOIN yamibuy_im.im_item ii
        ON ii.item_number = wsli.item_number
    LEFT JOIN yamibuy_master.xysc_wearhouse_goods wwg
        ON wwg.goods_id = ii.goods_id
    LEFT JOIN (
        SELECT item_number, warehouse_number, location_no,
               SUM(quantity) AS quantity
        FROM yamibuy_wh.wh_inventory_transaction
        GROUP BY item_number, warehouse_number, location_no
    ) wit ON wit.item_number  = wsli.item_number
          AND wit.warehouse_number = wsli.warehouse_number
          AND wit.location_no = wsli.location_no
    WHERE wit.quantity IS NOT NULL AND wit.quantity != 0
) item ON item.location_no       = wsl.location_no
       AND item.warehouse_number  = wsl.warehouse_number
WHERE wsl.warehouse_number = '002'
  AND wsl.location_type IN (3, 4)
  AND wsl.size_id NOT IN (13, 33)
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
"""

# ══════════════════════════════════════════════════════════════
# SQL 2: SKU 明细（每行 = 一个 SKU 在一个库位）
# ══════════════════════════════════════════════════════════════
SQL_SKU_DETAIL = """
SELECT
    wsl.warehouse_number,
    CASE WHEN wsl.location_type = 4 THEN 'Bin'
         WHEN wsl.location_type = 3 THEN 'Stock' END AS location_type,
    wsl.location_no,
    wsl.max_sku,
    wsl.size_id,
    wls.value       AS size_name,
    wls.storage_type,
    wls.volume      AS location_volume,
    wls.fill_rate,
    wls.volume * wls.fill_rate AS allowable_volume,
    item.item_number,
    item.title,
    item.quantity   AS item_units,
    IFNULL(item.volume, 0)                  AS item_volume,
    IFNULL(item.volume * item.quantity, 0)  AS item_total_volume,
    item.domain,
    item.category,
    item.subcategory,
    item.segment
FROM yamibuy_wh.wh_storage_location wsl
LEFT JOIN yamibuy_wh.wh_location_size wls ON wls.size_id = wsl.size_id
LEFT JOIN (
    SELECT
        wsli.warehouse_number,
        wsli.location_no,
        wsli.item_number,
        isd.title,
        wwg.volume,
        wit.quantity,
        c1.category_name AS domain,
        c2.category_name AS category,
        c3.category_name AS subcategory,
        CASE WHEN pps.segment = 6 THEN '0-Star'
             WHEN pps.segment = 1 THEN '1-Hit'
             WHEN pps.segment = 2 THEN '2-Head'
             WHEN pps.segment = 3 THEN '3-Middle'
             WHEN pps.segment = 4 THEN '4-Longtail'
             ELSE 'TBD' END AS segment
    FROM yamibuy_wh.wh_storage_location_item wsli
    LEFT JOIN yamibuy_im.im_item ii
        ON ii.item_number = wsli.item_number
    LEFT JOIN yamibuy_master.xysc_wearhouse_goods wwg
        ON wwg.goods_id = ii.goods_id
    /* 聚合 inventory_transaction 避免重复 */
    LEFT JOIN (
        SELECT item_number, warehouse_number, location_no,
               SUM(quantity) AS quantity
        FROM yamibuy_wh.wh_inventory_transaction
        GROUP BY item_number, warehouse_number, location_no
    ) wit ON wit.item_number      = wsli.item_number
          AND wit.warehouse_number = wsli.warehouse_number
          AND wit.location_no      = wsli.location_no
    LEFT JOIN yamibuy_im.im_item_short_description isd
        ON isd.item_number = wsli.item_number
       AND isd.language_id = 'zh_CN'
    LEFT JOIN yamibuy_im.im_category c3 ON c3.category_id = ii.category_id
    LEFT JOIN yamibuy_im.im_category c2 ON c2.category_id = c3.parent_category_id
    LEFT JOIN yamibuy_im.im_category c1 ON c1.category_id = c2.parent_category_id
    LEFT JOIN yamibuy_bigdata.po_product_segment pps
        ON pps.warehouse_number = wsli.warehouse_number
       AND pps.item_number      = ii.item_number
    WHERE wit.quantity IS NOT NULL AND wit.quantity != 0
      AND (wsli.item_number LIKE '1%%' OR wsli.item_number LIKE '5%%')
) item ON item.location_no      = wsl.location_no
       AND item.warehouse_number = wsl.warehouse_number
WHERE wsl.warehouse_number = '002'
  AND wsl.location_type IN (3, 4)
  AND wsl.size_id NOT IN (13, 33)
  AND item.item_number IS NOT NULL
"""

# ── Run ────────────────────────────────────────────────────────
conn = connect()

print("  [MySQL] Location summary...", flush=True)
with conn.cursor() as cur:
    cur.execute(SQL_LOCATION)
    cols = [d[0] for d in cur.description]
    df_loc = pd.DataFrame(cur.fetchall(), columns=cols)
print(f"    → {len(df_loc):,} locations")

print("  [MySQL] SKU detail...", flush=True)
with conn.cursor() as cur:
    cur.execute(SQL_SKU_DETAIL)
    cols = [d[0] for d in cur.description]
    df_sku = pd.DataFrame(cur.fetchall(), columns=cols)
conn.close()
print(f"    → {len(df_sku):,} sku-location rows")

# ── Cast numerics ──────────────────────────────────────────────
for col in ["location_volume","fill_rate","allowable_volume","sku_units","sku_total_volume"]:
    df_loc[col] = pd.to_numeric(df_loc[col], errors="coerce").fillna(0)
for col in ["location_volume","fill_rate","allowable_volume","item_units","item_volume","item_total_volume"]:
    df_sku[col] = pd.to_numeric(df_sku[col], errors="coerce").fillna(0)

# ── Add FC/SFC zone ────────────────────────────────────────────
def zone(loc):
    loc = str(loc)
    if loc.startswith("S"):   return "SFC"
    if loc.startswith("DC"):  return "SFC"
    if loc.startswith("K"):   return "SFC"
    return "FC"

def zone_type(row):
    loc  = str(row["location_no"])
    ltype = str(row["location_type"])
    if loc.startswith("S"):              return "SFC_Bin"
    if loc.startswith("DC"):             return "SFC_Stock"
    if loc.startswith("K"):              return "SFC_Bin_KYT"
    if ltype in ("Bin",  "4"):           return "FC_Bin"
    if ltype in ("Stock","3"):           return "FC_Stock"
    return "FC_Other"

df_loc["zone"]      = df_loc["location_no"].apply(zone)
df_loc["zone_type"] = df_loc.apply(zone_type, axis=1)
df_sku["zone"]      = df_sku["location_no"].apply(zone)
df_sku["zone_type"] = df_sku.apply(zone_type, axis=1)

# ══════════════════════════════════════════════════════════════
# Summary 1: FC vs SFC 总容量利用率
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("LOCATION VOLUME SUMMARY (FC vs SFC)")
print("=" * 60)

cap_summary = df_loc.groupby(["zone","zone_type","location_type"]).agg(
    total_locs        = ("location_no",       "count"),
    empty_locs        = ("sku_number",         lambda x: (x == 0).sum()),
    total_capacity    = ("location_volume",    "sum"),
    allowable_cap     = ("allowable_volume",   "sum"),
    used_volume       = ("sku_total_volume",   "sum"),
    total_units       = ("sku_units",          "sum"),
).reset_index()
cap_summary["utilization_pct"] = (
    cap_summary["used_volume"] / cap_summary["allowable_cap"].replace(0, float("nan")) * 100
).round(1)
cap_summary["free_volume"] = cap_summary["allowable_cap"] - cap_summary["used_volume"]

print(cap_summary.to_string(index=False))

# ══════════════════════════════════════════════════════════════
# Summary 2: 按 segment 汇总 SKU 占用体积
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("VOLUME BY SEGMENT × ZONE")
print("=" * 60)

seg_summary = df_sku.groupby(["zone","zone_type","segment"]).agg(
    sku_count     = ("item_number",    "nunique"),
    total_units   = ("item_units",     "sum"),
    total_volume  = ("item_total_volume", "sum"),
).reset_index().sort_values(["zone","segment"])
print(seg_summary.to_string(index=False))

# ══════════════════════════════════════════════════════════════
# Summary 3: SKU 维度汇总（每个 SKU 总占用体积）
# ══════════════════════════════════════════════════════════════
sku_summary = df_sku.groupby(["item_number","zone","zone_type","segment"]).agg(
    loc_count     = ("location_no",      "nunique"),
    total_units   = ("item_units",        "sum"),
    item_volume   = ("item_volume",       "max"),   # 单件体积
    total_volume  = ("item_total_volume", "sum"),
).reset_index()

# ── Save ───────────────────────────────────────────────────────
ts = datetime.now().strftime("%Y%m%d_%H%M")

df_loc.to_csv(OUT / f"volume_location_summary_{ts}.csv",  index=False)
df_sku.to_csv(OUT / f"volume_sku_detail_{ts}.csv",        index=False)
cap_summary.to_csv(OUT / f"volume_cap_summary_{ts}.csv",  index=False)
seg_summary.to_csv(OUT / f"volume_segment_summary_{ts}.csv", index=False)
sku_summary.to_csv(OUT / f"volume_sku_summary_{ts}.csv",  index=False)

print(f"\n✅ Saved to {OUT}:")
print(f"   volume_location_summary  ({len(df_loc):,} rows)")
print(f"   volume_sku_detail        ({len(df_sku):,} rows)")
print(f"   volume_cap_summary       ({len(cap_summary):,} rows)")
print(f"   volume_segment_summary   ({len(seg_summary):,} rows)")
print(f"   volume_sku_summary       ({len(sku_summary):,} rows)")
