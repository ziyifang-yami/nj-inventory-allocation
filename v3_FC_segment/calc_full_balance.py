"""
Full FC Volume Balance Analysis
================================
Constraint: TAIL-released FC loc volume >= TOP inbound volume
            Split by loc_type × storage_type

Sources:
  - sku_locations_top_tail_*.csv  : TAIL FC locs + TOP SFC locs
  - middle_location_selection_*.csv: MIDDLE FC locs to release
  - segment_sku_detail_*.csv      : unit_vol, adl, fc_qty

Output: prints full balance table + saves results/full_balance_*.csv
"""
import pandas as pd, glob, numpy as np
from pathlib import Path

OUT = Path(__file__).parent / "results"

# ── Load data ─────────────────────────────────────────────────
loc_files = sorted(glob.glob(str(OUT / "sku_locations_top_tail_*.csv")))
mid_files = sorted(glob.glob(str(OUT / "middle_location_selection_*.csv")))
seg_files = sorted(glob.glob(str(OUT / "segment_sku_detail_*.csv")))

df_loc = pd.read_csv(loc_files[-1])
df_mid = pd.read_csv(mid_files[-1])
df_seg = pd.read_csv(seg_files[-1])

df_loc["item_number"] = df_loc["item_number"].astype(str)
df_mid["item_number"] = df_mid["item_number"].astype(str)
df_seg["item_number"] = df_seg["item_number"].astype(str)

for c in ["qty","loc_volume_in3","fill_rate","allowable_volume_in3"]:
    df_loc[c] = pd.to_numeric(df_loc[c], errors="coerce").fillna(0)
df_seg["unit_volume_cbm"]  = pd.to_numeric(df_seg["unit_volume_cbm"],  errors="coerce").fillna(0)
df_seg["avg_daily_orders"] = pd.to_numeric(df_seg["avg_daily_orders"], errors="coerce").fillna(0)
df_seg["fc_qty"]           = pd.to_numeric(df_seg["fc_qty"],           errors="coerce").fillna(0)
df_seg["sfc_qty"]          = pd.to_numeric(df_seg["sfc_qty"],          errors="coerce").fillna(0)

# ── Key: loc_type_storage_type ────────────────────────────────
df_loc["loc_key"] = df_loc["loc_type"] + "_" + df_loc["storage_type"]

# ════════════════════════════════════════════════════════════════
# A. TAIL FC locations released (all FC locations for TAIL SKUs)
# ════════════════════════════════════════════════════════════════
tail_fc = df_loc[
    (df_loc["action"]=="FC_TO_DC") & (df_loc["zone"]=="FC")
].copy()

tail_released = tail_fc.groupby("loc_key").agg(
    locs_released   =("location_no",        "nunique"),
    vol_released_in3=("allowable_volume_in3","sum"),
    qty_released    =("qty",                 "sum"),
).reset_index()

print("=== A. TAIL — FC locations released ===")
print(tail_released.to_string(index=False))

# ════════════════════════════════════════════════════════════════
# B. MIDDLE FC locations released (from location selection)
# ════════════════════════════════════════════════════════════════
# middle_location_selection has loc_type but not storage_type
# need to join with loc data to get storage_type
mid_fc = df_mid.copy()
# join storage_type from df_loc
loc_meta = df_loc[df_loc["zone"]=="FC"][["location_no","storage_type","allowable_volume_in3"]]\
             .drop_duplicates("location_no")
mid_fc = mid_fc.merge(loc_meta, on="location_no", how="left")
mid_fc["storage_type"]       = mid_fc["storage_type"].fillna("Unknown")
mid_fc["allowable_volume_in3"]= pd.to_numeric(mid_fc["allowable_volume_in3"], errors="coerce").fillna(0)
mid_fc["loc_key"] = mid_fc["loc_type"] + "_" + mid_fc["storage_type"]

