import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== All meta timestamps ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT table_name,updated_at FROM meta ORDER BY updated_at DESC;'"))

print("\n=== fc2sfc_tasks row count ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT list_id,COUNT(*) FROM fc2sfc_tasks GROUP BY list_id;'"))

print("\n=== write_fc2sfc_tasks meta logic on server ===")
print(run("grep -n 'fc2sfc_tasks.*updated_at\\|INSERT.*meta.*fc2sfc' /opt/inv-allocation/cache_db.py | head -5"))

time.sleep(60)
