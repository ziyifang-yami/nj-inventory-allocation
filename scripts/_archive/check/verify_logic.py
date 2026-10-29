import subprocess, time, textwrap

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=60)
    return r.stdout.strip()

# 1. Confirm which code is on server
print("=== SERVER CODE — key LOC_CLEAR lines ===")
print(run("grep -n 'loc_live_skus\\|issubset\\|locs_with_tasks\\|loc_total_skus' /opt/inv-allocation/refresh_sfc_cache.py | head -15"))

# 2. B00911 check
print("\n=== B00911 in LOC_CLEAR? ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id,location_no,item_number FROM fc2sfc_tasks WHERE location_no='B00911';\""))

# 3. Write a standalone python file to server and run it
pycode = textwrap.dedent("""
import sqlite3, pymysql, os
from dotenv import load_dotenv
load_dotenv('/opt/inv-allocation/.env')

db = sqlite3.connect('/opt/inv-allocation/results/sfc_cache.db')
rows = db.execute("SELECT location_no, item_number FROM fc2sfc_tasks WHERE list_id='LOC_CLEAR' ORDER BY location_no LIMIT 15").fetchall()
loc_xfer = {}
for loc, sku in rows:
    loc_xfer.setdefault(loc, set()).add(str(sku))
sample = list(loc_xfer.keys())[:3]

triggers = db.execute("SELECT item_number FROM fc2sfc_triggers").fetchall()
transfer_set = set(str(r[0]) for r in triggers)
db.close()

conn = pymysql.connect(
    host=os.getenv('MYSQL_HOST'), user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASS'), port=int(os.getenv('MYSQL_PORT','3306')), charset='utf8mb4'
)
with conn.cursor() as cur:
    ph = ','.join(['%s']*len(sample))
    sql = (
        "SELECT li.location_no, li.item_number, SUM(li.quantity) as qty "
        "FROM yamibuy_wh.wh_lot_inventory li "
        "JOIN yamibuy_wh.wh_storage_location wsl "
        "ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number "
        "WHERE li.warehouse_number='002' AND li.quantity>0 "
        "AND wsl.location_type IN (3,4) AND wsl.zone_id!=2 "
        f"AND li.location_no IN ({ph}) "
        "GROUP BY li.location_no, li.item_number"
    )
    cur.execute(sql, sample)
    live_rows = cur.fetchall()
conn.close()

loc_live = {}
for loc, sku, qty in live_rows:
    loc_live.setdefault(loc, set()).add(str(sku))

errors = 0
for loc in sample:
    live = loc_live.get(loc, set())
    xfer = loc_xfer.get(loc, set())
    extra = live - transfer_set
    status = "OK" if not extra else f"WRONG extra={extra}"
    print(f"{loc}: live={sorted(live)} transfer={sorted(xfer)} -> {status}")
    if extra:
        errors += 1
print(f"Errors: {errors}/3")
""").strip()

# Write to server
upload = subprocess.run(
    SSH + ['cat > /tmp/check_lc.py'],
    input=pycode, capture_output=True, text=True, timeout=15
)
print("\n=== Sample LOC_CLEAR verification ===")
print(run("/opt/inv-allocation/.venv/bin/python /tmp/check_lc.py"))

time.sleep(60)
print("done")