mid_released = mid_fc.groupby("loc_key").agg(
    locs_released   =("location_no",         "nunique"),
    vol_released_in3=("allowable_volume_in3", "sum"),
    qty_released    =("qty_to_transfer",       "sum"),
).reset_index()

print("\n=== B. MIDDLE — FC locations released ===")
print(mid_released.to_string(index=False))

# ════════════════════════════════════════════════════════════════
# C. TOP — FC space needed (whole-location transfer, max 2 bin/SKU)
#    Per SKU: pick SFC locations (ascending qty) until
#    fc_qty + cum_transferred_qty >= 28×adl
#    Each selected location = whole transfer (整库位)
#    Bin volume cap per SKU = 2 × avg_bin_vol; overflow → Stock
# ════════════════════════════════════════════════════════════════
avg_bin_vol    = 32_909_921 / 16_463   # FC_Bin_BinBox: allowable_cap / locs ≈ 2,000 in³
bin_cap_per_sku = 2 * avg_bin_vol

top_sfc = df_loc[
    (df_loc["action"]=="DC_TO_FC") & (df_loc["zone"]=="SFC")
].copy()
top_sfc = top_sfc.merge(
    df_seg[["item_number","unit_volume_cbm","avg_daily_orders","fc_qty"]],
    on="item_number", how="left"
)
for c in ["unit_volume_cbm","avg_daily_orders","fc_qty"]:
    top_sfc[c] = top_sfc[c].fillna(0)

top_results = []
for sku, grp in top_sfc.groupby("item_number"):
    fc_now   = grp["fc_qty"].iloc[0]
    adl      = grp["avg_daily_orders"].iloc[0]
    unit_vol = grp["unit_volume_cbm"].iloc[0]
    target   = adl * 28

    needed = max(0, target - fc_now)
    if needed <= 0:
        # Already at target — still count 1 Bin loc for picking
        top_results.append({"item_number":sku,
                             "bin_locs":1,"bin_vol":0,
                             "stock_locs":0,"stock_vol":0,
                             "transfer_qty":0,"reaches_28":True})
        continue

    # Pick SFC locations ascending qty (整库位)
    locs = grp.sort_values("qty", ascending=True)
    cum_qty = 0
    cum_vol = 0
    locs_used = 0
    bin_vol = 0; stock_vol = 0
    bin_locs = 0; stock_locs = 0

    for _, loc in locs.iterrows():
        cum_qty  += loc["qty"]
        cum_vol  += loc["qty"] * unit_vol
        locs_used += 1
        # assign to Bin or Stock based on remaining bin capacity
        remaining_bin = bin_cap_per_sku - bin_vol
        loc_vol = loc["qty"] * unit_vol
        if remaining_bin > 0:
            into_bin  = min(loc_vol, remaining_bin)
            into_stock = max(0, loc_vol - remaining_bin)
            bin_vol   += into_bin
            stock_vol += into_stock
            if into_bin > 0:   bin_locs   = min(2, bin_locs + 1)
            if into_stock > 0: stock_locs += 1
        else:
            stock_vol  += loc_vol
            stock_locs += 1
        if fc_now + cum_qty >= target:
            break

    top_results.append({
        "item_number":  sku,
        "bin_locs":     bin_locs,
        "bin_vol":      bin_vol,
        "stock_locs":   stock_locs,
        "stock_vol":    stock_vol,
        "transfer_qty": cum_qty,
        "reaches_28":   (fc_now + cum_qty) >= target,
    })

df_top_req = pd.DataFrame(top_results)

top_needed = pd.DataFrame([
    {"loc_key":"Bin_Bin Box",  "locs_needed": int(df_top_req["bin_locs"].sum()),
     "vol_needed_in3": df_top_req["bin_vol"].sum(),
     "sku_count": len(df_top_req[df_top_req["bin_locs"]>0])},
    {"loc_key":"Stock_Pallet", "locs_needed": int(df_top_req["stock_locs"].sum()),
     "vol_needed_in3": df_top_req["stock_vol"].sum(),
     "sku_count": len(df_top_req[df_top_req["stock_locs"]>0])},
])

