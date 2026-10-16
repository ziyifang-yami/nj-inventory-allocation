import subprocess, time
SCP = ['scp','-o','ConnectTimeout=10','-o','BatchMode=yes']
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']

r1 = subprocess.run(SCP+['app.py','root@184.33.17.231:/opt/inv-allocation/app.py'],capture_output=True,text=True,timeout=30)
r2 = subprocess.run(SCP+['app_test_sfc.py','root@184.33.17.231:/opt/inv-allocation/app_test_sfc.py'],capture_output=True,text=True,timeout=30)
print('app.py:', r1.returncode, r1.stderr or 'OK')
print('app_test_sfc.py:', r2.returncode, r2.stderr or 'OK')

# verify
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("\nVerify sort on server:")
print(run("grep -n 'fc_d2s.*T1\\|T1.*fc_d2s\\|sort_cols.*fc_d2s\\|fc_d2s.*append' /opt/inv-allocation/app.py | head -5"))
print(run("grep -n 'fc_d2s.*T1\\|T1.*fc_d2s\\|sort_cols.*fc_d2s\\|fc_d2s.*append' /opt/inv-allocation/app_test_sfc.py | head -5"))

time.sleep(60)
