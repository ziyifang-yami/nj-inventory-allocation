"""Deploy latest code + force 8b re-run cleanly."""
import subprocess, time

SSH  = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
SCP  = ['scp','-o','ConnectTimeout=10','-o','BatchMode=yes']
LOCAL = r'c:\Project-Z\NJ_Inventory_Allocation\v4_DC_allocation\refresh_sfc_cache.py'

def run(cmd, timeout=20):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip()

# Upload
print("Uploading...")
r = subprocess.run(SCP+[LOCAL,'root@184.33.17.231:/tmp/refresh_new.py'],
                   capture_output=True, text=True, timeout=30)
print(f"SCP: rc={r.returncode}")

# Deploy
out, err = run("cp /tmp/refresh_new.py /opt/inv-allocation/refresh_sfc_cache.py && echo deployed")
print(f"Deploy: {out} {err}")

# Verify keyword
out, _ = run("grep -c 'loc_tasked_skus' /opt/inv-allocation/refresh_sfc_cache.py")
print(f"loc_tasked_skus occurrences: {out} (expect 3+, key fix: built from task rows)")

# Kill any running + clear lock + clear meta
run("pkill -f refresh_sfc_cache.py 2>/dev/null; sleep 1; rm -f /opt/inv-allocation/results/refresh.lock")
out, _ = run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"DELETE FROM meta WHERE table_name='fc2sfc_tasks';\" && echo cleared")
print(f"Meta: {out}")

# Launch via force_8b.sh which does it reliably
subprocess.Popen(SSH + ['bash /tmp/force_8b.sh'],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

# Confirm started
out, _ = run("ps aux | grep refresh_sfc_cache | grep -v grep | awk '{print $2, $9, $11}' | head -2")
print(f"Process: {out}")

# Poll
print("\nPolling...")
for i in range(25):
    time.sleep(30)
    proc, _ = run("ps aux | grep refresh_sfc_cache | grep -v grep | head -1")
    log, _  = run("tail -1 /opt/inv-allocation/results/refresh_log.txt")
    lc = log.split('LOC_CLEAR=')[1].split(' ')[0] if 'LOC_CLEAR=' in log else '?'
    ts = log[1:20] if log.startswith('[') else '...'
    print(f"  [{i+1}] {'running' if proc else 'DONE'}  {ts}  LOC_CLEAR={lc}", flush=True)
    if not proc and 'LOC_CLEAR=' in log and 'cache hit' not in log:
        # Check B00911
        b, _ = run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id,location_no,item_number FROM fc2sfc_tasks WHERE location_no='B00911';\"")
        print(f"\n=== RESULT ===\n{log}")
        print(f"B00911: {b or '(none — correctly excluded!)'}")
        break

time.sleep(60)
print("done")
