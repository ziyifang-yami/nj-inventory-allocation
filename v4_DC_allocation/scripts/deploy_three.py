import subprocess, time
SCP = ['scp','-o','ConnectTimeout=10','-o','BatchMode=yes']
files = [
    ('app.py',              '/opt/inv-allocation/app.py'),
    ('app_test_sfc.py',     '/opt/inv-allocation/app_test_sfc.py'),
    ('app_test_fc2sfc.py',  '/opt/inv-allocation/app_test_fc2sfc.py'),
]
for src, dst in files:
    r = subprocess.run(SCP+[src, f'root@184.33.17.231:{dst}'], capture_output=True, text=True, timeout=30)
    print(f'{src}: rc={r.returncode} {r.stderr or "OK"}')
time.sleep(60)
