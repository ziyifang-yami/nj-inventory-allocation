"""
Pull location data for TOP (DC_TO_FC) and TAIL (FC_TO_DC) SKUs only
- TOP: currently in SFC, need to know what FC space they'll need when moved back
- TAIL: currently in FC, need to know what FC space they'll release when moved out
Output: results/sku_locations_top_tail_YYYYMMDD_HHMM.csv
"""
import os, pymysql, pandas as pd, glob, numpy as np
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
OUT = Path(__file__).parent / "results"

# ── Get TOP and TAIL SKU lists ────────────────────────────────
seg_files = sorted(glob.glob(str(OUT / "segment_sku_detail_*.csv")))
df_seg = pd.read_csv(seg_files[-1])
df_seg["item_number"] = df_seg["item_number"].astype(str)
for c in ["fc_qty","sfc_qty","avg_daily_orders"]:
    df_seg[c] = pd.to_numeric(df_seg[c], errors="coerce").fillna(0)

df_seg = df_seg[(df_seg["fc_qty"]>0)|(df_seg["sfc_qty"]>0)].copy()

AOD_TOP, OL_TOP, OL_TAIL = 21, 30, 8
def classify(r):
    if r.active_order_days >= AOD_TOP and r.order_lines_30d >= OL_TOP: return "TOP"
    if r.order_lines_30d < OL_TAIL: return "TAIL"
    return "MIDDLE"
df_seg["seg"] = df_seg.apply(classify, axis=1)
df_seg["fc_d2s"] = np.where(df_seg["avg_daily_orders"]>0,
                             df_seg["fc_qty"]/df_seg["avg_daily_orders"], np.nan)

# TOP: needs DC_TO_FC (FC D2S < 28, has SFC stock)
top_skus = df_seg[
    (df_seg["seg"]=="TOP") &
    (df_seg["avg_daily_orders"]>0) &
    ((df_seg["fc_d2s"].isna()) | (df_seg["fc_d2s"]<28)) &
    (df_seg["sfc_qty"]>0)
]["item_number"].tolist()

# TAIL: needs FC_TO_DC (has FC stock)
tail_skus = df_seg[
    (df_seg["seg"]=="TAIL") &
    (df_seg["fc_qty"]>0)
]["item_number"].tolist()

print(f"TOP SKUs to transfer: {len(top_skus):,}")
print(f"TAIL SKUs to transfer: {len(tail_skus):,}")

# ── Query locations ───────────────────────────────────────────
conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
)

SQL_TMPL = """
SELECT
    wit.item_number,
    wit.location_no,
    CASE WHEN wsl.location_type = 4 THEN 'Bin'
         WHEN wsl.location_type = 3 THEN 'Stock' END AS loc_type,
    wls.storage_type,
    wls.description   AS size_description,
    wls.volume        AS loc_volume_in3,
    wls.fill_rate,
    wls.volume * wls.fill_rate AS allowable_volume_in3,
    CASE
        WHEN wit.location_no NOT LIKE 'S%%'
         AND wit.location_no NOT LIKE 'DC%%'
         AND wit.location_no NOT LIKE 'K%%'
        THEN 'FC' ELSE 'SFC'
    END AS zone,
    SUM(wit.quantity) AS qty
FROM yamibuy_wh.wh_inventory_transaction wit
JOIN yamibuy_wh.wh_storage_location wsl
    ON wsl.location_no = wit.location_no AND wsl.warehouse_number = wit.warehouse_number
JOIN yamibuy_wh.wh_location_size wls ON wls.size_id = wsl.size_id
WHERE wit.warehouse_number = '002'
  AND wit.quantity > 0
  AND wsl.location_type IN (3, 4)
  AND wsl.size_id NOT IN (13, 33)
  AND wit.item_number IN ({placeholders})
GROUP BY wit.item_number, wit.location_no, wsl.location_type,
         wls.storage_type, wls.description, wls.volume, wls.fill_rate, zone
HAVING SUM(wit.quantity) > 0
"""

