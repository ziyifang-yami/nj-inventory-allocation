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
    (r"c:\Project-Z\NJ_Inventory_Allocation\v4_DC_allocation\app_test_sfc.py",
     "/opt/inv-allocation/app_test_sfc.py"),
    (r"c:\Project-Z\NJ_Inventory_Allocation\v4_DC_allocation\app_test_fc2sfc.py",
     "/opt/inv-allocation/app_test_fc2sfc.py"),
]

for local, remote in files:
    rc, err = scp(local, remote)
    label = remote.split("/")[-1]
    print(f"  {'✅' if rc==0 else '❌'} {label} rc={rc}" + (f" {err}" if err else ""))

for port, svc in [("8512", "inv-allocation-8512"), ("8513", "inv-allocation-8513")]:
    out, err, rc = ssh(f"systemctl restart {svc} 2>/dev/null; sleep 2; ss -tlnp | grep {port} | head -1")
    print(f"  {port}: {out or err or 'restarting...'}")

time.sleep(5)
for port in ["8512", "8513"]:
    out, _, _ = ssh(f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{port}/")
    print(f"  HTTP {port}: {out}")