reaches = df_top_req["reaches_28"].sum()
print("\n=== C. TOP — FC space needed (whole-location, max 2 bin/SKU) ===")
print(f"  avg_bin_vol={avg_bin_vol:,.0f} in³  bin_cap/SKU={bin_cap_per_sku:,.0f} in³")
print(f"  SKUs reaching 28D2S: {reaches:,} / {len(df_top_req):,}")
print(top_needed.to_string(index=False))

# ── C2. If ALL SFC stock transferred back (no D2S cap) ────────
top_all = df_loc[
    (df_loc["action"]=="DC_TO_FC") & (df_loc["zone"]=="SFC")
].copy()
top_all = top_all.merge(
    df_seg[["item_number","unit_volume_cbm"]], on="item_number", how="left"
)
top_all["unit_volume_cbm"] = top_all["unit_volume_cbm"].fillna(0)
top_all["loc_vol"] = top_all["qty"] * top_all["unit_volume_cbm"]

total_sfc_qty = top_all["qty"].sum()
total_sfc_vol = top_all["loc_vol"].sum()

# Bin vs Stock split for ALL stock
bin_vol_all   = 0.0
stock_vol_all = 0.0
bin_locs_all  = 0
stock_locs_all= 0

for sku, grp in top_all.groupby("item_number"):
    sku_vol = grp["loc_vol"].sum()
    into_bin   = min(sku_vol, bin_cap_per_sku)
    into_stock = max(0, sku_vol - bin_cap_per_sku)
    bin_vol_all   += into_bin
    stock_vol_all += into_stock
    bin_locs_all   += min(2, max(1, int(np.ceil(into_bin / avg_bin_vol))))
    if into_stock > 0: stock_locs_all += 1

print(f"\n=== C2. TOP — IF ALL SFC stock transferred (no D2S cap) ===")
print(f"  Total SFC qty      : {total_sfc_qty:>10,.0f} units")
print(f"  Total SFC vol      : {total_sfc_vol/1e6:>10.2f}M in³")
print(f"  → FC Bin needed    : {bin_locs_all:>10,} locs  ({bin_vol_all/1e6:.2f}M in³)")
print(f"  → FC Stock needed  : {stock_locs_all:>10,} locs  ({stock_vol_all/1e6:.2f}M in³)")
print(f"  TAIL releases Bin  : {int(tail_released[tail_released['loc_key']=='Bin_Bin Box']['locs_released'].sum()):>10,} locs  "
      f"({float(tail_released[tail_released['loc_key']=='Bin_Bin Box']['vol_released_in3'].sum())/1e6:.2f}M in³)")
print(f"  Bin balance (all-in): {(float(tail_released[tail_released['loc_key']=='Bin_Bin Box']['vol_released_in3'].sum()) - bin_vol_all)/1e6:>+10.2f}M in³  "
      f"{'✅' if float(tail_released[tail_released['loc_key']=='Bin_Bin Box']['vol_released_in3'].sum()) >= bin_vol_all else '⚠️ NEED MIDDLE'}")

# ── C3. SFC location detail: how many locs transferred vs remaining ───
print(f"\n=== C3. TOP — SFC location transfer detail (min 28D2S, whole-loc) ===")

# Get all SFC locations for TOP SKUs
top_sfc_detail = df_loc[
    (df_loc["action"]=="DC_TO_FC") & (df_loc["zone"]=="SFC")
].copy()
top_sfc_detail = top_sfc_detail.merge(
    df_seg[["item_number","unit_volume_cbm","avg_daily_orders","fc_qty"]],
    on="item_number", how="left"
)
for c in ["unit_volume_cbm","avg_daily_orders","fc_qty","qty"]:
    top_sfc_detail[c] = pd.to_numeric(top_sfc_detail[c], errors="coerce").fillna(0)

