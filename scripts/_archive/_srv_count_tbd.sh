#!/bin/bash
cd /opt/inv-allocation
.venv/bin/python - <<'EOF'
from cache_db import CacheDB
db = CacheDB('results/sfc_cache.db')
df = db.read_sales()
n = len(df)
print(f"Sales SKUs: {n:,}")
if n > 0:
    tbd = int((df['inv_days_30'] < 30).sum())
    zero = int((df['inv_days_30'] == 0).sum())
    normal = int((df['inv_days_30'] >= 30).sum())
    print(f"TBD (inv_days_30 < 30): {tbd:,}  ({tbd/n*100:.1f}%)")
    print(f"  of which inv_days_30=0: {zero:,}")
    print(f"  inv_days_30 1-29:       {tbd-zero:,}")
    print(f"Normal (>=30):  {normal:,}  ({normal/n*100:.1f}%)")
EOF
