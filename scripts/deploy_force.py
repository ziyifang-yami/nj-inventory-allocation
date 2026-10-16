import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
SCP = ['scp','-o','ConnectTimeout=10','-o','BatchMode=yes']

def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

def scp(src, dst):
    r = subprocess.run(SCP+[src, f'root@184.33.17.231:{dst}'], capture_output=True, text=True, timeout=30)
    print(f"  {src}: rc={r.returncode} {r.stderr or 'OK'}")

print("=== Upload ===")
scp('app.py',          '/opt/inv-allocation/app.py')
scp('app_test_sfc.py', '/opt/inv-allocation/app_test_sfc.py')

print("\n=== Verify T1 label removed on server ===")
print("app_test_sfc.py:")
print(run("grep -n 'TOP=7d\\|MID=7d\\|_t1_label' /opt/inv-allocation/app_test_sfc.py | head -10"))
print("app.py:")
print(run("grep -n 'TOP=7d\\|MID=7d\\|extra.*7d' /opt/inv-allocation/app.py | head -5"))

time.sleep(60)
