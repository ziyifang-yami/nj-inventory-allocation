import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== TOP=7d in app_test_sfc.py on server ===")
print(run("grep -n 'TOP=\\|MID=\\|7d\\|extra.*cap\\|_t1_label\\|t1_top\\|target' /opt/inv-allocation/app_test_sfc.py | grep -v '#' | head -20"))

time.sleep(60)
