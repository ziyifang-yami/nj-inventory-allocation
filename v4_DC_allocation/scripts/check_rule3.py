import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== PRAGMA table_info fc2sfc_tasks ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'PRAGMA table_info(fc2sfc_tasks);'"))

print("\n=== first LOC_CLEAR row (all cols) ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT * FROM fc2sfc_tasks WHERE list_id=''LOC_CLEAR'' LIMIT 1;'"))

print("\n=== first MIDDLE_EVICT row (all cols) ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT * FROM fc2sfc_tasks WHERE list_id=''MIDDLE_EVICT'' LIMIT 1;'"))

print("\n=== rule column sample ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT rule FROM fc2sfc_tasks LIMIT 5;'"))

print("\n=== segment column sample ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT segment FROM fc2sfc_tasks WHERE list_id=''MIDDLE_EVICT'' LIMIT 5;'"))

time.sleep(60)
