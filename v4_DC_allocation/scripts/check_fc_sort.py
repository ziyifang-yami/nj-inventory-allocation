import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== Sort logic on server ===")
print(run("grep -n 'StockFirst\\|_type_order\\|ascending.*True.*False' /opt/inv-allocation/logic/fc_to_sfc.py | head -10"))

time.sleep(60)
