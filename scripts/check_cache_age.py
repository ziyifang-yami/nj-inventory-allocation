import os, sys
os.chdir('/opt/inv-allocation')
sys.path.insert(0, '/opt/inv-allocation')
from cache_db import CacheDB
db = CacheDB('results/sfc_cache.db')
tables = ['sales','sfc_lots','fc_exp','exp_flags','tbd_skus','task_list','fc2sfc_tasks','fc2sfc_triggers','pending_putaway']
for t in tables:
    age = db.age_minutes(t)
    status = "✅" if age < 60 else "⚠️" if age < 720 else "❌"
    print(f"  {status} {t}: {age:.0f} min ago")
