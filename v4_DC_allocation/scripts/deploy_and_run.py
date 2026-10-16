import subprocess, time

SSH  = ['ssh', '-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
SCP  = ['scp', '-o','ConnectTimeout=10','-o','BatchMode=yes']
LOCAL = r'c:\Project-Z\NJ_Inventory_Allocation\v4_DC_allocation\refresh_sfc_cache.py'

def ssh(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=30)
    return r.stdout.strip(), r.stderr.strip()

# 1. Upload
print("Uploading new code...")
r = subprocess.run(SCP+[LOCAL,'root@184.33.17.231:/tmp/refresh_new.py'],
                   capture_output=True, text=True, timeout=30)
print("SCP:", r.returncode, r.stderr.strip() or "OK")

# 2. Deploy
out, err = ssh("cp /tmp/refresh_new.py /opt/inv-allocation/refresh_sfc_cache.py && echo deployed")
print("Deploy:", out, err)

# 3. Verify new logic keyword
out, _ = ssh("grep -c 'loc_tasked_skus' /opt/inv-allocation/refresh_sfc_cache.py")
print(f"loc_tasked_skus occurrences on server: {out} (expect 3+)")

# 4. Kill any running refresh, clear lock + meta, start fresh
print("\nClearing cache meta and starting fresh run...")
out, _ = ssh("pkill -f refresh_sfc_cache.py 2>/dev/null; sleep 2; rm -f /opt/inv-allocation/results/refresh.lock; echo killed")
print("Kill:", out)
out, err = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"DELETE FROM meta WHERE table_name='fc2sfc_tasks';\" && echo cleared")
print("Meta clear:", out, err)
out, _ = ssh("cd /opt/inv-allocation && nohup .venv/bin/python refresh_sfc_cache.py >> results/refresh.log 2>&1 & echo PID:$!")
print("Started:", out)

# 5. Poll until done
pid = out.replace("PID:","").strip()
print(f"\nPolling for PID {pid} to finish (~7-8 min)...")
for i in range(25):
    time.sleep(30)
    proc, _ = ssh(f"ps -p {pid} --no-headers 2>/dev/null | head -1")
    log,  _ = ssh("tail -1 /opt/inv-allocation/results/refresh_log.txt")
    lc = log.split('LOC_CLEAR=')[1].split(' ')[0] if 'LOC_CLEAR=' in log else '?'
    ts = log[1:20] if log.startswith('[') else ''
    print(f"  [{i+1}] {'running' if proc else 'DONE'}  ts={ts}  LOC_CLEAR={lc}", flush=True)
    if not proc and 'LOC_CLEAR=' in log and 'cache hit' not in log:
        print("\n=== FINAL ===")
        print(log)
        # Quick B00911 check
        out2, _ = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT list_id,location_no,item_number FROM fc2sfc_tasks WHERE location_no='B00911';\"")
        print(f"\nB00911 in tasks: {out2 or '(none — correct!)'}")
        break

time.sleep(60)
print("done")
