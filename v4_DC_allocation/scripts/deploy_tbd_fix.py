import subprocess, time

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
SCP = ['scp','-o','ConnectTimeout=10','-o','BatchMode=yes']

def ssh(cmd, timeout=20):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()

def scp(src, dst):
    r = subprocess.run(SCP+[src, f'root@184.33.17.231:{dst}'], capture_output=True, text=True, timeout=30)
    print(f"  {src.split(chr(92))[-1]}: rc={r.returncode} {r.stderr or 'OK'}")

print("=== Upload ===")
scp('refresh_sfc_cache.py', '/opt/inv-allocation/refresh_sfc_cache.py')
scp('app.py',               '/opt/inv-allocation/app.py')
scp('app_test_fc2sfc.py',   '/opt/inv-allocation/app_test_fc2sfc.py')
scp('app_test_sfc.py',      '/opt/inv-allocation/app_test_sfc.py')

print("\n=== Verify fix on server ===")
print(ssh("grep -n 'isna\\|TBD' /opt/inv-allocation/refresh_sfc_cache.py | grep -i tbd | head -5"))

print("\n=== Force full re-run (8b) ===")
subprocess.Popen(SSH + ['bash /tmp/force_8b.sh'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(5)
print(ssh("ps aux | grep refresh_sfc_cache | grep -v grep | awk '{print $2,$9}'"))

# Poll
print("\nPolling (~7 min)...")
for i in range(20):
    time.sleep(30)
    proc = ssh("ps aux | grep refresh_sfc_cache | grep -v grep | head -1")
    log  = ssh("tail -1 /opt/inv-allocation/results/refresh_log.txt")
    lc = log.split('LOC_CLEAR=')[1].split(' ')[0] if 'LOC_CLEAR=' in log else '?'
    ts = log[1:20] if log.startswith('[') else '...'
    print(f"  [{i+1}] {'running' if proc else 'DONE'}  {ts}  LOC_CLEAR={lc}", flush=True)
    if not proc and 'LOC_CLEAR=' in log and 'cache hit' not in log:
        # Check 1159022911 no longer in tasks
        r = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT COUNT(*) FROM fc2sfc_tasks WHERE item_number='1159022911';\"")
        tbd_count = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT COUNT(*) FROM tbd_skus;'")
        print(f"\n=== DONE ===\n{log}")
        print(f"1159022911 in fc2sfc_tasks: {r} (expect 0)")
        print(f"TBD count: {tbd_count}")
        break

time.sleep(60)
print("done")
