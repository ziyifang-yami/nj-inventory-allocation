#!/bin/bash
cd /opt/inv-allocation
.venv/bin/python - <<'EOF'
import sqlite3
c = sqlite3.connect('results/sfc_cache.db')
c.execute("DELETE FROM meta WHERE table_name='fc2sfc_tasks'")
c.commit()
c.close()
print("fc2sfc_tasks meta cleared — Step 8b will run on next refresh")
EOF
