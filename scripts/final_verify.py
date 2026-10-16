import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def ssh(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("last log:", ssh("tail -1 /opt/inv-allocation/results/refresh_log.txt"))
print("TBD count:", ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT COUNT(*) FROM tbd_skus;'"))
print("1159022911 in tasks:", ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id,COUNT(*) FROM fc2sfc_tasks WHERE item_number='1159022911' GROUP BY list_id;\""))
print("LOC/MID/TAIL counts:", ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT list_id,COUNT(*) FROM fc2sfc_tasks GROUP BY list_id;'"))

time.sleep(60)
