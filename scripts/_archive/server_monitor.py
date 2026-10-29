"""Monitor server refresh status via SSH — runs as background watcher."""
import subprocess, time, sys

HOST = "root@184.33.17.231"
SSH_OPTS = ["-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=3", "-o", "BatchMode=yes"]

def run(cmd):
    r = subprocess.run(
        ["ssh"] + SSH_OPTS + [HOST, cmd],
        capture_output=True, text=True, timeout=20
    )
    return r.stdout.strip(), r.stderr.strip()

print(f"[monitor] Starting at {time.strftime('%H:%M:%S')}", flush=True)

for i in range(30):           # poll for up to 15 minutes
    try:
        out, err = run(
            "tail -1 /opt/inv-allocation/results/refresh_log.txt; "
            "echo ---; ps aux | grep refresh_sfc | grep -v grep | head -2"
        )
        print(f"\n[{time.strftime('%H:%M:%S')}] poll {i+1}:")
        print(out or "(no output)")
        if err:
            print("STDERR:", err)
        sys.stdout.flush()
        # Stop polling once we see the latest run timestamp
        if "17:" in out and "LOC_CLEAR" in out:
            print("[monitor] Done — new result found.")
            break
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] error: {e}", flush=True)
    time.sleep(30)

print("[monitor] Exiting.", flush=True)
# Keep alive so background process doesn't close immediately
time.sleep(60)
