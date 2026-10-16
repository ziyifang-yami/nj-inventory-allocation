import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
SCP = ['scp','-o','ConnectTimeout=10','-o','BatchMode=yes']

r = subprocess.run(SCP+['app.py','root@184.33.17.231:/opt/inv-allocation/app.py'],capture_output=True,text=True,timeout=30)
print('SCP rc:', r.returncode, r.stderr or 'OK')

# Verify the key lines
r2 = subprocess.run(SSH+['grep -n "sc4\\|TBD.*9E9E9E\\|v2.*aod_top" /opt/inv-allocation/app.py | head -8'],capture_output=True,text=True,timeout=20)
print('Server check:', r2.stdout.strip())

time.sleep(60)
