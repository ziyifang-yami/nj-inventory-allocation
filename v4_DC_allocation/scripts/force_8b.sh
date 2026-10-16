#!/bin/bash
cd /opt/inv-allocation
# Clear fc2sfc_tasks meta so Step 8b will re-run
sqlite3 results/sfc_cache.db "DELETE FROM meta WHERE table_name='fc2sfc_tasks';"
echo "Meta cleared. Starting refresh (Step 8b will re-run)..."
nohup .venv/bin/python refresh_sfc_cache.py >> results/refresh.log 2>&1 &
echo "Refresh started in background (PID $!). Tail: tail -f results/refresh.log"
