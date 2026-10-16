import subprocess, time

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
SCP = ['scp','-o','ConnectTimeout=10','-o','BatchMode=yes']

def ssh(cmd, timeout=20):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()

def scp(src, dst):
    r = subprocess.run(SCP+[src,f'root@184.33.17.231:{dst}'],capture_output=True,text=True,timeout=30)
    print(f"  {src.split(chr(92))[-1]}: rc={r.returncode} {r.stderr or 'OK'}")

print("=== Upload fc_to_sfc.py ===")
scp('logic/fc_to_sfc.py', '/opt/inv-allocation/logic/fc_to_sfc.py')

print("\n=== Verify key changes ===")
print(ssh("grep -n 'StockFirst\\|excess_qty\\|candidates\\|inv_age_days\\|startswith.*V' /opt/inv-allocation/logic/fc_to_sfc.py | head -12"))

# Clear fc2sfc_tasks meta + trigger refresh
print("\n=== Force fc2sfc re-run ===")
subprocess.Popen(SSH + ['bash /tmp/force_8b.sh'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(5)
print(ssh("ps aux | grep refresh_sfc_cache | grep -v grep | awk '{print $2,$9}'"))

print("\nPolling (~7 min)...")
for i in range(20):
    time.sleep(30)
    proc = ssh("ps aux | grep refresh_sfc_cache | grep -v grep | head -1")
    log  = ssh("tail -1 /opt/inv-allocation/results/refresh_log.txt")
    lc   = log.split('LOC_CLEAR=')[1].split(' ')[0] if 'LOC_CLEAR=' in log else '?'
    ts   = log[1:20] if log.startswith('[') else '...'
    print(f"  [{i+1}] {'running' if proc else 'DONE'}  {ts}  LOC_CLEAR={lc}", flush=True)
    if not proc and 'LOC_CLEAR=' in log and 'cache hit' not in log:
        print(f"\n=== DONE ===\n{log}")
        # Check rule labels in fc2sfc_tasks
        print("\n=== fc2sfc_tasks rule sample ===")
        print(ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT rule,COUNT(*) FROM fc2sfc_tasks GROUP BY rule;'"))
        break

time.sleep(60)
print("done")
