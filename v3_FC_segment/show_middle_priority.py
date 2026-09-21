"""
Show MIDDLE SKU priority ranking for both directions
and integrate location-level selection
"""
import pandas as pd, glob, numpy as np
from pathlib import Path

OUT = Path(__file__).parent / "results"

seg_files = sorted(glob.glob(str(OUT / "segment_sku_detail_*.csv")))
df = pd.read_csv(seg_files[-1])
df["item_number"] = df["item_number"].astype(str)
for c in ["fc_qty","sfc_qty","avg_daily_orders","unit_volume_cbm"]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

# Filter in-stock
df = df[(df["fc_qty"]>0)|(df["sfc_qty"]>0)].copy()

# Classify
def classify(r):
    if r.active_order_days >= 21 and r.order_lines_30d >= 30: return "TOP"
    if r.order_lines_30d < 8: return "TAIL"
    return "MIDDLE"
df["seg"] = df.apply(classify, axis=1)
df["fc_d2s"] = np.where(df["avg_daily_orders"]>0, df["fc_qty"]/df["avg_daily_orders"], np.nan)

mid = df[df["seg"]=="MIDDLE"].copy()

# ── MIDDLE FC→DC (excess evict) ───────────────────────────────
mid["keep_qty"]  = np.where(mid["avg_daily_orders"]>0,
                             np.round(mid["avg_daily_orders"]*30), mid["fc_qty"])
mid["excess_qty"] = (mid["fc_qty"] - mid["keep_qty"]).clip(lower=0)
mid["excess_vol"] = mid["excess_qty"] * mid["unit_volume_cbm"]

mid_out = mid[
    (mid["avg_daily_orders"]>0) &
    (~mid["fc_d2s"].isna()) &
    (mid["fc_d2s"]>30) &
    (mid["excess_qty"]>5)
].copy()

max_ev = mid_out["excess_vol"].max() or 1
max_ex = mid_out["excess_qty"].max() or 1
mid_out["priority"] = (
    (1 - mid_out["excess_vol"] / max_ev) * 700 +
    (1 - mid_out["excess_qty"] / max_ex) * 299
)
mid_out = mid_out.sort_values("priority").reset_index(drop=True)
mid_out["rank"] = mid_out.index + 1

print("="*70)
print(f"MIDDLE FC→DC (Evict Excess) — {len(mid_out):,} SKUs")
print("P1: Excess Volume largest  P2: Excess Qty largest")
print("="*70)
print(mid_out[["rank","item_number","seg","fc_d2s","excess_qty","excess_vol",
               "avg_daily_orders","unit_volume_cbm"]].head(20).to_string(index=False))
print(f"\n  Total excess qty: {mid_out['excess_qty'].sum():,.0f}")
print(f"  Total excess vol: {mid_out['excess_vol'].sum()/1e6:.2f}M in³")

# ── MIDDLE DC→FC (routine replenish) ─────────────────────────
mid["d2s_v"]      = mid["fc_d2s"].fillna(0)
mid["needed_qty"] = (mid["avg_daily_orders"]*28 - mid["fc_qty"]).clip(lower=0)
mid["xfer_qty"]   = mid[["sfc_qty","needed_qty"]].min(axis=1)
mid["batch_vol"]  = mid["xfer_qty"] * mid["unit_volume_cbm"]

mid_in = mid[
    (mid["avg_daily_orders"]>0) &
    (mid["d2s_v"] <= 28) &
    (mid["sfc_qty"] > 0) &
    (mid["needed_qty"] > 0)
].copy()

max_d2s = max(28, 1)
max_vol = mid_in["batch_vol"].max() or 1
max_adl = mid_in["avg_daily_orders"].max() or 1
mid_in["priority"] = (
    (mid_in["d2s_v"] / max_d2s) * 600 -
    (mid_in["batch_vol"] / max_vol) * 300 -
    (mid_in["avg_daily_orders"] / max_adl) * 99
)
mid_in = mid_in.sort_values("priority").reset_index(drop=True)
mid_in["rank"] = mid_in.index + 1

print("\n" + "="*70)
print(f"MIDDLE DC→FC (Routine Replenish) — {len(mid_in):,} SKUs")
print("P1: FC D2S lowest  P2: Batch Volume largest  P3: Daily Sales highest")
print("="*70)
print(mid_in[["rank","item_number","seg","fc_d2s","xfer_qty","batch_vol",
              "avg_daily_orders"]].head(20).to_string(index=False))
print(f"\n  Total replenish qty: {mid_in['xfer_qty'].sum():,.0f}")
print(f"  Total batch vol    : {mid_in['batch_vol'].sum()/1e6:.2f}M in³")

# ── Integrate location-level selection for FC→DC ─────────────
loc_files = sorted(glob.glob(str(OUT / "middle_location_selection_*.csv")))
if loc_files:
    df_loc_sel = pd.read_csv(loc_files[-1])
    df_loc_sel["item_number"] = df_loc_sel["item_number"].astype(str)

    # Merge priority into location selection
    priority_map = mid_out.set_index("item_number")["priority"].to_dict()
    df_loc_sel["sku_priority"] = df_loc_sel["item_number"].map(priority_map).fillna(999)
    df_loc_sel = df_loc_sel.sort_values(["sku_priority","item_number","location_no"])

    print("\n" + "="*70)
    print(f"MIDDLE FC→DC — Location-level plan (sorted by SKU priority)")
    print("="*70)
    print(df_loc_sel[["item_number","location_no","loc_type","qty_to_transfer",
                       "rule","sku_priority"]].head(30).to_string(index=False))
    print(f"\n  Total: {len(df_loc_sel):,} location moves")
    print(f"  SKUs:  {df_loc_sel['item_number'].nunique():,}")
    print("\n  By rule:")
    print(df_loc_sel.groupby("rule")[["item_number","qty_to_transfer"]].agg(
        sku_count=("item_number","nunique"),
        total_qty=("qty_to_transfer","sum")
    ).to_string())

    # Save integrated file
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
    out_path = OUT / f"middle_fc_to_dc_plan_{ts}.csv"
    df_loc_sel.to_csv(out_path, index=False)
    print(f"\n✅ Saved: {out_path}")
else:
    print("\n⚠️ No middle_location_selection file found. Run calc_middle_location_selection.py first.")
