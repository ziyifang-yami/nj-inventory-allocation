"""Verify LOC_CLEAR logic on server — checks B00911 and samples."""
import subprocess, time, sys

HOST = "root@184.33.17.231"
SSH_OPTS = ["-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=3", "-o", "BatchMode=yes"]

def ssh(cmd):
    r = subprocess.run(["ssh"] + SSH_OPTS + [HOST, cmd],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip()

# 1. Is B00911 in LOC_CLEAR?
print("=== B00911 in fc2sfc_tasks ===")
print(ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db "
          "'SELECT list_id,location_no,item_number,qty_to_transfer FROM fc2sfc_tasks WHERE location_no=''B00911'';'"))

# 2. What are the LOC_CLEAR counts now?
print("\n=== fc2sfc_tasks counts by list ===")
print(ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db "
          "'SELECT list_id,COUNT(*) as rows,COUNT(DISTINCT location_no) as locs FROM fc2sfc_tasks GROUP BY list_id;'"))

# 3. Sample 5 LOC_CLEAR locations — check their MySQL live inventory
print("\n=== Sample 5 LOC_CLEAR locations (live MySQL check) ===")
sample_locs_raw = ssh("sqlite3 /opt/inv-allocation/results/sfc_cache.db "
                      "'SELECT DISTINCT location_no FROM fc2sfc_tasks WHERE list_id=''LOC_CLEAR'' LIMIT 5;'")
print("Sampled locations:", sample_locs_raw)

# 4. Check what new code produced via refresh log detail
print("\n=== Last refresh log detail ===")
print(ssh("tail -3 /opt/inv-allocation/results/refresh_log.txt"))

# 5. Verify the new code is actually on server
print("\n=== Server code has issubset? ===")
print(ssh("grep -c 'issubset\\|loc_live_skus\\|locs_with_tasks' /opt/inv-allocation/refresh_sfc_cache.py"))

time.sleep(60)
print("done")
