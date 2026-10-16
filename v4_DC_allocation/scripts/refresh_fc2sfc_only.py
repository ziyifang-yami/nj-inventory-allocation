"""
Force-refresh FC→SFC task cache (bypass 1h TTL).
Run on server: /opt/inv-allocation/.venv/bin/python /tmp/refresh_fc2sfc.py
"""
import os, sys, time
os.chdir('/opt/inv-allocation')
sys.path.insert(0, '/opt/inv-allocation')

from cache_db import CacheDB
from data.loader import load, load_fc_lot_data
from logic.fc_to_sfc import (
    compute_fc_to_sfc,
    select_middle_fc_locations,
    select_tail_fc_locations,
    build_fc_to_sfc_queue,
)
from config import P

db = CacheDB("results/sfc_cache.db")

print("Loading SKU inventory data...")
df, err = load()
if err or df is None or df.empty:
    print(f"ERROR loading SKU data: {err}")
    sys.exit(1)
print(f"  {len(df):,} SKUs loaded")

print("Computing FC→SFC triggers...")
t0 = time.perf_counter()
df_triggered = compute_fc_to_sfc(df, dict(P))
df_mid  = df_triggered[df_triggered["trigger"] == "MIDDLE_EVICT"] if not df_triggered.empty else None
df_tail = df_triggered[df_triggered["trigger"] == "TAIL_CLEAR"]   if not df_triggered.empty else None
n_mid  = len(df_mid)  if df_mid  is not None and not df_mid.empty  else 0
n_tail = len(df_tail) if df_tail is not None and not df_tail.empty else 0
print(f"  MIDDLE: {n_mid:,}  TAIL: {n_tail:,}  ({time.perf_counter()-t0:.1f}s)")

# Write triggers
df_triggered["trigger_type"] = df_triggered["trigger"]
df_triggered["seg"]          = df_triggered["seg"] if "seg" in df_triggered.columns else df_triggered.get("segment", "")
db.write_fc2sfc_triggers(df_triggered)
print("  Triggers written.")

# Build lot-level tasks
print("Loading FC lot data...")
t1 = time.perf_counter()
all_skus = []
if df_mid  is not None and not df_mid.empty:  all_skus += df_mid["item_number"].tolist()
if df_tail is not None and not df_tail.empty: all_skus += df_tail["item_number"].tolist()
all_skus = list(set(all_skus))

df_fc_lots, fc_err = load_fc_lot_data(all_skus)
if fc_err:
    print(f"  WARNING: {fc_err}")
    df_fc_lots = None
print(f"  {len(df_fc_lots):,} lot rows  ({time.perf_counter()-t1:.1f}s)" if df_fc_lots is not None else "  0 lot rows")

# Verify grouping
if df_fc_lots is not None and not df_fc_lots.empty:
    dupes = df_fc_lots.groupby(["item_number","location_no"]).size()
    dupes = dupes[dupes > 1]
    if dupes.empty:
        print(f"  ✅ No duplicate item+location rows")
    else:
        print(f"  ⚠ {len(dupes)} item+location combos still have duplicates!")
        print(dupes.head(5))

print("Building FC→SFC task lists...")
t2 = time.perf_counter()
mid_tasks  = {}
tail_tasks = {}

if df_fc_lots is not None and not df_fc_lots.empty:
    if df_mid is not None and not df_mid.empty:
        for _, sku_row in df_mid.iterrows():
            locs = select_middle_fc_locations(df_fc_lots, sku_row, cfg=dict(P))
            if not locs.empty:
                locs["segment"] = "MIDDLE"
                mid_tasks[str(sku_row["item_number"])] = locs

    if df_tail is not None and not df_tail.empty:
        for _, sku_row in df_tail.iterrows():
            sku = str(sku_row["item_number"])
            locs = select_tail_fc_locations(df_fc_lots, sku)
            if not locs.empty:
                locs["segment"] = "TAIL"
                tail_tasks[sku] = locs

import pandas as pd
mid_df  = pd.concat(mid_tasks.values(),  ignore_index=True) if mid_tasks  else pd.DataFrame()
tail_df = pd.concat(tail_tasks.values(), ignore_index=True) if tail_tasks else pd.DataFrame()
print(f"  MIDDLE tasks: {len(mid_df):,}  TAIL tasks: {len(tail_df):,}  ({time.perf_counter()-t2:.1f}s)")

db.write_fc2sfc_tasks({
    "MIDDLE_EVICT": mid_df,
    "TAIL_CLEAR":   tail_df,
})
print(f"  Written to cache. Total {len(mid_df)+len(tail_df):,} task rows.")
print("Done.")
