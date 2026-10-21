"""
watchdog.py — Monitor refresh_sfc_cache.py health and restart if stale.

Run via cron every 10 minutes:
  */10 * * * * cd /opt/inv-allocation && .venv/bin/python watchdog.py >> results/watchdog.log 2>&1

Checks:
  1. fc2sfc_tasks age > TASKS_STALE_MIN  → stale, needs refresh
  2. refresh.lock exists with live PID   → already running, skip
  3. refresh.lock exists with dead PID   → stale lock, remove and restart
  4. No lock + stale tasks               → spawn refresh_sfc_cache.py

Also checks if the running refresh process is hung (> MAX_RUN_MIN).
"""
import os
import sys
import sqlite3
import subprocess
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Config ────────────────────────────────────────────────────────────────────
HERE            = Path(__file__).parent.resolve()
DB_PATH         = HERE / "results" / "sfc_cache.db"
LOCK_FILE       = HERE / "results" / "refresh.lock"
REFRESH_SCRIPT  = HERE / "refresh_sfc_cache.py"
VENV_PYTHON     = HERE / ".venv" / "bin" / "python"
LOG_FILE        = HERE / "results" / "watchdog.log"

TASKS_STALE_MIN = 75   # trigger restart if fc2sfc_tasks > 75 min old (1h TTL + 15min buffer)
MAX_RUN_MIN     = 20   # kill and restart if refresh has been running > 20 min (normal run ~4min)

NJ_TZ = ZoneInfo("America/New_York")
UTC   = ZoneInfo("UTC")

def now_et():
    return datetime.datetime.now(NJ_TZ).strftime("%Y-%m-%d %H:%M ET")

def age_minutes(table: str) -> float:
    """Return minutes since table was last written. 9999 if never."""
    try:
        c = sqlite3.connect(str(DB_PATH), timeout=5)
        row = c.execute(
            "SELECT updated_at FROM meta WHERE table_name=?", (table,)
        ).fetchone()
        c.close()
        if not row:
            return 9999.0
        ts = datetime.datetime.fromisoformat(row[0])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return (datetime.datetime.now(UTC) - ts.astimezone(UTC)).total_seconds() / 60
    except Exception as e:
        print(f"[{now_et()}] watchdog: DB read error: {e}")
        return 9999.0

def get_lock_pid() -> int | None:
    """Return PID from lock file, or None if missing/invalid."""
    try:
        return int(LOCK_FILE.read_text().strip())
    except Exception:
        return None

def pid_alive(pid: int) -> bool:
    """True if process with given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False

def pid_run_minutes(pid: int) -> float:
    """How long (minutes) has this PID been running. -1 on error."""
    try:
        import subprocess as sp
        r = sp.run(
            ["ps", "-p", str(pid), "-o", "etimes="],
            capture_output=True, text=True
        )
        seconds = float(r.stdout.strip())
        return seconds / 60
    except Exception:
        return -1.0

def spawn_refresh():
    """Start refresh_sfc_cache.py as a detached background process."""
    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    proc = subprocess.Popen(
        [python, str(REFRESH_SCRIPT)],
        cwd=str(HERE),
        stdout=open(HERE / "results" / "refresh.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,   # detach from cron's process group
    )
    return proc.pid

def main():
    tasks_age = age_minutes("fc2sfc_tasks")
    triggers_age = age_minutes("fc2sfc_triggers")

    print(f"[{now_et()}] watchdog: fc2sfc_tasks={tasks_age:.0f}min  fc2sfc_triggers={triggers_age:.0f}min")

    # Tasks are fresh — nothing to do
    if tasks_age <= TASKS_STALE_MIN:
        print(f"[{now_et()}] watchdog: tasks fresh ({tasks_age:.0f} min), OK")
        return

    # Tasks are stale — check lock
    lock_pid = get_lock_pid()

    if lock_pid is not None and pid_alive(lock_pid):
        run_min = pid_run_minutes(lock_pid)
        if run_min > 0 and run_min > MAX_RUN_MIN:
            # Refresh has been running too long — kill and restart
            print(f"[{now_et()}] watchdog: PID {lock_pid} hung ({run_min:.0f} min > {MAX_RUN_MIN}min), killing")
            try:
                os.kill(lock_pid, 9)
            except Exception as e:
                print(f"[{now_et()}] watchdog: kill failed: {e}")
            LOCK_FILE.unlink(missing_ok=True)
            new_pid = spawn_refresh()
            print(f"[{now_et()}] watchdog: spawned new refresh PID={new_pid}")
        else:
            print(f"[{now_et()}] watchdog: refresh running (PID={lock_pid}, {run_min:.0f} min), tasks stale but waiting")
        return

    # Lock file exists but PID is dead — stale lock
    if lock_pid is not None and not pid_alive(lock_pid):
        print(f"[{now_et()}] watchdog: stale lock (dead PID={lock_pid}), removing")
        LOCK_FILE.unlink(missing_ok=True)

    # No running refresh + stale tasks → spawn
    print(f"[{now_et()}] watchdog: tasks stale ({tasks_age:.0f} min), spawning refresh")
    new_pid = spawn_refresh()
    print(f"[{now_et()}] watchdog: spawned refresh PID={new_pid}")

if __name__ == "__main__":
    main()
