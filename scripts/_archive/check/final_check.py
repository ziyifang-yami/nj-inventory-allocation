import subprocess, time

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== Last log line ===")
print(run("tail -1 /opt/inv-allocation/results/refresh_log.txt"))

print("\n=== B00911 in tasks now ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id,location_no,item_number FROM fc2sfc_tasks WHERE location_no='B00911';\"") or "(none — B00911 correctly excluded)")

print("\n=== LOC_CLEAR / MIDDLE / TAIL counts ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id,COUNT(*) as rows,COUNT(DISTINCT location_no) as locs FROM fc2sfc_tasks GROUP BY list_id;\""))

time.sleep(60)
