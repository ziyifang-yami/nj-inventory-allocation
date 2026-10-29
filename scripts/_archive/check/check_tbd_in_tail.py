import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

# FC→SFC TAIL_CLEAR 里有没有 TBD SKU
print("=== TBD in fc2sfc TAIL_CLEAR ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT COUNT(*) FROM fc2sfc_tasks t JOIN tbd_skus b ON t.item_number=b.item_number WHERE t.list_id='TAIL_CLEAR';\""))

# SFC→FC task_list T1-T4 里有没有 TBD SKU
print("\n=== TBD in SFC→FC task_list ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id, COUNT(*) FROM task_list t JOIN tbd_skus b ON t.item_number=b.item_number GROUP BY list_id;\""))

# Sample of TBD in TAIL_CLEAR
print("\n=== Sample TBD in TAIL_CLEAR ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT t.item_number,t.location_no FROM fc2sfc_tasks t JOIN tbd_skus b ON t.item_number=b.item_number WHERE t.list_id='TAIL_CLEAR' LIMIT 5;\""))

time.sleep(60)
