"""Fix nginx inv-allocation location to match upc-check-price pattern"""
nginx_conf = '/etc/nginx/sites-enabled/default'

with open(nginx_conf) as f:
    content = f.read()

# Replace inv-allocation block with full headers like upc-check-price
old = '''    location /inv-allocation {
        proxy_pass http://127.0.0.1:8508;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }'''

new = '''    location /inv-allocation {
        proxy_pass http://127.0.0.1:8508;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }'''

if old in content:
    content = content.replace(old, new)
    print("Replaced inv-allocation block")
else:
    print("Block not found exactly, current content around inv-allocation:")
    idx = content.find('/inv-allocation')
    print(repr(content[max(0,idx-10):idx+300]))

with open(nginx_conf, 'w') as f:
    f.write(content)

import subprocess
r = subprocess.run(['nginx', '-t'], capture_output=True, text=True)
print(r.stdout, r.stderr)
r2 = subprocess.run(['nginx', '-s', 'reload'], capture_output=True, text=True)
print("reload:", r2.returncode)