def fetch_chunks(skus, label):
    chunks = [skus[i:i+500] for i in range(0, len(skus), 500)]
    rows, cols = [], None
    with conn.cursor() as cur:
        for idx, chunk in enumerate(chunks):
            ph = ",".join(["%s"] * len(chunk))
            cur.execute(SQL_TMPL.format(placeholders=ph), chunk)
            if cols is None:
                cols = [d[0] for d in cur.description]
            rows.extend(cur.fetchall())
            print(f"  {label} batch {idx+1}/{len(chunks)}: {len(rows):,} rows", flush=True)
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame()

print("\nFetching TOP locations (currently in SFC)...")
df_top = fetch_chunks(top_skus, "TOP")
if not df_top.empty:
    df_top["seg"] = "TOP"; df_top["action"] = "DC_TO_FC"

print("\nFetching TAIL locations (currently in FC)...")
df_tail = fetch_chunks(tail_skus, "TAIL")
if not df_tail.empty:
    df_tail["seg"] = "TAIL"; df_tail["action"] = "FC_TO_DC"

conn.close()

print("\nFetching MIDDLE FC→DC locations (excess inventory in FC)...")
# MIDDLE FC→DC: SKUs where FC D2S > 30, need to evict excess
import numpy as np as np2
df_seg_mid = df_seg.copy()
df_seg_mid["fc_d2s"] = np.where(df_seg_mid["avg_daily_orders"]>0,
                                 df_seg_mid["fc_qty"]/df_seg_mid["avg_daily_orders"], np.nan)
df_seg_mid["excess"] = df_seg_mid["fc_qty"] - np.where(
    df_seg_mid["avg_daily_orders"]>0,
    np.round(df_seg_mid["avg_daily_orders"]*30), df_seg_mid["fc_qty"])

mid_out_skus = df_seg_mid[
    (df_seg_mid["seg"]=="MIDDLE") &
    (df_seg_mid["avg_daily_orders"]>0) &
    (~df_seg_mid["fc_d2s"].isna()) &
    (df_seg_mid["fc_d2s"]>30) &
    (df_seg_mid["excess"]>5) &
    (df_seg_mid["fc_qty"]>0)
]["item_number"].tolist()
print(f"MIDDLE FC→DC SKUs: {len(mid_out_skus):,}")

df_mid = fetch_chunks(mid_out_skus, "MIDDLE_FC_TO_DC")
if not df_mid.empty:
    df_mid["seg"] = "MIDDLE"
    df_mid["action"] = "FC_TO_DC"

df = pd.concat([df_top, df_tail, df_mid], ignore_index=True)
df["item_number"] = df["item_number"].astype(str)
for c in ["qty","loc_volume_in3","fill_rate","allowable_volume_in3"]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

print(f"\nTotal: {len(df):,} rows, {df['item_number'].nunique():,} SKUs")

print("\n=== TOP (DC_TO_FC) — current SFC locations to be vacated ===")
if not df_top.empty:
    s = df_top[df_top["zone"]=="SFC"].groupby(["loc_type","storage_type"]).agg(
        sku_count=("item_number","nunique"),
        loc_count=("location_no","nunique"),
        total_qty=("qty","sum"),
    ).reset_index()
    print(s.to_string(index=False))

print("\n=== TAIL (FC_TO_DC) — current FC locations to be released ===")
if not df_tail.empty:
    s = df_tail[df_tail["zone"]=="FC"].groupby(["loc_type","storage_type"]).agg(
        sku_count=("item_number","nunique"),
        loc_count=("location_no","nunique"),
        total_qty=("qty","sum"),
    ).reset_index()
    print(s.to_string(index=False))

ts = datetime.now().strftime("%Y%m%d_%H%M")
out_path = OUT / f"sku_locations_top_tail_{ts}.csv"
df.to_csv(out_path, index=False)
print(f"\n✅ Saved: {out_path}  ({len(df):,} rows)")
