import subprocess, time

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
SCP = ['scp','-o','ConnectTimeout=10','-o','BatchMode=yes']

def ssh(cmd, timeout=20):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip()

def scp(src, dst):
    r = subprocess.run(SCP+[src, f'root@184.33.17.231:{dst}'], capture_output=True, text=True, timeout=30)
    print(f"  SCP {src.split('/')[-1].split(chr(92))[-1]}: rc={r.returncode} {r.stderr or 'OK'}")

print("=== Uploading files ===")
scp('cache_db.py',          '/opt/inv-allocation/cache_db.py')
scp('data/loader.py',       '/opt/inv-allocation/data/loader.py')
scp('app_test_fc2sfc.py',   '/opt/inv-allocation/app_test_fc2sfc.py')

# Run migration on existing DB (ALTER TABLE adds size_id)
print("\n=== Running DB migration ===")
out, _ = ssh("cd /opt/inv-allocation && .venv/bin/python -c \"from cache_db import CacheDB; db=CacheDB('results/sfc_cache.db'); print('migration done')\"")
print(out)

# Clear fc2sfc_tasks meta and force re-run to get size_id populated
print("\n=== Clearing fc2sfc_tasks meta ===")
subprocess.Popen(SSH + ['bash /tmp/force_8b.sh'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)
out, _ = ssh("ps aux | grep refresh_sfc_cache | grep -v grep | awk '{print $2,$9}'")
print(f"Refresh process: {out or 'not started yet'}")

print("\nRefresh takes ~7 min. size_id will populate after it completes.")
time.sleep(120)
print("done")
