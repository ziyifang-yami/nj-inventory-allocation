"""
MIDDLE FC→DC Location/Batch Selection
======================================
Sheet rules:
1) No Exp + Multiple Locations → pick location(s) closest to transfer_qty
   (minimize splits, whole-location transfer preferred)
2) No Exp + Single Location    → keep 30D2S, transfer only excess qty
3) With Exp + Multiple Locs    → sort by exp_date DESC (furthest first → go to DC)
                                  FC keeps nearest-expiry stock
4) With Exp + Single Location  → keep 30D2S, transfer only excess qty

Output: results/middle_location_selection_YYYYMMDD_HHMM.csv
  Each row = one location to transfer, with:
    item_number, location_no, loc_type, qty_to_transfer, reason, rule_applied
"""
import os, pymysql, pandas as pd, glob, numpy as np
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
OUT = Path(__file__).parent / "results"

# ── Load segment data ─────────────────────────────────────────
seg_files = sorted(glob.glob(str(OUT / "segment_sku_detail_*.csv")))
df_seg = pd.read_csv(seg_files[-1])
df_seg["item_number"] = df_seg["item_number"].astype(str)
for c in ["fc_qty","sfc_qty","avg_daily_orders","unit_volume_cbm"]:
    df_seg[c] = pd.to_numeric(df_seg[c], errors="coerce").fillna(0)

# MIDDLE FC→DC candidates
df_seg["fc_d2s"] = np.where(df_seg["avg_daily_orders"]>0,
                              df_seg["fc_qty"]/df_seg["avg_daily_orders"], np.nan)
df_seg["keep_qty"]  = np.where(df_seg["avg_daily_orders"]>0,
                                np.round(df_seg["avg_daily_orders"]*30), df_seg["fc_qty"])
df_seg["excess_qty"] = (df_seg["fc_qty"] - df_seg["keep_qty"]).clip(lower=0)

mid_candidates = df_seg[
    (df_seg["segment"].apply(lambda x: str(x))) == "MIDDLE"  # use raw segment col
]

# Use classified seg
AOD_TOP, OL_TOP, OL_TAIL = 21, 30, 8
def classify(r):
    if r.active_order_days >= AOD_TOP and r.order_lines_30d >= OL_TOP: return "TOP"
    if r.order_lines_30d < OL_TAIL: return "TAIL"
    return "MIDDLE"
df_seg["seg"] = df_seg.apply(classify, axis=1)

mid_out = df_seg[
    (df_seg["seg"] == "MIDDLE") &
    (df_seg["avg_daily_orders"] > 0) &
    (~df_seg["fc_d2s"].isna()) &
    (df_seg["fc_d2s"] > 30) &
    (df_seg["excess_qty"] > 5) &
    (df_seg["fc_qty"] > 0)
].copy()
print(f"MIDDLE FC→DC candidates: {len(mid_out):,} SKUs, total excess: {mid_out['excess_qty'].sum():,.0f} units")

if len(mid_out) == 0:
    print("No MIDDLE FC→DC SKUs found.")
    exit(0)

# ── Query FC lot data for these SKUs ─────────────────────────
conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
)

skus = mid_out["item_number"].tolist()
SQL_LOT = """
SELECT
    li.item_number,
    li.location_no,
    CASE WHEN wsl.location_type = 4 THEN 'Bin'
         WHEN wsl.location_type = 3 THEN 'Stock' END AS loc_type,
    SUM(li.quantity) AS qty,
    MAX(CASE WHEN ib.qc_ExpiredDate > 0
             THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END) AS exp_date,
    MAX(CASE WHEN li.lot_id = 1 THEN 1 ELSE 0 END) AS is_legacy,
    MAX(IFNULL(iie.need_minimal_exp, 0)) AS need_minimal_exp
FROM yamibuy_wh.wh_lot_inventory li
JOIN yamibuy_wh.wh_storage_location wsl
    ON wsl.location_no      = li.location_no
   AND wsl.warehouse_number = li.warehouse_number
LEFT JOIN yamibuy_wh.wh_lot_master lm ON li.lot_id = lm.lot_id
LEFT JOIN yamibuy_wh.wh_inbound_batch ib
    ON lm.source_number = ib.inbound_number
LEFT JOIN yamibuy_im.im_item_extend iie
    ON iie.item_number = li.item_number
WHERE li.warehouse_number = '002'
  AND li.quantity > 0
  AND wsl.location_type IN (3, 4)
  AND wsl.size_id NOT IN (13, 33)
  AND (li.location_no NOT LIKE 'S%%'
   AND li.location_no NOT LIKE 'DC%%'
   AND li.location_no NOT LIKE 'K%%')
  AND li.item_number IN ({placeholders})
GROUP BY li.item_number, li.location_no, wsl.location_type
HAVING SUM(li.quantity) > 0
ORDER BY li.item_number, loc_type, qty DESC
"""

chunks = [skus[i:i+500] for i in range(0, len(skus), 500)]
all_rows, all_cols = [], None
print("Fetching MIDDLE FC lot data...", flush=True)
with conn.cursor() as cur:
    for idx, chunk in enumerate(chunks):
        ph = ",".join(["%s"]*len(chunk))
        cur.execute(SQL_LOT.format(placeholders=ph), chunk)
        if all_cols is None:
            all_cols = [d[0] for d in cur.description]
        all_rows.extend(cur.fetchall())
        print(f"  batch {idx+1}/{len(chunks)}: {len(all_rows):,} rows", flush=True)
