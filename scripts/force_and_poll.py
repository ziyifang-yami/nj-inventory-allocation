import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def ssh(cmd, timeout=20):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()

# Clear meta and start refresh
print("Clearing fc2sfc_tasks meta...")
subprocess.Popen(SSH + ['bash /tmp/force_8b.sh'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(5)
print("Process:", ssh("ps aux | grep refresh_sfc_cache | grep -v grep | awk '{print $2,$9}'"))

print("Polling (~7 min)...")
for i in range(20):
    time.sleep(30)
    proc = ssh("ps aux | grep refresh_sfc_cache | grep -v grep | head -1")
    log  = ssh("tail -1 /opt/inv-allocation/results/refresh_log.txt")
    lc   = log.split('LOC_CLEAR=')[1].split(' ')[0] if 'LOC_CLEAR=' in log else '?'
    ts   = log[1:20] if log.startswith('[') else '...'
    print(f"  [{i+1}] {'running' if proc else 'DONE'}  {ts}  LOC_CLEAR={lc}", flush=True)
    if not proc and 'LOC_CLEAR=' in log and 'cache hit' not in log:
        r = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT COUNT(*) FROM fc2sfc_tasks WHERE item_number='1159022911';\"")
        tbd = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT COUNT(*) FROM tbd_skus;'")
        print(f"\n=== DONE ===\n{log}")
        print(f"1159022911 in tasks: {r} (expect 0)")
        print(f"TBD count: {tbd}")
        break

time.sleep(60)
