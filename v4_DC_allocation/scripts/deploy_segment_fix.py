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
scp('cache_db.py',        '/opt/inv-allocation/cache_db.py')
scp('app_test_fc2sfc.py', '/opt/inv-allocation/app_test_fc2sfc.py')

print("\n=== DB migration (add segment column) ===")
out = ssh("cd /opt/inv-allocation && .venv/bin/python -c \"from cache_db import CacheDB; db=CacheDB('results/sfc_cache.db'); print('done')\"")
print(out)

print("\n=== Verify columns ===")
print(ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'PRAGMA table_info(fc2sfc_tasks);' | grep -E 'segment|size_id'"))

print("\n=== Force 8b re-run ===")
subprocess.Popen(SSH + ['bash /tmp/force_8b.sh'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)
print(ssh("ps aux | grep refresh_sfc_cache | grep -v grep | awk '{print $2,$9}'"))
print("\nRefresh running (~7 min). Segment will be stored in DB after completion.")
time.sleep(120)
print("done")
