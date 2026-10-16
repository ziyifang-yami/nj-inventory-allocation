import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== rule values per list_id ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id, rule, COUNT(*) FROM fc2sfc_tasks GROUP BY list_id, rule ORDER BY list_id, rule;\""))
time.sleep(60)
