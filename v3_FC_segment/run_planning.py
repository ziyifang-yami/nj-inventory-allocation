"""
30-Day Transfer Planning Model
================================
约束：
  - DC_TO_FC: ≤ 500 SKU/day (MIDDLE routine replenish)
  - FC_TO_DC: ≤ 500 SKU/day (TAIL + MIDDLE evict)
  - 计划周期: 30 days
  - 目标: 达到 calc_ideal 理想状态
  - SFC_Bin 接受 TAIL 货
  - FC 安全库存: D2S ≥ 7 days (不低于安全线)

输出:
  results/planning_30day_YYYYMMDD_HHMM.csv  — 每日执行计划
  results/planning_summary_YYYYMMDD_HHMM.csv — 汇总
"""
import pandas as pd
import numpy as np
import glob
from pathlib import Path
from datetime import datetime, timedelta

OUT = Path(__file__).parent / "results"

# ── Parameters ────────────────────────────────────────────────
P = dict(
    daily_cap_in     = 500,   # DC→FC max SKU/day
    daily_cap_out    = 500,   # FC→DC max SKU/day
    plan_days        = 30,    # planning horizon
    safety_d2s       = 7,     # min FC D2S before pulling from SFC
    top_trigger_d2s  = 28,
    top_target_d2s   = 28,
    mid_routine_d2s  = 28,
    mid_evict_d2s    = 30,
    mid_target_d2s   = 28,
    mid_emergency_d2s= 14,
    min_loc_qty      = 5,
    pallet_vol_in3   = 106_000,  # usable pallet volume (in³), ~20 pallets/500SKU
)

# ── Load data ─────────────────────────────────────────────────
print("Loading data...", flush=True)
seg_files = sorted(glob.glob(str(OUT / "segment_sku_detail_*.csv")))
if not seg_files: raise FileNotFoundError("No segment_sku_detail_*.csv")
df = pd.read_csv(seg_files[-1])
df["item_number"] = df["item_number"].astype(str)
for c in ["fc_qty","sfc_qty","avg_daily_orders","unit_volume_cbm"]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

# Filter in-stock
df = df[(df["fc_qty"]>0)|(df["sfc_qty"]>0)].copy()

# Classify with default thresholds (AOD≥21, OL≥30, TAIL<8)
AOD_TOP, OL_TOP, OL_TAIL = 21, 30, 8
def classify(r):
    if r.active_order_days >= AOD_TOP and r.order_lines_30d >= OL_TOP: return "TOP"
    if r.order_lines_30d < OL_TAIL: return "TAIL"
    return "MIDDLE"
df["seg"] = df.apply(classify, axis=1)
df["fc_d2s"] = np.where(df["avg_daily_orders"]>0,
                        df["fc_qty"]/df["avg_daily_orders"], np.nan)

print(f"  Total in-stock SKUs: {len(df):,}")
print(f"  Seg: {df['seg'].value_counts().to_dict()}")

# ── Compute ideal state per SKU ───────────────────────────────
def ideal_qty(row):
    fc  = row["fc_qty"];  sfc = row["sfc_qty"]
    adl = row["avg_daily_orders"]
    d2s = row["fc_d2s"]
    seg = row["seg"]

    if seg == "TOP":
        if adl > 0 and (pd.isna(d2s) or d2s < P["top_trigger_d2s"]):
            needed = max(0.0, round(adl * P["top_target_d2s"]) - fc)
            xfer   = min(needed, sfc)
            return fc + xfer, sfc - xfer, xfer, "DC_TO_FC"
    elif seg == "MIDDLE":
        d2s_v = d2s if not pd.isna(d2s) else 0.0
        if adl > 0 and d2s_v <= P["mid_routine_d2s"] and sfc > 0:
            needed = max(0.0, round(adl * P["mid_target_d2s"]) - fc)
            if needed > 0:
                xfer = min(needed, sfc)
                return fc + xfer, sfc - xfer, xfer, "DC_TO_FC"
        elif adl > 0 and d2s_v > P["mid_evict_d2s"]:
            keep   = round(adl * P["mid_evict_d2s"])
            excess = fc - keep
            if excess > P["min_loc_qty"]:
                return fc - excess, sfc + excess, excess, "FC_TO_DC"
    elif seg == "TAIL":
        if fc > 0:
            return 0.0, sfc + fc, fc, "FC_TO_DC"
    return fc, sfc, 0.0, "KEEP"

res = df.apply(ideal_qty, axis=1, result_type="expand")
res.columns = ["fc_ideal","sfc_ideal","transfer_qty","action"]
df = pd.concat([df, res], axis=1)
df["vol"] = df["unit_volume_cbm"]  # in³ (column named cbm but actually in³)
df["transfer_vol"] = df["transfer_qty"] * df["vol"]

# Priority scores
df["priority"] = 999.0
# TAIL FC→DC: highest priority
mask_tail = (df["action"]=="FC_TO_DC") & (df["seg"]=="TAIL")
df.loc[mask_tail, "priority"] = 100 + df.loc[mask_tail, "fc_qty"].rank(ascending=False) * 0.001

# MIDDLE FC→DC: by excess volume desc
mask_mid_out = (df["action"]=="FC_TO_DC") & (df["seg"]=="MIDDLE")
df.loc[mask_mid_out, "priority"] = 200 - df.loc[mask_mid_out, "transfer_vol"]

# TOP DC→FC: by D2S asc
mask_top_in = (df["action"]=="DC_TO_FC") & (df["seg"]=="TOP")
d2s_top = df.loc[mask_top_in, "fc_d2s"].fillna(0)
df.loc[mask_top_in, "priority"] = 300 + d2s_top

