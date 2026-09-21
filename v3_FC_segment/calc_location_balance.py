"""
Calculate location balance for TOP/TAIL transfer planning.
Transfer granularity: whole location (整库位转移)

Logic:
- TOP DC_TO_FC: find minimum number of SFC locations to transfer
  such that fc_qty + transferred_qty >= 28*adl (whole locations, ascending qty)
- TAIL FC_TO_DC: all FC locations released (whole locations, full clear)

Two-level constraint (换位平衡, no dependency on existing free space):
  L1: FC_Bin locations released by TAIL >= FC_Bin locations needed by TOP
  L2: FC_Bin volume released by TAIL    >= FC_Bin volume brought in by TOP
      FC_Stock volume released by TAIL  >= FC_Stock overflow volume from TOP
"""
import pandas as pd, glob, numpy as np
from pathlib import Path

OUT = Path(__file__).parent / "results"

# ── Load location data ────────────────────────────────────────
loc_files = sorted(glob.glob(str(OUT / "sku_locations_top_tail_*.csv")))
if not loc_files:
    raise FileNotFoundError("Run pull_sku_locations.py first")
df_loc = pd.read_csv(loc_files[-1])
df_loc["item_number"] = df_loc["item_number"].astype(str)
for c in ["qty","loc_volume_in3","fill_rate","allowable_volume_in3"]:
    df_loc[c] = pd.to_numeric(df_loc[c], errors="coerce").fillna(0)

# ── Load segment detail ───────────────────────────────────────
seg_files = sorted(glob.glob(str(OUT / "segment_sku_detail_*.csv")))
df_seg = pd.read_csv(seg_files[-1])
df_seg["item_number"] = df_seg["item_number"].astype(str)
for c in ["fc_qty","sfc_qty","avg_daily_orders","unit_volume_cbm"]:
    df_seg[c] = pd.to_numeric(df_seg[c], errors="coerce").fillna(0)
seg_map = df_seg.set_index("item_number")[["fc_qty","avg_daily_orders","unit_volume_cbm"]]

# ═══════════════════════════════════════════════════════════════
# TOP: how many SFC locations need to be transferred?
# Whole-location transfer: pick SFC locations (ascending qty) until
# fc_qty + cumulative_qty >= 28 × adl
# ═══════════════════════════════════════════════════════════════
top_sfc = df_loc[
    (df_loc["action"] == "DC_TO_FC") &
    (df_loc["zone"] == "SFC")
].copy()
top_sfc = top_sfc.merge(seg_map, on="item_number", how="left")
top_sfc["target_qty"] = (top_sfc["avg_daily_orders"] * 28).round(0)

# For each TOP SKU: sort SFC locations by qty ascending, accumulate
# until fc_qty + cum_qty >= target_qty
top_results = []
for sku, grp in top_sfc.groupby("item_number"):
    fc_now    = grp["fc_qty"].iloc[0]
    target    = grp["target_qty"].iloc[0]
    adl       = grp["avg_daily_orders"].iloc[0]
    unit_vol  = grp["unit_volume_cbm"].iloc[0]

    needed_qty = max(0, target - fc_now)
    if needed_qty <= 0:
        # Already at target, but still need 1 bin location
        top_results.append({
            "item_number": sku, "locs_to_transfer": 0,
            "qty_to_transfer": 0, "vol_to_transfer": 0,
            "reaches_28d2s": True, "loc_type": "Bin",
        })
        continue

    # Sort by qty ascending — transfer smallest locs first to minimize excess
    locs = grp.sort_values("qty", ascending=True).reset_index(drop=True)
    cum_qty = 0
    locs_needed = 0
    total_vol = 0
    for _, loc_row in locs.iterrows():
        cum_qty    += loc_row["qty"]
        locs_needed += 1
        total_vol  += loc_row["qty"] * unit_vol
        if fc_now + cum_qty >= target:
            break

    reaches = (fc_now + cum_qty) >= target
    top_results.append({
        "item_number":     sku,
        "locs_to_transfer": locs_needed,
        "qty_to_transfer":  cum_qty,
        "vol_to_transfer":  round(total_vol, 0),
        "reaches_28d2s":    reaches,
        "loc_type":         locs.iloc[0]["loc_type"] if len(locs) else "Bin",
    })

