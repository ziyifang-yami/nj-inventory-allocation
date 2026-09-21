"""Fix nginx: change /inv-allocation proxy_pass to not include path"""
nginx_conf = '/etc/nginx/sites-enabled/default'

with open(nginx_conf) as f:
    content = f.read()

# Fix proxy_pass to remove path suffix (root proxy)
content = content.replace(
    'proxy_pass http://127.0.0.1:8508/inv-allocation;',
    'proxy_pass http://127.0.0.1:8508;'
)

with open(nginx_conf, 'w') as f:
    f.write(content)

import subprocess
r = subprocess.run(['nginx', '-t'], capture_output=True, text=True)
print(r.stdout, r.stderr)
r2 = subprocess.run(['nginx', '-s', 'reload'], capture_output=True, text=True)
print(r2.stdout, r2.stderr)

# Start streamlit
import os
cmd = ('pkill -f "streamlit.*8508" 2>/dev/null; sleep 1; '
       'cd /opt/inv-allocation; '
       'nohup /opt/inbound_quality_simulator/.venv/bin/python -m streamlit run app.py '
       '--server.port=8508 --server.address=0.0.0.0 --server.headless=true '
       '--browser.gatherUsageStats=false > /var/log/inv-allocation.log 2>&1 &')
os.system(cmd)
import time; time.sleep(3)

r3 = subprocess.run(['ss', '-tlnp'], capture_output=True, text=True)
for line in r3.stdout.splitlines():
    if '8508' in line:
        print('PORT 8508 UP:', line)
        break
else:
    print('8508 not yet up, check /var/log/inv-allocation.log')
