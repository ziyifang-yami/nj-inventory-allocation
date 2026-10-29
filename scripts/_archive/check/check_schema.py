import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== fc2sfc_tasks columns ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'PRAGMA table_info(fc2sfc_tasks);'"))

print("\n=== sample row (all columns) ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT * FROM fc2sfc_tasks LIMIT 1;'"))

time.sleep(60)
