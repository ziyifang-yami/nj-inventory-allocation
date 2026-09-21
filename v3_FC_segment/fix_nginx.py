import re

nginx_conf = '/etc/nginx/sites-enabled/default'

with open(nginx_conf) as f:
    content = f.read()

# 1. 清理掉之前插坏的 inv-allocation block（不完整的）
content = re.sub(r'\s*location /inv-allocation \{[^}]*\}', '', content)

# 2. 在 upc-check-price block 结束后插入正确的 inv-allocation block
inv_block = """
    location /inv-allocation {
        proxy_pass http://127.0.0.1:8508/inv-allocation;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }"""

# 找 upc-check-price block 的结束 }，在其后插入
content = re.sub(
    r'(location /upc-check-price \{.*?\})',
    r'\1' + inv_block,
    content,
    flags=re.DOTALL
)

with open(nginx_conf, 'w') as f:
    f.write(content)

print("nginx config updated")