df_top_req = pd.DataFrame(top_results)
reaches_28 = df_top_req["reaches_28d2s"].sum()

# ═══════════════════════════════════════════════════════════════
# TAIL: all FC locations released (whole clear)
# ═══════════════════════════════════════════════════════════════
tail_fc = df_loc[
    (df_loc["action"] == "FC_TO_DC") &
    (df_loc["zone"] == "FC")
].copy()
tail_fc_bin   = tail_fc[tail_fc["loc_type"] == "Bin"]
tail_fc_stock = tail_fc[tail_fc["loc_type"] == "Stock"]

tail_bin_locs   = tail_fc_bin["location_no"].nunique()
tail_bin_vol    = tail_fc_bin["allowable_volume_in3"].sum()
tail_stock_locs = tail_fc_stock["location_no"].nunique()
tail_stock_vol  = tail_fc_stock["allowable_volume_in3"].sum()

# ═══════════════════════════════════════════════════════════════
# Balance check
# ═══════════════════════════════════════════════════════════════
top_bin_locs_needed = df_top_req["locs_to_transfer"].sum()
top_bin_vol_needed  = df_top_req[df_top_req["loc_type"]=="Bin"]["vol_to_transfer"].sum()
top_stock_vol_needed= df_top_req[df_top_req["loc_type"]=="Stock"]["vol_to_transfer"].sum()

print("=" * 65)
print("LOCATION BALANCE ANALYSIS (whole-location transfer)")
print("=" * 65)

print(f"\nTOP SKUs: {len(df_top_req):,}")
print(f"  Can reach 28D2S: {reaches_28:,} ({reaches_28/max(len(df_top_req),1)*100:.0f}%)")
print(f"  Cannot (SFC stock insufficient): {len(df_top_req)-reaches_28:,}")

print(f"\nL1 — FC_Bin Location Count (换位平衡):")
print(f"  TAIL releases FC_Bin locs  : {tail_bin_locs:>8,}")
print(f"  TOP needs SFC locs to move : {int(top_bin_locs_needed):>8,}")
print(f"  Balance                    : {int(tail_bin_locs - top_bin_locs_needed):>+8,}  "
      f"{'✅' if tail_bin_locs >= top_bin_locs_needed else '⚠️ INSUFFICIENT'}")

print(f"\nL2 — FC_Bin Volume (换位平衡):")
print(f"  TAIL releases Bin vol      : {tail_bin_vol/1e6:>8.2f}M in³")
print(f"  TOP brings in Bin vol      : {top_bin_vol_needed/1e6:>8.2f}M in³")
print(f"  Balance                    : {(tail_bin_vol-top_bin_vol_needed)/1e6:>+8.2f}M in³  "
      f"{'✅' if tail_bin_vol >= top_bin_vol_needed else '⚠️'}")

print(f"\nL2 — FC_Stock Volume (overflow only):")
print(f"  TAIL releases Stock vol    : {tail_stock_vol/1e6:>8.2f}M in³")
print(f"  TOP Stock overflow vol     : {top_stock_vol_needed/1e6:>8.2f}M in³")
print(f"  Balance                    : {(tail_stock_vol-top_stock_vol_needed)/1e6:>+8.2f}M in³  "
      f"{'✅' if tail_stock_vol >= top_stock_vol_needed else '⚠️'}")

# Save
ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
df_top_req.to_csv(OUT / f"top_location_requirements_{ts}.csv", index=False)

tail_summary = tail_fc.groupby(["loc_type","storage_type"]).agg(
    locs=("location_no","nunique"),
    vol =("allowable_volume_in3","sum"),
    qty =("qty","sum"),
).reset_index()
tail_summary.to_csv(OUT / f"tail_fc_release_{ts}.csv", index=False)

print(f"\n✅ Saved: top_location_requirements_{ts}.csv")
print(f"✅ Saved: tail_fc_release_{ts}.csv")
