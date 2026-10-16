import subprocess, time

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def ssh(cmd, timeout=20):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()

print("=== current refresh processes ===")
print(ssh("ps aux | grep refresh_sfc_cache | grep -v grep"))
print("=== lock file ===")
print(ssh("cat /opt/inv-allocation/results/refresh.lock 2>/dev/null || echo NO_LOCK"))

# Ensure meta is cleared and a refresh is running
meta = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT updated_at FROM meta WHERE table_name='fc2sfc_tasks';\"")
print(f"=== fc2sfc_tasks meta: {meta} ===")

proc = ssh("ps aux | grep refresh_sfc_cache | grep -v grep")
if not proc:
    print("No refresh running — starting one with cleared meta...")
    ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"DELETE FROM meta WHERE table_name='fc2sfc_tasks';\"")
    ssh("rm -f /opt/inv-allocation/results/refresh.lock")
    # Use bash -c with timeout to detach properly
    subprocess.Popen(
        SSH + ['bash /tmp/force_8b.sh'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(5)
    print("Started. Polling...")
else:
    print("Refresh already running. Polling...")

# Poll every 30s until done
for i in range(25):
    time.sleep(30)
    proc = ssh("ps aux | grep refresh_sfc_cache | grep -v grep | head -1")
    log  = ssh("tail -1 /opt/inv-allocation/results/refresh_log.txt")
    lc = log.split('LOC_CLEAR=')[1].split(' ')[0] if 'LOC_CLEAR=' in log else '?'
    ts = log[1:20] if log.startswith('[') else '...'
    print(f"[{i+1}] {'running' if proc else 'DONE'}  {ts}  LOC_CLEAR={lc}", flush=True)
    if not proc and 'LOC_CLEAR=' in log and 'cache hit' not in log:
        print("\n=== COMPLETE ===")
        print(log)
        b = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id,location_no,item_number FROM fc2sfc_tasks WHERE location_no='B00911';\"")
        print(f"B00911 in tasks: '{b}' (should be empty)")
        break

time.sleep(60)
