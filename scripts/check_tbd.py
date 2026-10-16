import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== TBD count ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT COUNT(*) FROM tbd_skus;'"))

print("\n=== TBD SKUs in fc2sfc_triggers ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT COUNT(*) FROM fc2sfc_triggers t INNER JOIN tbd_skus b ON t.item_number=b.item_number;'"))

print("\n=== TBD SKUs in fc2sfc_tasks ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT COUNT(*) FROM fc2sfc_tasks t INNER JOIN tbd_skus b ON t.item_number=b.item_number;'"))

print("\n=== TBD SKUs in SFC→FC task_list ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT list_id, COUNT(*) FROM task_list t INNER JOIN tbd_skus b ON t.item_number=b.item_number GROUP BY list_id;'"))

time.sleep(60)