# Mark which locations are selected for transfer (min to reach 28D2S)
selected_locs = []
for sku, grp in top_sfc_detail.groupby("item_number"):
    fc_now = grp["fc_qty"].iloc[0]
    adl    = grp["avg_daily_orders"].iloc[0]
    target = adl * 28
    needed = max(0, target - fc_now)

    locs = grp.sort_values("qty", ascending=True).reset_index(drop=True)
    cum = 0
    for i, loc in locs.iterrows():
        cum += loc["qty"]
        selected_locs.append({
            "item_number": sku,
            "location_no": loc["location_no"],
            "loc_type":    loc["loc_type"],
            "storage_type":loc.get("storage_type",""),
            "qty":         loc["qty"],
            "transferred": True,  # all selected for transfer
        })
        if cum >= needed and needed > 0:
            # mark remaining locs as NOT transferred
            remaining = locs.iloc[i+1:]
            for _, rloc in remaining.iterrows():
                selected_locs.append({
                    "item_number": sku,
                    "location_no": rloc["location_no"],
                    "loc_type":    rloc["loc_type"],
                    "storage_type":rloc.get("storage_type",""),
                    "qty":         rloc["qty"],
                    "transferred": False,
                })
            break

df_sel = pd.DataFrame(selected_locs)

# Summary by loc_type × storage_type × transferred
summary = df_sel.groupby(["loc_type","storage_type","transferred"]).agg(
    locs =("location_no","nunique"),
    qty  =("qty",         "sum"),
    skus =("item_number", "nunique"),
).reset_index()
summary["status"] = summary["transferred"].map({True:"→ Transfer", False:"✋ Remain in SFC"})

print(f"\n  {'loc_type':<8} {'storage_type':<12} {'status':<20} {'locs':>6} {'qty':>8} {'skus':>6}")
print("  " + "-"*65)
for _, r in summary.sort_values(["loc_type","storage_type","transferred"]).iterrows():
    print(f"  {r['loc_type']:<8} {str(r['storage_type']):<12} {r['status']:<20} {int(r['locs']):>6,} {int(r['qty']):>8,} {int(r['skus']):>6,}")

# Totals
t_in  = df_sel[df_sel["transferred"]==True]
t_out = df_sel[df_sel["transferred"]==False]
print(f"\n  TOTAL transferred  : {t_in['location_no'].nunique():>6,} locs  {t_in['qty'].sum():>8,.0f} units")
print(f"  TOTAL remain in SFC: {t_out['location_no'].nunique():>6,} locs  {t_out['qty'].sum():>8,.0f} units")

# ════════════════════════════════════════════════════════════════
# D. Full Balance: TAIL + MIDDLE released vs TOP needed
# ════════════════════════════════════════════════════════════════
all_keys = set(tail_released["loc_key"]) | set(mid_released["loc_key"]) | set(top_needed["loc_key"])

balance_rows = []
for key in sorted(all_keys):
    tail_vol  = tail_released[tail_released["loc_key"]==key]["vol_released_in3"].sum()
    tail_locs = int(tail_released[tail_released["loc_key"]==key]["locs_released"].sum())
    mid_vol   = mid_released[mid_released["loc_key"]==key]["vol_released_in3"].sum()
    mid_locs  = int(mid_released[mid_released["loc_key"]==key]["locs_released"].sum())
    top_vol   = top_needed[top_needed["loc_key"]==key]["vol_needed_in3"].sum()
    top_locs  = int(top_needed[top_needed["loc_key"]==key]["locs_needed"].sum())

    total_released_vol  = tail_vol + mid_vol
    total_released_locs = tail_locs + mid_locs
    vol_balance  = total_released_vol  - top_vol
    locs_balance = total_released_locs - top_locs
    feasible = vol_balance >= 0 and locs_balance >= 0

    balance_rows.append({
        "loc_key":              key,
        "tail_locs_released":   tail_locs,
        "tail_vol_released_M":  round(tail_vol/1e6, 2),
        "mid_locs_released":    mid_locs,
        "mid_vol_released_M":   round(mid_vol/1e6, 2),
        "total_locs_released":  total_released_locs,
        "total_vol_released_M": round(total_released_vol/1e6, 2),
        "top_locs_needed":      top_locs,
        "top_vol_needed_M":     round(top_vol/1e6, 3),
        "vol_balance_M":        round(vol_balance/1e6, 2),
        "locs_balance":         locs_balance,
        "feasible":             "✅" if feasible else "⚠️",
    })

