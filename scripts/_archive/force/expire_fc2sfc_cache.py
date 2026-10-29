"""Expire fc2sfc_tasks cache so next refresh rebuilds it with new loader."""
import sqlite3
conn = sqlite3.connect('/opt/inv-allocation/results/sfc_cache.db')
conn.execute("UPDATE meta SET updated_at='2000-01-01T00:00:00' WHERE table_name='fc2sfc_tasks'")
conn.commit()
row = conn.execute("SELECT updated_at FROM meta WHERE table_name='fc2sfc_tasks'").fetchone()
conn.close()
print(f"fc2sfc_tasks cache timestamp set to: {row[0]}")
