import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== app_test_sfc.py header on server ===")
print(run("grep -n 'In-stock SKUs\\|Sales Segment\\|transit log\\|_n_instock' /opt/inv-allocation/app_test_sfc.py | head -8"))

print("\n=== app_test_fc2sfc.py caption on server ===")
print(run("grep -n 'n_locs\\|rows.*SKUs.*locs\\|location_no.*nunique' /opt/inv-allocation/app_test_fc2sfc.py | head -5"))

time.sleep(60)
