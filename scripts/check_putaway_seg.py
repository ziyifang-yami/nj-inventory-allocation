import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()
print(run("grep -n 'pa_seg\\|pa_zone\\|seg_order\\|Receiving Zone\\|TOP.*MIDDLE.*TAIL' /opt/inv-allocation/app.py | head -8"))
time.sleep(60)
