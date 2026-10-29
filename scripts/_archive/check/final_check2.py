import subprocess, time

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== Last 2 log lines ===")
print(run("tail -2 /opt/inv-allocation/results/refresh_log.txt"))

print("\n=== B00911 in tasks ===")
b = run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id,location_no,item_number FROM fc2sfc_tasks WHERE location_no='B00911';\"")
print(b or "(none — correctly excluded!)")

print("\n=== All list counts ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id,COUNT(*) rows,COUNT(DISTINCT location_no) locs FROM fc2sfc_tasks GROUP BY list_id;\""))

time.sleep(60)
