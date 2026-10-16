import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

# Check if TBD in sales SQLite (inv_days_30 < 30)
script = """
cd /opt/inv-allocation
.venv/bin/python3 - << 'EOF'
import sys; sys.path.insert(0,'/opt/inv-allocation')
from cache_db import CacheDB
from data.loader import load, load_inventory
import pandas as pd, numpy as np
from config import P

db = CacheDB('results/sfc_cache.db')
df_sales, _ = load()
df_inv, _ = load_inventory()

df_sales['item_number'] = df_sales['item_number'].astype(str)
df_inv['item_number'] = df_inv['item_number'].astype(str)
df = df_inv.merge(df_sales.drop(columns=['fc_qty','sfc_qty'],errors='ignore'), on='item_number', how='left')
df['fc_qty'] = pd.to_numeric(df.get('fc_qty',0),errors='coerce').fillna(0)
df['sfc_qty'] = pd.to_numeric(df.get('sfc_qty',0),errors='coerce').fillna(0)
df = df[(df['fc_qty']>0)|(df['sfc_qty']>0)].copy()

for c in ['active_order_days','order_lines_30d','avg_daily_orders','sales_units_30d']:
    df[c] = pd.to_numeric(df.get(c,0),errors='coerce').fillna(0)

aod_top,ol_top,ol_tail = P['aod_top'],P['ol_top'],P['ol_tail']
_aod = df['active_order_days'].to_numpy(float)
_ol  = df['order_lines_30d'].to_numpy(float)
df['seg'] = np.where((_aod>=aod_top)&(_ol>=ol_top),'TOP',np.where(_ol<ol_tail,'TAIL','MIDDLE'))
if 'inv_days_30' in df.columns:
    df.loc[df['inv_days_30'].isna()|(df['inv_days_30']<30),'seg'] = 'TBD'

print('Seg counts:', df['seg'].value_counts().to_dict())
print('TBD sample inv_days_30:', df[df['seg']=='TBD']['inv_days_30'].describe())
EOF
"""
print(run(script))
time.sleep(60)
