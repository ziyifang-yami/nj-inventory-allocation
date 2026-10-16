"""Deep-verify 10 LOC_CLEAR locations against live MySQL."""
import subprocess, sys, time

HOST = "root@184.33.17.231"
SSH_OPTS = ["-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=3", "-o", "BatchMode=yes"]

def ssh(cmd):
    r = subprocess.run(["ssh"] + SSH_OPTS + [HOST, cmd],
                       capture_output=True, text=True, timeout=120)
    return r.stdout.strip(), r.stderr.strip()

# Write the check script to server then run it
check_script = """import sqlite3, pymysql, os
from dotenv import load_dotenv
load_dotenv('/opt/inv-allocation/.env')

db = sqlite3.connect('/opt/inv-allocation/results/sfc_cache.db')
rows = db.execute("SELECT location_no, item_number FROM fc2sfc_tasks WHERE list_id='LOC_CLEAR' ORDER BY location_no LIMIT 30").fetchall()
loc_transfer = {}
for loc, sku in rows:
    loc_transfer.setdefault(loc, set()).add(str(sku))
sample_locs = list(loc_transfer.keys())[:10]
db.close()

conn = pymysql.connect(host=os.getenv('MYSQL_HOST'), user=os.getenv('MYSQL_USER'), password=os.getenv('MYSQL_PASS'), port=int(os.getenv('MYSQL_PORT','3306')), charset='utf8mb4')
with conn.cursor() as cur:
    ph = ','.join(['%s']*len(sample_locs))
    cur.execute(f"SELECT li.location_no, li.item_number, SUM(li.quantity) as qty FROM yamibuy_wh.wh_lot_inventory li JOIN yamibuy_wh.wh_storage_location wsl ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number WHERE li.warehouse_number='002' AND li.quantity>0 AND wsl.location_type IN (3,4) AND wsl.zone_id!=2 AND li.location_no IN ({ph}) GROUP BY li.location_no, li.item_number HAVING SUM(li.quantity)>0", sample_locs)
    live = cur.fetchall()
conn.close()

loc_live = {}
for loc, sku, qty in live:
    loc_live.setdefault(loc, set()).add(str(sku))

errors = 0
for loc in sample_locs:
    transfer = loc_transfer.get(loc, set())
    live_set  = loc_live.get(loc, set())
    non_transfer = live_set - transfer
    status = "OK" if not non_transfer else f"WRONG extra={sorted(non_transfer)}"
    print(f"{loc}: live={sorted(live_set)} xfer={sorted(transfer)} -> {status}")
    if non_transfer:
        errors += 1
print(f"Errors: {errors}/{len(sample_locs)}")
"""

# Write script to server
with open("_tmp_check.py", "w") as f:
    f.write(check_script)

import subprocess
r = subprocess.run(["scp", "-o", "ConnectTimeout=10", "_tmp_check.py",
                    "root@184.33.17.231:/tmp/check_loc.py"],
                   capture_output=True, text=True, timeout=20)
print("SCP:", r.returncode, r.stderr)

out, err = ssh("cd /opt/inv-allocation && .venv/bin/python /tmp/check_loc.py 2>&1")
print(out)
if err:
    print("ERR:", err)

time.sleep(60)
print("done")
