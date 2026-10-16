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

files = [
    (r"c:\Project-Z\NJ_Inventory_Allocation\v4_DC_allocation\app.py",
     "/opt/inv-allocation/app.py"),
    (r"c:\Project-Z\NJ_Inventory_Allocation\v4_DC_allocation\data\loader.py",
     "/opt/inv-allocation/data/loader.py"),
    (r"c:\Project-Z\NJ_Inventory_Allocation\v4_DC_allocation\cache_db.py",
     "/opt/inv-allocation/cache_db.py"),
]

for local, remote in files:
    rc, err = scp(local, remote)
    label = remote.split("/")[-1]
    print(f"  {'✅' if rc==0 else '❌'} {label} rc={rc}" + (f" {err}" if err else ""))

print("Restarting 8508...")
out, err, rc = ssh("systemctl restart inv-allocation-8508 2>/dev/null || pkill -f 'port 8508' 2>/dev/null; sleep 2; ss -tlnp | grep 8508 | head -1")
print(f"  {out or err}")

# Quick verify
time.sleep(4)
out, _, _ = ssh("ss -tlnp | grep 8508")
print(f"  Port 8508: {'UP ✅' if '8508' in out else 'checking...'}")
out2, _, _ = ssh("curl -s -o /dev/null -w '%{http_code}' http://localhost:8508/")
print(f"  HTTP: {out2}")
