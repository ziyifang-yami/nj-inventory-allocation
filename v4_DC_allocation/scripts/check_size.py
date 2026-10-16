import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

script = """
cd /opt/inv-allocation
.venv/bin/python3 - << 'EOF'
import pymysql, os
from dotenv import load_dotenv
load_dotenv('/opt/inv-allocation/.env')
conn = pymysql.connect(host=os.getenv('MYSQL_HOST'), user=os.getenv('MYSQL_USER'),
                       password=os.getenv('MYSQL_PASS'), port=int(os.getenv('MYSQL_PORT','3306')), charset='utf8mb4')
with conn.cursor() as cur:
    # What size_ids exist in FC zone locations (zone_id != 2)?
    cur.execute('''
        SELECT wsl.size_id, wls.description, wls.storage_type,
               COUNT(*) as loc_count
        FROM yamibuy_wh.wh_storage_location wsl
        JOIN yamibuy_wh.wh_location_size wls ON wls.size_id = wsl.size_id
        WHERE wsl.warehouse_number = '002'
          AND wsl.location_type IN (3,4)
          AND wsl.zone_id != 2
        GROUP BY wsl.size_id, wls.description, wls.storage_type
        ORDER BY wsl.size_id
    ''')
    rows = cur.fetchall()
    print("size_id | description | storage_type | loc_count")
    for r in rows:
        print(f"  {r[0]} | {r[1]} | {r[2]} | {r[3]}")

    # Sample: what size_id does B00911 have?
    cur.execute("SELECT location_no, size_id FROM yamibuy_wh.wh_storage_location WHERE warehouse_number='002' AND location_no IN ('B00911','B01622','C21053A') ")
    print("\\nSample locations:")
    for r in cur.fetchall():
        print(f"  {r[0]} -> size_id={r[1]}")
conn.close()
EOF
"""
print(run(script))
time.sleep(60)
