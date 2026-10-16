import subprocess, time

HOST = "root@184.33.17.231"
SSH_OPTS = ["-o", "ConnectTimeout=20", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=5"]

def ssh(cmd, timeout=20):
    r = subprocess.run(["ssh"] + SSH_OPTS + [HOST, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def scp(local, remote, timeout=20):
    r = subprocess.run(["scp"] + SSH_OPTS + [local, f"{HOST}:{remote}"],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stderr.strip()

rc, err = scp(r"c:\Project-Z\NJ_Inventory_Allocation\v4_DC_allocation\app.py",
              "/opt/inv-allocation/app.py")
print(f"Upload app.py: {'✅' if rc==0 else '❌'}" + (f" {err}" if err else ""))

out, err, rc = ssh("systemctl restart inv-allocation-8508 2>/dev/null; sleep 3; ss -tlnp | grep 8508 | head -1")
print(f"Restart: {out or err}")

time.sleep(5)
out2, _, _ = ssh("curl -s -o /dev/null -w '%{http_code}' http://localhost:8508/")
print(f"HTTP 8508: {out2}")
