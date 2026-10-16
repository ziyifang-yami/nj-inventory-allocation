import subprocess, time

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def ssh(cmd, timeout=30):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip()

# Check meta table
print("=== meta table ===")
out, _ = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT table_name, updated_at FROM meta ORDER BY updated_at DESC;'")
print(out)

# Kill any running refresh
print("\n=== kill any running refresh ===")
out, _ = ssh("pkill -f refresh_sfc_cache.py 2>/dev/null; sleep 2; echo killed")
print(out)

# Remove stale lock
out, _ = ssh("rm -f /opt/inv-allocation/results/refresh.lock; echo lock_removed")
print(out)

# Hard delete fc2sfc_tasks meta
print("\n=== hard delete fc2sfc_tasks meta ===")
out, err = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"DELETE FROM meta WHERE table_name='fc2sfc_tasks';\" && echo deleted")
print(out, err)

# Verify meta is gone
out, _ = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT table_name, updated_at FROM meta WHERE table_name=''fc2sfc_tasks'';'")
print(f"fc2sfc_tasks meta after delete: '{out}' (should be empty)")

# Start fresh refresh
print("\n=== start fresh refresh ===")
subprocess.Popen(SSH + ['bash /tmp/force_8b.sh'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(5)
out, _ = ssh("ps aux | grep refresh_sfc_cache | grep -v grep | awk '{print $2,$9,$11}'")
print(f"Process: {out}")

# Poll
print("\nPolling every 30s...")
for i in range(25):
    time.sleep(30)
    proc, _ = ssh("ps aux | grep refresh_sfc_cache | grep -v grep | head -1")
    log, _  = ssh("tail -1 /opt/inv-allocation/results/refresh_log.txt")
    ts  = log[1:20] if log.startswith('[') else '...'
    lc  = log.split('LOC_CLEAR=')[1].split(' ')[0] if 'LOC_CLEAR=' in log else '?'
    print(f"  [{i+1}] {'running' if proc else 'DONE'}  {ts}  LOC_CLEAR={lc}", flush=True)
    if not proc and 'LOC_CLEAR=' in log and 'cache hit' not in log:
        print(f"\n=== COMPLETE ===\n{log}")
        # check meta timestamp
        out2, _ = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT table_name, updated_at FROM meta WHERE table_name=''fc2sfc_tasks'';'")
        print(f"fc2sfc_tasks meta: {out2}")
        break

time.sleep(60)
print("done")