# MIDDLE DC→FC: P1 D2S asc, P2 batch vol desc, P3 daily sales desc
mask_mid_in = (df["action"]=="DC_TO_FC") & (df["seg"]=="MIDDLE")
d2s_mid = df.loc[mask_mid_in, "fc_d2s"].fillna(0)
bvol_mid = df.loc[mask_mid_in, "transfer_vol"]
adl_mid  = df.loc[mask_mid_in, "avg_daily_orders"]
df.loc[mask_mid_in, "priority"] = d2s_mid * 1e6 - bvol_mid * 1e3 - adl_mid

# Sort by priority
df_in  = df[df["action"]=="DC_TO_FC"].sort_values("priority").copy()
df_out = df[df["action"]=="FC_TO_DC"].sort_values("priority").copy()

print(f"\nIdeal state:")
print(f"  DC_TO_FC: {len(df_in):,} SKUs, {df_in['transfer_qty'].sum():,.0f} units, "
      f"{df_in['transfer_vol'].sum()/1e6:.1f}M in³")
print(f"  FC_TO_DC: {len(df_out):,} SKUs, {df_out['transfer_qty'].sum():,.0f} units, "
      f"{df_out['transfer_vol'].sum()/1e6:.1f}M in³")

# ── 30-Day Schedule ───────────────────────────────────────────
print(f"\nBuilding {P['plan_days']}-day schedule...", flush=True)

schedule = []
remaining_in  = df_in.copy()
remaining_out = df_out.copy()

start_date = datetime.today().date()

for day in range(1, P["plan_days"] + 1):
    date = start_date + timedelta(days=day - 1)
    day_records = []

    # FC→DC (OUT): TAIL first, then MIDDLE
    cap_out = P["daily_cap_out"]
    for _, row in remaining_out.iterrows():
        if cap_out <= 0: break
        day_records.append({
            "day": day, "date": str(date),
            "item_number": row["item_number"],
            "seg": row["seg"],
            "action": "FC_TO_DC",
            "transfer_qty": row["transfer_qty"],
            "transfer_vol": row["transfer_vol"],
            "priority": row["priority"],
        })
        cap_out -= 1

    done_out = {r["item_number"] for r in day_records if r["action"]=="FC_TO_DC"}
    remaining_out = remaining_out[~remaining_out["item_number"].isin(done_out)]

    # DC→FC (IN): TOP first, then MIDDLE
    cap_in = P["daily_cap_in"]
    for _, row in remaining_in.iterrows():
        if cap_in <= 0: break
        day_records.append({
            "day": day, "date": str(date),
            "item_number": row["item_number"],
            "seg": row["seg"],
            "action": "DC_TO_FC",
            "transfer_qty": row["transfer_qty"],
            "transfer_vol": row["transfer_vol"],
            "priority": row["priority"],
        })
        cap_in -= 1

    done_in = {r["item_number"] for r in day_records if r["action"]=="DC_TO_FC"}
    remaining_in = remaining_in[~remaining_in["item_number"].isin(done_in)]

    schedule.extend(day_records)

df_sched = pd.DataFrame(schedule)

# ── Daily summary ─────────────────────────────────────────────
daily = df_sched.groupby(["day","date","action"]).agg(
    sku_count    =("item_number",   "count"),
    transfer_qty =("transfer_qty",  "sum"),
    transfer_vol =("transfer_vol",  "sum"),
).reset_index()
daily["pallets"] = (daily["transfer_vol"] / P["pallet_vol_in3"]).apply(np.ceil).astype(int)

# ── Progress summary ──────────────────────────────────────────
total_in_ideal   = len(df_in)
total_out_ideal  = len(df_out)
done_in_30  = df_sched[df_sched["action"]=="DC_TO_FC"]["item_number"].nunique()
done_out_30 = df_sched[df_sched["action"]=="FC_TO_DC"]["item_number"].nunique()

print(f"\n{'='*60}")
print(f"30-DAY PLAN SUMMARY")
print(f"{'='*60}")
print(f"  DC→FC (Replenish):")
print(f"    Ideal total:  {total_in_ideal:>6,} SKUs")
print(f"    30d complete: {done_in_30:>6,} SKUs  ({done_in_30/max(total_in_ideal,1)*100:.1f}%)")
print(f"    Remaining:    {total_in_ideal-done_in_30:>6,} SKUs")
print(f"  FC→DC (Evict/Clear):")
print(f"    Ideal total:  {total_out_ideal:>6,} SKUs")
print(f"    30d complete: {done_out_30:>6,} SKUs  ({done_out_30/max(total_out_ideal,1)*100:.1f}%)")
print(f"    Remaining:    {total_out_ideal-done_out_30:>6,} SKUs")

vol_done_out = df_sched[df_sched["action"]=="FC_TO_DC"]["transfer_vol"].sum()
vol_done_in  = df_sched[df_sched["action"]=="DC_TO_FC"]["transfer_vol"].sum()
total_pallets = (vol_done_out + vol_done_in) / P["pallet_vol_in3"]
print(f"\n  Total volume moved: {(vol_done_out+vol_done_in)/1e6:.1f}M in³")
print(f"  Estimated pallets:  {total_pallets:,.0f}  (~{total_pallets/30:.0f}/day)")

# ── Save ──────────────────────────────────────────────────────
ts = datetime.now().strftime("%Y%m%d_%H%M")
sched_path   = OUT / f"planning_30day_{ts}.csv"
summary_path = OUT / f"planning_summary_{ts}.csv"
df_sched.to_csv(sched_path,   index=False)
daily.to_csv(summary_path,    index=False)
print(f"\n✅ Saved:")
print(f"   {sched_path}  ({len(df_sched):,} rows)")
print(f"   {summary_path}  ({len(daily):,} rows)")
