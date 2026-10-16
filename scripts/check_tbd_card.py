import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== TBD card in server app.py ===")
print(run("grep -n 'TBD\\|sc4\\|sc1.*sc2.*sc3\\|columns.*4' /opt/inv-allocation/app.py | head -15"))
time.sleep(60)
