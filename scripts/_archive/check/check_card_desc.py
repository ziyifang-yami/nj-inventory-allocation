import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== card description line on server ===")
print(run("grep -n 'FC primary\\|Dual warehouse\\|SFC primary\\|New products' /opt/inv-allocation/app.py | head -5"))
print("\n=== local line 623-625 ===")
time.sleep(60)
