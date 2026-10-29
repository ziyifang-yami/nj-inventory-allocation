#!/bin/bash
set -e
cd /opt/inv-allocation

echo "[$(date '+%Y-%m-%d %H:%M ET' --date='TZ=\"America/New_York\"')] Force refresh started"

# Remove lock and stale fc2sfc_tasks cache so Step 8b runs fresh
echo "Removing lock..."
rm -f results/refresh.lock

echo "Expiring fc2sfc_tasks cache in SQLite..."
python3 -c "
import sqlite3, datetime
from zoneinfo import ZoneInfo
c = sqlite3.connect('results/sfc_cache.db')
# Set updated_at to 2 hours ago so age_minutes > 60 → Step 8b will run
old_ts = (datetime.datetime.now(ZoneInfo('UTC')) - datetime.timedelta(hours=2)).isoformat()
c.execute(\"UPDATE meta SET updated_at=? WHERE table_name='fc2sfc_tasks'\", (old_ts,))
c.commit()
print(f'  fc2sfc_tasks updated_at set to {old_ts}')
c.close()
"

echo "Starting refresh (background, logging to results/refresh.log)..."
nohup .venv/bin/python refresh_sfc_cache.py >> results/refresh.log 2>&1 &
REFRESH_PID=$!
echo "  PID: $REFRESH_PID"
echo "  Tailing log for 15s..."
sleep 15
tail -20 results/refresh.log
echo ""
echo "Refresh running in background. Check results/refresh.log for completion."