conn.close()

df_lot = pd.DataFrame(all_rows, columns=all_cols)
df_lot["item_number"] = df_lot["item_number"].astype(str)
df_lot["qty"] = pd.to_numeric(df_lot["qty"], errors="coerce").fillna(0)
df_lot["exp_date"] = pd.to_datetime(df_lot["exp_date"], errors="coerce")
print(f"  → {len(df_lot):,} FC lot rows for MIDDLE SKUs")

# ── Apply location selection rules ───────────────────────────
results = []

for _, sku_row in mid_out.iterrows():
    sku         = sku_row["item_number"]
    excess      = sku_row["excess_qty"]
    keep        = sku_row["keep_qty"]
    adl         = sku_row["avg_daily_orders"]

    locs = df_lot[df_lot["item_number"] == sku].copy()
    if locs.empty:
        continue

    has_exp     = bool(locs["need_minimal_exp"].max() == 1)
    loc_count   = len(locs)

    if has_exp and loc_count > 1:
        # Rule 3: Has exp management + Multiple Locs → sort exp DESC (furthest first → DC)
        locs_sorted = locs.sort_values("exp_date", ascending=False).reset_index(drop=True)
        cum = 0
        for _, loc in locs_sorted.iterrows():
            xfer = min(loc["qty"], excess - cum)
            if xfer <= 0: break
            results.append({
                "item_number":     sku,
                "location_no":     loc["location_no"],
                "loc_type":        loc["loc_type"],
                "loc_qty":         loc["qty"],
                "qty_to_transfer": round(xfer),
                "exp_date":        str(loc["exp_date"])[:10] if pd.notna(loc["exp_date"]) else "",
                "rule":            "3_ExpMultiLoc_FurthestFirst",
                "excess_qty":      excess,
                "keep_qty":        keep,
            })
            cum += xfer
            if cum >= excess: break

    elif has_exp and loc_count == 1:
        # Rule 4: Has exp management + Single Loc → transfer excess only
        loc = locs.iloc[0]
        xfer = min(loc["qty"], excess)
        if xfer > 0:
            results.append({
                "item_number":     sku,
                "location_no":     loc["location_no"],
                "loc_type":        loc["loc_type"],
                "loc_qty":         loc["qty"],
                "qty_to_transfer": round(xfer),
                "exp_date":        str(loc["exp_date"])[:10] if pd.notna(loc["exp_date"]) else "",
                "rule":            "4_ExpSingleLoc_ExcessOnly",
                "excess_qty":      excess,
                "keep_qty":        keep,
            })

    elif loc_count > 1:
        # Rule 1: No exp management + Multiple Locs → closest to transfer_qty
        locs_sorted = locs.sort_values("qty", ascending=False).reset_index(drop=True)
        diffs = (locs_sorted["qty"] - excess).abs()
        best_single_idx = diffs.idxmin()
        best_single = locs_sorted.iloc[best_single_idx]

        if abs(best_single["qty"] - excess) / max(excess, 1) <= 0.15:
            results.append({
                "item_number":     sku,
                "location_no":     best_single["location_no"],
                "loc_type":        best_single["loc_type"],
                "loc_qty":         best_single["qty"],
                "qty_to_transfer": round(best_single["qty"]),
                "exp_date":        "",
                "rule":            "1_NoExpMultiLoc_ClosestSingle",
                "excess_qty":      excess,
                "keep_qty":        keep,
            })
        else:
            locs_asc = locs.sort_values("qty", ascending=True).reset_index(drop=True)
            cum = 0
            for _, loc in locs_asc.iterrows():
                if cum >= excess: break
                results.append({
                    "item_number":     sku,
                    "location_no":     loc["location_no"],
                    "loc_type":        loc["loc_type"],
                    "loc_qty":         loc["qty"],
                    "qty_to_transfer": round(loc["qty"]),
                    "exp_date":        "",
                    "rule":            "1_NoExpMultiLoc_Accumulate",
                    "excess_qty":      excess,
                    "keep_qty":        keep,
                })
                cum += loc["qty"]

    else:
        # Rule 2: No exp management + Single Loc → transfer excess only
        loc = locs.iloc[0]
        xfer = min(loc["qty"], excess)
        if xfer > 0:
            results.append({
                "item_number":     sku,
                "location_no":     loc["location_no"],
                "loc_type":        loc["loc_type"],
                "loc_qty":         loc["qty"],
                "qty_to_transfer": round(xfer),
                "exp_date":        "",
                "rule":            "2_NoExpSingleLoc_ExcessOnly",
                "excess_qty":      excess,
                "keep_qty":        keep,
            })

df_result = pd.DataFrame(results)
print(f"\nTotal location assignments: {len(df_result):,}")
print(f"SKUs with assignments: {df_result['item_number'].nunique():,}")

print("\n=== By rule applied ===")
print(df_result.groupby("rule")[["item_number","qty_to_transfer"]].agg(
    sku_count=("item_number","nunique"),
    total_qty=("qty_to_transfer","sum")
).to_string())

print("\n=== By loc_type ===")
print(df_result.groupby("loc_type")[["qty_to_transfer"]].agg(
    locs=("qty_to_transfer","count"),
    total_qty=("qty_to_transfer","sum")
).to_string())

ts = datetime.now().strftime("%Y%m%d_%H%M")
out_path = OUT / f"middle_location_selection_{ts}.csv"
df_result.to_csv(out_path, index=False)
print(f"\n✅ Saved: {out_path}  ({len(df_result):,} rows)")
