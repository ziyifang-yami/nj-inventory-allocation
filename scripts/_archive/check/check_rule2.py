import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

# Get distinct text rule values (not numeric)
print("=== distinct rule values per list ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id, rule, COUNT(*) FROM fc2sfc_tasks WHERE CAST(rule AS TEXT) NOT GLOB '*[0-9]*' GROUP BY list_id, rule ORDER BY list_id;\""))
time.sleep(60)
