import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== process ===")
print(run("ps aux | grep refresh_sfc_cache | grep -v grep | awk '{print $2,$9,$11}'"))
print("=== last log ===")
print(run("tail -2 /opt/inv-allocation/results/refresh_log.txt | grep -v 'cache hit'"))
print("=== loc_size sample ===")
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT DISTINCT loc_size FROM fc2sfc_tasks WHERE loc_size IS NOT NULL AND loc_size != '' LIMIT 8;\""))
time.sleep(60)
