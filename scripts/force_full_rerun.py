import subprocess, time

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=30)
    return r.stdout.strip(), r.stderr.strip()

# Step 1: kill any running refresh first
print("=== killing any running refresh ===")
out, _ = run("pkill -f refresh_sfc_cache.py 2>/dev/null; sleep 2; rm -f /opt/inv-allocation/results/refresh.lock; echo done")
print(out)

# Step 2: clear fc2sfc_tasks meta so 8b is forced
print("=== clearing fc2sfc_tasks meta ===")
out, err = run("cd /opt/inv-allocation && sqlite3 results/sfc_cache.db \"DELETE FROM meta WHERE table_name='fc2sfc_tasks';\" && echo cleared")
print(out, err)

# Step 3: launch fresh background refresh
print("=== launching fresh refresh ===")
out, _ = run("cd /opt/inv-allocation && nohup .venv/bin/python refresh_sfc_cache.py >> results/refresh.log 2>&1 & echo PID:$!")
print(out)

# Step 4: poll until done
print("\nPolling every 30s...")
for i in range(25):
    time.sleep(30)
    proc, _ = run("ps aux | grep refresh_sfc_cache | grep -v grep | awk '{print $1,$2,$11}'")
    log, _  = run("tail -1 /opt/inv-allocation/results/refresh_log.txt")
    ts = log[1:20] if log.startswith('[') else ''
    lc = ''
    if 'LOC_CLEAR=' in log:
        lc = log.split('LOC_CLEAR=')[1].split(' ')[0]
    print(f"[poll {i+1}] proc={proc or 'done'}  ts={ts}  LOC_CLEAR={lc}", flush=True)
    if not proc and log and 'fc2sfc_tasks:' in log and 'cache hit' not in log:
        print("\n=== DONE — full result ===")
        print(log)
        break

time.sleep(60)
