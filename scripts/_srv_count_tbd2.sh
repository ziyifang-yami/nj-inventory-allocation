#!/bin/bash
cd /opt/inv-allocation
.venv/bin/python - <<'EOF'
from cache_db import CacheDB
import sqlite3

db = CacheDB('results/sfc_cache.db')

# NJ current in-stock SKUs (FC+SFC combined, deduplicated) from inventory query
# The refresh_sfc_cache.py Step 1 stores df_inv in memory but doesn't persist it to SQLite.
# Best proxy: sfc_lots (SFC only) + fc2sfc_triggers (FC with current stock)
# OR: query directly via SQLite's sales table cross-joined with current inventory

# Actually: the sales table has 134K SKUs with NJ history
# The df_inv from Step 1 had 43-44K SKUs with current NJ stock
# Let's cross-reference: sales SKUs that also appear in sfc_lots OR fc2sfc_tasks

conn = sqlite3.connect('results/sfc_cache.db')

# Current in-stock from sfc_lots (SFC zone)
sfc_skus = set(r[0] for r in conn.execute("SELECT DISTINCT item_number FROM sfc_lots").fetchall())

# Current in-stock from fc2sfc_triggers (FC zone — triggered SKUs = have FC stock)
fc_trigger_skus = set(r[0] for r in conn.execute("SELECT DISTINCT item_number FROM fc2sfc_triggers").fetchall())

# Also check fc2sfc_tasks for FC skus
fc_task_skus = set(r[0] for r in conn.execute("SELECT DISTINCT item_number FROM fc2sfc_tasks").fetchall())

# Combined NJ in-stock (deduped)
nj_skus = sfc_skus | fc_trigger_skus | fc_task_skus
print(f"SFC in-stock SKUs (sfc_lots):      {len(sfc_skus):,}")
print(f"FC triggered SKUs (fc2sfc_triggers): {len(fc_trigger_skus):,}")
print(f"FC task SKUs (fc2sfc_tasks):         {len(fc_task_skus):,}")
print(f"NJ combined dedup:                   {len(nj_skus):,}")

# Now cross with sales to check inv_days_30
from cache_db import CacheDB
df_sales = CacheDB('results/sfc_cache.db').read_sales()
df_nj = df_sales[df_sales['item_number'].isin(nj_skus)]
print(f"\nNJ SKUs with Athena sales data: {len(df_nj):,}")

if len(df_nj) > 0:
    tbd = int((df_nj['inv_days_30'] < 30).sum())
    normal = int((df_nj['inv_days_30'] >= 30).sum())
    no_athena = len(nj_skus) - len(df_nj)
    print(f"TBD (inv_days_30 < 30):  {tbd:,}  ({tbd/len(nj_skus)*100:.1f}%)")
    print(f"No Athena data at all:   {no_athena:,}  ({no_athena/len(nj_skus)*100:.1f}%)  → also TBD")
    print(f"Total TBD:               {tbd+no_athena:,}  ({(tbd+no_athena)/len(nj_skus)*100:.1f}%)")
    print(f"Normal (>=30):           {normal:,}  ({normal/len(nj_skus)*100:.1f}%)")

conn.close()
EOF