df_balance = pd.DataFrame(balance_rows)

print("\n" + "="*80)
print("D. FULL BALANCE: TAIL+MIDDLE released vs TOP needed (by loc_key)")
print("="*80)
pd.set_option("display.width", 200)
print(df_balance.to_string(index=False))

# Overall
print(f"\n  Total released vol : {df_balance['total_vol_released_M'].sum():.2f}M in³")
print(f"  Total TOP vol needed: {df_balance['top_vol_needed_M'].sum():.3f}M in³")
print(f"  Overall balance    : {df_balance['total_vol_released_M'].sum() - df_balance['top_vol_needed_M'].sum():+.2f}M in³")

ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
df_balance.to_csv(OUT / f"full_balance_{ts}.csv", index=False)
print(f"\n✅ Saved: full_balance_{ts}.csv")

# ════════════════════════════════════════════════════════════════
# E. SFC capacity check: can TAIL inbound fit in SFC?
# ════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("E. SFC CAPACITY CHECK — Can TAIL fit in SFC?")
print(f"{'='*65}")

# TAIL goods going to SFC: qty × unit_vol
tail_inbound = df_loc[
    (df_loc["action"]=="FC_TO_DC") & (df_loc["zone"]=="FC")
].copy()
tail_inbound = tail_inbound.merge(
    df_seg[["item_number","unit_volume_cbm"]], on="item_number", how="left"
)
tail_inbound["unit_volume_cbm"] = tail_inbound["unit_volume_cbm"].fillna(0)
tail_inbound["goods_vol"] = tail_inbound["qty"] * tail_inbound["unit_volume_cbm"]

tail_goods_vol = tail_inbound["goods_vol"].sum()
tail_goods_qty = tail_inbound["qty"].sum()

print(f"\n  TAIL goods going to SFC:")
print(f"    Total qty  : {tail_goods_qty:>12,.0f} units")
print(f"    Total vol  : {tail_goods_vol/1e6:>12.2f}M in³ (goods volume)")

# SFC current free space (from capacity data)
# Use last known values from capacity analysis
# SFC_Bin_BinBox free: ~1.4M in³ (93-99% full)
# SFC_Stock_Pallet free: ~134M in³ (43% full)
sfc_bin_free   =   1_413_390 + 6_762_704 + 530_842  # BinBox + Pallet + extra
sfc_stock_free = 134_105_141  # Stock_Pallet free

print(f"\n  SFC current free space (from latest capacity data):")
print(f"    SFC_Bin free   : {sfc_bin_free/1e6:>8.2f}M in³  ({'⚠️ TIGHT' if sfc_bin_free < tail_goods_vol * 0.3 else '✅ OK'})")
print(f"    SFC_Stock free : {sfc_stock_free/1e6:>8.2f}M in³")
print(f"    SFC_Total free : {(sfc_bin_free+sfc_stock_free)/1e6:>8.2f}M in³")

print(f"\n  TAIL goes to SFC_Stock (pallet storage):")
sfc_stock_after = sfc_stock_free - tail_goods_vol
print(f"    SFC_Stock free after TAIL : {sfc_stock_after/1e6:>8.2f}M in³  "
      f"{'✅ Sufficient' if sfc_stock_after >= 0 else '⚠️ INSUFFICIENT'}")

if sfc_stock_after < 0:
    print(f"    ⚠️ SFC_Stock deficit: {abs(sfc_stock_after)/1e6:.2f}M in³")
    print(f"    → Need DC expansion or reduce TAIL transfer volume")
