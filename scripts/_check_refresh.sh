#!/bin/bash
echo "=== CRONTAB ==="
crontab -l 2>/dev/null || echo "(no crontab)"

echo ""
echo "=== LOCK FILE ==="
ls -la /opt/inv-allocation/results/refresh.lock 2>/dev/null || echo "no lock file"

echo ""
echo "=== REFRESH LOG (last 40 lines) ==="
tail -40 /opt/inv-allocation/results/refresh.log 2>/dev/null || echo "no refresh.log"

echo ""
echo "=== META TABLE ==="
cd /opt/inv-allocation
python3 - <<'EOF'
import sqlite3, datetime
from zoneinfo import ZoneInfo
try:
    c = sqlite3.connect("results/sfc_cache.db")
    rows = c.execute("SELECT table_name, updated_at FROM meta ORDER BY updated_at DESC").fetchall()
    ET = ZoneInfo("America/New_York")
    now = datetime.datetime.now(ZoneInfo("UTC"))
    for name, ts in rows:
        try:
            t = datetime.datetime.fromisoformat(ts)
            if t.tzinfo is None:
                t = t.replace(tzinfo=ZoneInfo("UTC"))
            age = (now - t.astimezone(ZoneInfo("UTC"))).total_seconds() / 60
            t_et = t.astimezone(ET).strftime("%H:%M ET")
            print(f"  {name:<25} {t_et}  ({age:.0f} min ago)")
        except:
            print(f"  {name:<25} {ts}")
    c.close()
except Exception as e:
    print(f"DB error: {e}")
EOF

echo ""
echo "=== SYSTEMD CRON STATUS ==="
systemctl is-active cron 2>/dev/null || systemctl is-active crond 2>/dev/null || echo "unknown"

echo ""
echo "=== RECENT CRON SYSLOG ==="
grep -i "inv-allocation\|refresh_sfc\|CRON" /var/log/syslog 2>/dev/null | tail -20 \
  || journalctl -u cron --since "2 hours ago" --no-pager 2>/dev/null | tail -20 \
  || echo "no cron log found"

echo "=== DONE ==="
