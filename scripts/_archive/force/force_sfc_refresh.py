import subprocess, time

SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
SCP = ['scp','-o','ConnectTimeout=10','-o','BatchMode=yes']

def ssh(cmd, timeout=20):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()

def scp(src, dst):
    r = subprocess.run(SCP+[src,f'root@184.33.17.231:{dst}'],capture_output=True,text=True,timeout=30)
    print(f"  {src.split(chr(92))[-1]}: rc={r.returncode} {r.stderr or 'OK'}")

# Upload latest sfc_to_fc.py
print("=== Upload sfc_to_fc.py ===")
scp('logic/sfc_to_fc.py', '/opt/inv-allocation/logic/sfc_to_fc.py')

# Verify the key change is there
print("\n=== Verify whole-location greedy on server ===")
print(ssh("grep -n 'whole location\\|greedy\\|cum >= needed\\|take everything' /opt/inv-allocation/logic/sfc_to_fc.py | head -8"))

# Clear task_list meta (TTL is 30min) so it rebuilds with new logic
print("\n=== Clear task_list meta ===")
out = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db \"DELETE FROM meta WHERE table_name='task_list';\" && echo cleared")
print(out)

# Trigger refresh
print("\n=== Trigger refresh ===")
subprocess.Popen(SSH + ['bash /tmp/force_8b.sh'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(5)
print(ssh("ps aux | grep refresh_sfc_cache | grep -v grep | awk '{print $2,$9}'"))

print("\nPolling (~2 min for task_list, ~7 min for fc2sfc_tasks)...")
for i in range(20):
    time.sleep(30)
    proc = ssh("ps aux | grep refresh_sfc_cache | grep -v grep | head -1")
    log  = ssh("tail -1 /opt/inv-allocation/results/refresh_log.txt")
    t1   = log.split('T1: ')[1].split(' ')[0] if 'T1: ' in log else '?'
    ts   = log[1:20] if log.startswith('[') else '...'
    print(f"  [{i+1}] {'running' if proc else 'DONE'}  {ts}  T1={t1}", flush=True)
    if not proc and 'task_lists:' in log and 'cache hit' not in log:
        print(f"\n=== DONE ===\n{log}")
        # Check T1: qty vs qty_to_transfer should be equal now
        print("\n=== T1 sample (qty == qty_to_transfer = whole location) ===")
        print(ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db 'SELECT item_number,location_no,qty,qty_to_transfer FROM task_list WHERE list_id=''T1'' LIMIT 8;'"))
        break

time.sleep(60)
print("done")
