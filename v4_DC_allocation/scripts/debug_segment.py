import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

# What does trigger_type look like in each list?
print("=== trigger_type values per list ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id, trigger_type, COUNT(*) FROM fc2sfc_tasks GROUP BY list_id, trigger_type LIMIT 20;\""))

print("\n=== sample LOC_CLEAR rows ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id,trigger_type,item_number,location_no FROM fc2sfc_tasks WHERE list_id='LOC_CLEAR' LIMIT 5;\""))

time.sleep(60)
