import subprocess, time

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
SCP = ['scp','-o','ConnectTimeout=10','-o','BatchMode=yes']

def ssh(cmd, timeout=20):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip()

def scp(src, dst):
    r = subprocess.run(SCP+[src, f'root@184.33.17.231:{dst}'], capture_output=True, text=True, timeout=30)
    print(f"  {src.split(chr(92))[-1]}: rc={r.returncode} {r.stderr or 'OK'}")

print("=== Upload ===")
scp('cache_db.py',        '/opt/inv-allocation/cache_db.py')
scp('data/loader.py',     '/opt/inv-allocation/data/loader.py')
scp('logic/fc_to_sfc.py', '/opt/inv-allocation/logic/fc_to_sfc.py')
scp('app_test_fc2sfc.py', '/opt/inv-allocation/app_test_fc2sfc.py')

print("\n=== DB migration ===")
out, _ = ssh("cd /opt/inv-allocation && .venv/bin/python -c \"from cache_db import CacheDB; db=CacheDB('results/sfc_cache.db'); print('done')\"")
print(out)

print("\n=== Verify new columns ===")
out, _ = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'PRAGMA table_info(fc2sfc_tasks);' | grep -E 'loc_size|size_id|segment'")
print(out)

print("\n=== Force 8b ===")
subprocess.Popen(SSH + ['bash /tmp/force_8b.sh'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(5)
out, _ = ssh("ps aux | grep refresh_sfc_cache | grep -v grep | awk '{print $2,$9}'")
print(f"Process: {out}")

# Poll
print("\nPolling (~7 min)...")
for i in range(20):
    time.sleep(30)
    proc, _ = ssh("ps aux | grep refresh_sfc_cache | grep -v grep | head -1")
    log,  _ = ssh("tail -1 /opt/inv-allocation/results/refresh_log.txt")
    lc = log.split('LOC_CLEAR=')[1].split(' ')[0] if 'LOC_CLEAR=' in log else '?'
    ts = log[1:20] if log.startswith('[') else '...'
    print(f"  [{i+1}] {'running' if proc else 'DONE'}  {ts}  LOC_CLEAR={lc}", flush=True)
    if not proc and 'LOC_CLEAR=' in log and 'cache hit' not in log:
        # Verify loc_size populated
        out, _ = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT loc_size, COUNT(*) FROM fc2sfc_tasks WHERE list_id='LOC_CLEAR' GROUP BY loc_size LIMIT 8;\"")
        print(f"\n=== loc_size values ===\n{out}")
        out2, _ = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT segment, COUNT(*) FROM fc2sfc_tasks GROUP BY segment;\"")
        print(f"\n=== segment values ===\n{out2}")
        break

time.sleep(60)
print("done")
