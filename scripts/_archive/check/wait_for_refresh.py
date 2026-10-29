import subprocess, time

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print(f"Waiting for PID 860129 to finish...", flush=True)
for i in range(20):
    time.sleep(30)
    proc = run('ps -p 860129 --no-headers 2>/dev/null')
    log  = run('tail -1 /opt/inv-allocation/results/refresh_log.txt')
    print(f"[{i+1}] proc={'running' if proc else 'done'}  log={log[-120:] if log else '(none)'}", flush=True)
    if not proc and log:
        print("\n=== FINAL RESULT ===")
        print(log)
        break

time.sleep(60)
