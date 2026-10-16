import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print('=== running processes ===')
print(run('ps aux | grep refresh_sfc | grep -v grep'))
print('=== lock file ===')
print(run('ls -la /opt/inv-allocation/results/refresh.lock 2>/dev/null && cat /opt/inv-allocation/results/refresh.lock || echo NO_LOCK'))
print('=== last log line ===')
print(run('tail -1 /opt/inv-allocation/results/refresh_log.txt'))
print('=== meta timestamps ===')
print(run("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"SELECT table_name,updated_at FROM meta ORDER BY updated_at DESC LIMIT 8;\""))
time.sleep(300)
