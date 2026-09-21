"""
分析 FC / SFC / Overlap SKU 分布
按 Units 和 Volume (CBM) 两个维度统计
Run: python analyze_overlap.py
"""
import pymysql, os
import pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── 读取 allocation 结果 ─────────────────────────────────
df = pd.read_csv("allocation_result.csv")
print(f"Total SKUs in result: {len(df):,}")

# ── 从 MySQL 取 SKU 体积 ─────────────────────────────────
print("\n[MySQL] Loading SKU volume from xysc_wearhouse_goods...", flush=True)
conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST", "rds.g3.yamibuy.net"),
    user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASS"),
    port=3306, charset="utf8mb4",
)

# Batch query to avoid IN clause size limit
item_numbers = df["item_number"].astype(str).tolist()
batch_size   = 2000
vol_rows     = []
for i in range(0, len(item_numbers), batch_size):
    batch = tuple(item_numbers[i:i+batch_size])
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ii.item_number,
                   COALESCE(wwg.volume, 0)  AS volume_cbm,
                   COALESCE(wwg.weight, 0)  AS weight_g
            FROM yamibuy_im.im_item ii
            LEFT JOIN yamibuy_master.xysc_wearhouse_goods wwg ON wwg.goods_id = ii.goods_id
            WHERE ii.item_number IN %s
        """, (batch,))
        vol_rows.extend(cur.fetchall())
conn.close()

vol_df = pd.DataFrame(vol_rows, columns=["item_number", "volume_cbm", "weight_g"])
vol_df["item_number"] = vol_df["item_number"].astype(str)
df["item_number"]     = df["item_number"].astype(str)
print(f"  → {len(vol_df):,} SKUs with volume data")

# ── Merge volume ─────────────────────────────────────────
df = df.merge(vol_df, on="item_number", how="left")
df["volume_cbm"] = pd.to_numeric(df["volume_cbm"], errors="coerce").fillna(0)
df["fc_qty"]     = pd.to_numeric(df["fc_qty"],     errors="coerce").fillna(0)
df["sfc_qty"]    = pd.to_numeric(df["sfc_qty"],    errors="coerce").fillna(0)

# ── Classify location ────────────────────────────────────
# FC only:  fc_qty > 0 AND sfc_qty = 0
# SFC only: fc_qty = 0 AND sfc_qty > 0
# Overlap:  fc_qty > 0 AND sfc_qty > 0

df["location_type"] = "Unknown"
df.loc[(df["fc_qty"] > 0) & (df["sfc_qty"] == 0), "location_type"] = "FC Only"
df.loc[(df["fc_qty"] == 0) & (df["sfc_qty"] > 0), "location_type"] = "SFC Only"
df.loc[(df["fc_qty"] > 0) & (df["sfc_qty"] > 0),  "location_type"] = "Overlap"

# ── Compute volume metrics ───────────────────────────────
df["fc_volume_cbm"]  = df["fc_qty"]  * df["volume_cbm"]
df["sfc_volume_cbm"] = df["sfc_qty"] * df["volume_cbm"]
df["total_units"]    = df["fc_qty"]  + df["sfc_qty"]
df["total_volume"]   = df["fc_volume_cbm"] + df["sfc_volume_cbm"]

# ════════════════════════════════════════════════════════
# 1. Overall FC / SFC / Overlap breakdown
# ════════════════════════════════════════════════════════
print("\n" + "="*65)
print("1. FC / SFC / OVERLAP — Overall")
print("="*65)
loc_summary = df.groupby("location_type").agg(
    sku_count    = ("item_number",     "count"),
    fc_units     = ("fc_qty",          "sum"),
    sfc_units    = ("sfc_qty",         "sum"),
    total_units  = ("total_units",     "sum"),
    fc_vol_cbm   = ("fc_volume_cbm",   "sum"),
    sfc_vol_cbm  = ("sfc_volume_cbm",  "sum"),
    total_vol    = ("total_volume",    "sum"),
).reset_index()
loc_summary["fc_vol_cbm"]  = loc_summary["fc_vol_cbm"].round(0)
loc_summary["sfc_vol_cbm"] = loc_summary["sfc_vol_cbm"].round(0)
loc_summary["total_vol"]   = loc_summary["total_vol"].round(0)
print(loc_summary.to_string(index=False))

# Total row
print("\n--- TOTAL ---")
total = {
    "sku_count":   df["item_number"].nunique(),
    "fc_units":    df["fc_qty"].sum(),
    "sfc_units":   df["sfc_qty"].sum(),
    "total_units": df["total_units"].sum(),
    "fc_vol_cbm":  round(df["fc_volume_cbm"].sum(), 0),
    "sfc_vol_cbm": round(df["sfc_volume_cbm"].sum(), 0),
    "total_vol":   round(df["total_volume"].sum(), 0),
}
for k, v in total.items():
    print(f"  {k:20s}: {v:>15,.0f}")

# ════════════════════════════════════════════════════════
# 2. Breakdown by group_tier
# ════════════════════════════════════════════════════════
print("\n" + "="*65)
print("2. By group_tier × location_type — Units")
print("="*65)
tier_units = df.groupby(["group_tier", "location_type"]).agg(
    sku_count = ("item_number", "count"),
    fc_units  = ("fc_qty",     "sum"),
    sfc_units = ("sfc_qty",    "sum"),
).reset_index().sort_values(["group_tier", "location_type"])
print(tier_units.to_string(index=False))

print("\n" + "="*65)
print("3. By group_tier × location_type — Volume (CBM)")
print("="*65)
tier_vol = df.groupby(["group_tier", "location_type"]).agg(
    sku_count   = ("item_number",    "count"),
    fc_vol      = ("fc_volume_cbm",  "sum"),
    sfc_vol     = ("sfc_volume_cbm", "sum"),
    total_vol   = ("total_volume",   "sum"),
).round(1).reset_index().sort_values(["group_tier", "location_type"])
print(tier_vol.to_string(index=False))

# ════════════════════════════════════════════════════════
# 4. Overlap detail — how much is in both FC and SFC
# ════════════════════════════════════════════════════════
print("\n" + "="*65)
print("4. OVERLAP SKUs — Current distribution detail")
print("="*65)
overlap = df[df["location_type"] == "Overlap"].copy()
print(f"Total overlap SKUs: {len(overlap):,}")
print(f"FC units in overlap:  {overlap['fc_qty'].sum():>12,.0f}")
print(f"SFC units in overlap: {overlap['sfc_qty'].sum():>12,.0f}")
print(f"FC volume (CBM):      {overlap['fc_volume_cbm'].sum():>12,.1f}")
print(f"SFC volume (CBM):     {overlap['sfc_volume_cbm'].sum():>12,.1f}")
print()

# By group_tier
print("Overlap by group_tier:")
ov_tier = overlap.groupby("group_tier").agg(
    sku_count = ("item_number", "count"),
    fc_units  = ("fc_qty",      "sum"),
    sfc_units = ("sfc_qty",     "sum"),
    fc_vol    = ("fc_volume_cbm",  "sum"),
    sfc_vol   = ("sfc_volume_cbm", "sum"),
).round(1).reset_index()
print(ov_tier.to_string(index=False))

# ════════════════════════════════════════════════════════
# 5. Action summary with volume
# ════════════════════════════════════════════════════════
print("\n" + "="*65)
print("5. Allocation Action — Units + Volume impact")
print("="*65)
df["transfer_units"]  = df["transfer_qty"].abs()
df["transfer_vol"]    = df["transfer_units"] * df["volume_cbm"]

action_summary = df.groupby("action").agg(
    sku_count       = ("item_number",    "count"),
    transfer_units  = ("transfer_units", "sum"),
    transfer_vol    = ("transfer_vol",   "sum"),
).round(1).reset_index()
print(action_summary.to_string(index=False))
