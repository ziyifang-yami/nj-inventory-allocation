"""
One-shot pending_putaway refresh — run on server.
Bypasses the 30-min TTL guard in refresh_sfc_cache.py.
"""
import sys, os
os.chdir("/opt/inv-allocation")
sys.path.insert(0, "/opt/inv-allocation")

from data.loader import load_pending_putaway
from cache_db import CacheDB

db = CacheDB("results/sfc_cache.db")

print("Loading pending putaway from RECG locations...")
df, err = load_pending_putaway()

if err:
    print(f"ERROR: {err}")
    sys.exit(1)

print(f"Loaded {len(df)} rows, columns: {list(df.columns)}")

# Show BT/GT sample
if "bt_number" in df.columns:
    print("\nSample BT/GT:")
    print(df[["item_number","bt_number","gt_number"]].head(10).to_string())
else:
    print("WARNING: bt_number column missing — check loader.py was deployed")

db.write_pending_putaway(df)
print(f"\nWritten to cache. Age: {db.age_minutes('pending_putaway'):.1f} min")
