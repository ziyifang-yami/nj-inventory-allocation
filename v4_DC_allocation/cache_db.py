"""
cache_db.py — SQLite-based cache for SFC→FC task list data.

Single DB: results/sfc_cache.db

Tables:
  sales      — Athena sales/AOD data per SKU          (12h TTL)
  sfc_lots   — lot-level SFC location data             (30min TTL)
  fc_exp     — FC expiry summary per SKU               (1h TTL)
  exp_flags  — SKUs with need_minimal_exp=1            (12h TTL)
  task_list  — T1/T2/T3/T4 transfer task rows          (30min TTL)
  meta       — last refresh timestamps per table

Usage:
  db = CacheDB("results/sfc_cache.db")
  db.write_sales(df)
  df = db.read_sales()
  age = db.age_minutes("sales")
"""

from __future__ import annotations
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

UTC = ZoneInfo("UTC")

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta (
    table_name TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    item_number         TEXT PRIMARY KEY,
    inv_days_30         REAL,
    active_order_days   REAL,
    order_lines_30d     REAL,
    avg_daily_orders    REAL,
    sales_units_30d     REAL,
    unit_volume_cbm     REAL,
    avg_daily_sales     REAL,
    daily_sales_cbm     REAL
);

CREATE TABLE IF NOT EXISTS sfc_lots (
    item_number      TEXT NOT NULL,
    location_no      TEXT NOT NULL,
    loc_type         TEXT,
    qty              REAL,
    exp_date         TEXT,
    inv_age_days     INTEGER,
    case_qty         INTEGER,
    need_minimal_exp INTEGER,
    PRIMARY KEY (item_number, location_no)
);

CREATE TABLE IF NOT EXISTS fc_exp (
    item_number      TEXT PRIMARY KEY,
    fc_min_exp_date  TEXT,
    fc_max_exp_date  TEXT
);

CREATE TABLE IF NOT EXISTS exp_flags (
    item_number TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS fc_lots (
    item_number      TEXT NOT NULL,
    location_no      TEXT NOT NULL,
    loc_type         TEXT,
    qty              REAL,
    exp_date         TEXT,
    inv_age_days     INTEGER,
    case_qty         INTEGER,
    need_minimal_exp INTEGER,
    PRIMARY KEY (item_number, location_no)
);

CREATE TABLE IF NOT EXISTS pending_putaway (
    item_number         TEXT NOT NULL,
    location_no         TEXT,
    waiting_putaway_qty INTEGER,
    po_reference        TEXT,
    last_update         TEXT,
    PRIMARY KEY (item_number)
);

CREATE TABLE IF NOT EXISTS fc2sfc_tasks (
    list_id          TEXT NOT NULL,
    item_number      TEXT,
    location_no      TEXT,
    loc_type         TEXT,
    qty_to_transfer  REAL,
    qty              REAL,
    exp_date         TEXT,
    rule             TEXT,
    trigger_type     TEXT,
    excess_qty       REAL,
    keep_qty         REAL,
    fc_d2s           REAL,
    avg_daily_orders REAL,
    excess_vol       REAL
);
CREATE INDEX IF NOT EXISTS idx_fc2sfc_list_id ON fc2sfc_tasks(list_id);

CREATE TABLE IF NOT EXISTS task_list (
    list_id          TEXT NOT NULL,
    item_number      TEXT,
    segment          TEXT,
    source_type      TEXT,
    location_no      TEXT,
    loc_type         TEXT,
    qty_to_transfer  REAL,
    qty              REAL,
    exp_date         TEXT,
    case_qty         INTEGER,
    fc_d2s           REAL,
    avg_daily_orders REAL,
    order_lines_30d  REAL,
    rule             TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_list_id ON task_list(list_id);
"""

# Sales columns stored in DB (subset that matters for routing + classification)
_SALES_COLS = [
    "item_number", "inv_days_30", "active_order_days", "order_lines_30d",
    "avg_daily_orders", "sales_units_30d", "unit_volume_cbm",
    "avg_daily_sales", "daily_sales_cbm",
]


class CacheDB:
    def __init__(self, path: str | Path = "results/sfc_cache.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self):
        return sqlite3.connect(self.path, timeout=10)

    def _init(self):
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ── Age ───────────────────────────────────────────────────────────────────

    def age_minutes(self, table: str) -> float:
        """Minutes since last write to table. Returns 9999 if never written."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT updated_at FROM meta WHERE table_name=?", (table,)
            ).fetchone()
        if not row:
            return 9999.0
        try:
            ts = datetime.fromisoformat(row[0])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            return (datetime.now(UTC) - ts.astimezone(UTC)).total_seconds() / 60
        except Exception:
            return 9999.0

    def _atomic_replace(self, conn, table: str, df: pd.DataFrame):
        """
        Atomically replace table content: write to staging, then swap.
        Readers never see empty data — they see old data until new is ready.
        """
        staging = f"{table}_staging"
        # Write to staging table
        df.to_sql(staging, conn, if_exists="replace", index=False)
        # Atomic swap inside the same connection/transaction
        conn.execute(f"DELETE FROM {table}")
        conn.execute(f"INSERT INTO {table} SELECT * FROM {staging}")
        conn.execute(f"DROP TABLE IF EXISTS {staging}")
        conn.execute(
            "INSERT OR REPLACE INTO meta(table_name, updated_at) VALUES(?,?)",
            (table, datetime.now(UTC).isoformat()),
        )

    # ── Sales ─────────────────────────────────────────────────────────────────

    def write_sales(self, df: pd.DataFrame):
        """Write Athena sales data to SQLite."""
        if df is None or df.empty:
            return
        df = df.copy()
        df["item_number"] = df["item_number"].astype(str)
        for col in _SALES_COLS:
            if col not in df.columns:
                df[col] = None
            elif col != "item_number":
                df[col] = pd.to_numeric(df[col], errors="coerce")
        with self._conn() as conn:
            self._atomic_replace(conn, "sales", df[_SALES_COLS])
            self._touch(conn, "sales")

    def read_sales(self) -> pd.DataFrame:
        """Read all sales data from SQLite."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT item_number,inv_days_30,active_order_days,order_lines_30d,"
                "avg_daily_orders,sales_units_30d,unit_volume_cbm,"
                "avg_daily_sales,daily_sales_cbm FROM sales"
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(rows, columns=cols)
        for c in cols:
            if c != "item_number":
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
        return df

    # ── Pending putaway ───────────────────────────────────────────────────────

    def write_pending_putaway(self, df: pd.DataFrame):
        with self._conn() as conn:
            conn.execute("DELETE FROM pending_putaway")
            if df is not None and not df.empty:
                df = df.copy()
                df["item_number"] = df["item_number"].astype(str)
                for col in ["item_number","location_no","waiting_putaway_qty","po_reference","last_update"]:
                    if col not in df.columns:
                        df[col] = None
                if "last_update" in df.columns:
                    df["last_update"] = df["last_update"].astype(str).replace("NaT","")
                rows = df[["item_number","location_no","waiting_putaway_qty","po_reference","last_update"]].values.tolist()
                conn.executemany("INSERT OR REPLACE INTO pending_putaway VALUES(?,?,?,?,?)", rows)
            # Always write meta so age is real (0 rows = genuinely empty, not "never run")
            conn.execute(
                "INSERT OR REPLACE INTO meta(table_name, updated_at) VALUES(?,?)",
                ("pending_putaway", datetime.now(UTC).isoformat()),
            )

    def read_pending_putaway(self) -> pd.DataFrame:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT item_number,location_no,waiting_putaway_qty,po_reference,last_update "
                "FROM pending_putaway"
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(rows, columns=cols)
        df["waiting_putaway_qty"] = pd.to_numeric(df["waiting_putaway_qty"], errors="coerce").fillna(0).astype(int)
        df["last_update"] = pd.to_datetime(df["last_update"].replace("",None), errors="coerce")
        return df

    # ── FC lots ───────────────────────────────────────────────────────────────

    def write_fc_lots(self, df: pd.DataFrame):
        if df is None or df.empty:
            return
        df = df.copy()
        if "exp_date" in df.columns:
            df["exp_date"] = df["exp_date"].astype(str).replace("NaT", "")
        rows = df[["item_number","location_no","loc_type","qty",
                   "exp_date","inv_age_days","case_qty","need_minimal_exp"]].values.tolist()
        with self._conn() as conn:
            conn.execute("DELETE FROM fc_lots")
            conn.executemany(
                "INSERT OR REPLACE INTO fc_lots VALUES(?,?,?,?,?,?,?,?)", rows
            )
            self._touch(conn, "fc_lots")

    def read_fc_lots(self, skus: list[str] | None = None) -> pd.DataFrame:
        with self._conn() as conn:
            if skus:
                ph = ",".join("?" * len(skus))
                df = pd.read_sql(
                    f"SELECT * FROM fc_lots WHERE item_number IN ({ph})",
                    conn, params=skus
                )
            else:
                df = pd.read_sql("SELECT * FROM fc_lots", conn)
        if df.empty:
            return df
        df["qty"]              = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        df["exp_date"]         = pd.to_datetime(df["exp_date"].replace("", None), errors="coerce")
        df["inv_age_days"]     = pd.to_numeric(df["inv_age_days"], errors="coerce").fillna(0).astype(int)
        df["need_minimal_exp"] = pd.to_numeric(df["need_minimal_exp"], errors="coerce").fillna(0).astype(int)
        df["case_qty"]         = pd.to_numeric(df["case_qty"], errors="coerce").fillna(1).clip(lower=1).astype(int)
        return df

    # ── FC→SFC tasks ──────────────────────────────────────────────────────────

    _FC2SFC_COLS = [
        "list_id","item_number","location_no","loc_type","qty_to_transfer","qty",
        "exp_date","rule","trigger_type","excess_qty","keep_qty",
        "fc_d2s","avg_daily_orders","excess_vol",
    ]

    def write_fc2sfc_tasks(self, task_lists: dict):
        """Write MIDDLE_EVICT / TAIL_CLEAR DataFrames to fc2sfc_tasks table."""
        rows = []
        list_ids = []
        for list_id, df in task_lists.items():
            list_ids.append(list_id)
            if df is None or df.empty:
                continue
            df = df.copy()
            df["list_id"] = list_id
            if "exp_date" in df.columns:
                df["exp_date"] = df["exp_date"].astype(str).replace("NaT", "")
            for col in self._FC2SFC_COLS:
                if col not in df.columns:
                    df[col] = None
            rows.append(df[self._FC2SFC_COLS])
        with self._conn() as conn:
            ph = ",".join("?" * len(list_ids))
            if rows:
                new_data = pd.concat(rows, ignore_index=True)
                new_data.to_sql("fc2sfc_staging", conn, if_exists="replace", index=False)
                conn.execute(f"DELETE FROM fc2sfc_tasks WHERE list_id IN ({ph})", list_ids)
                conn.execute("INSERT INTO fc2sfc_tasks SELECT * FROM fc2sfc_staging")
                conn.execute("DROP TABLE IF EXISTS fc2sfc_staging")
            else:
                conn.execute(f"DELETE FROM fc2sfc_tasks WHERE list_id IN ({ph})", list_ids)
            self._touch(conn, "fc2sfc_tasks")

    def read_fc2sfc_task(self, list_id: str) -> pd.DataFrame:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT item_number,location_no,loc_type,qty_to_transfer,qty,"
                "exp_date,rule,trigger_type,excess_qty,keep_qty,"
                "fc_d2s,avg_daily_orders,excess_vol "
                "FROM fc2sfc_tasks WHERE list_id=?", (list_id,)
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(rows, columns=cols)
        df["qty_to_transfer"]  = pd.to_numeric(df["qty_to_transfer"], errors="coerce").fillna(0)
        df["qty"]              = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        df["exp_date"]         = pd.to_datetime(df["exp_date"].replace("", None), errors="coerce")
        df["fc_d2s"]           = pd.to_numeric(df["fc_d2s"], errors="coerce")
        df["avg_daily_orders"] = pd.to_numeric(df["avg_daily_orders"], errors="coerce").fillna(0)
        df["excess_qty"]       = pd.to_numeric(df["excess_qty"], errors="coerce").fillna(0)
        df["keep_qty"]         = pd.to_numeric(df["keep_qty"], errors="coerce").fillna(0)
        df["excess_vol"]       = pd.to_numeric(df["excess_vol"], errors="coerce").fillna(0)
        return df

    # ── SFC lots ──────────────────────────────────────────────────────────────

    def write_sfc_lots(self, df: pd.DataFrame):
        if df is None or df.empty:
            return
        df = df.copy()
        if "exp_date" in df.columns:
            df["exp_date"] = df["exp_date"].astype(str).replace("NaT", "")
        rows = df[["item_number","location_no","loc_type","qty",
                   "exp_date","inv_age_days","case_qty","need_minimal_exp"]].values.tolist()
        with self._conn() as conn:
            conn.execute("DELETE FROM sfc_lots")
            conn.executemany(
                "INSERT OR REPLACE INTO sfc_lots VALUES(?,?,?,?,?,?,?,?)", rows
            )
            self._touch(conn, "sfc_lots")

    def read_sfc_lots(self, skus: list[str] | None = None) -> pd.DataFrame:
        with self._conn() as conn:
            if skus:
                ph = ",".join("?" * len(skus))
                df = pd.read_sql(
                    f"SELECT * FROM sfc_lots WHERE item_number IN ({ph})",
                    conn, params=skus
                )
            else:
                df = pd.read_sql("SELECT * FROM sfc_lots", conn)
        if df.empty:
            return df
        df["qty"]              = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        df["exp_date"]         = pd.to_datetime(df["exp_date"].replace("", None), errors="coerce")
        df["inv_age_days"]     = pd.to_numeric(df["inv_age_days"], errors="coerce").fillna(0).astype(int)
        df["need_minimal_exp"] = pd.to_numeric(df["need_minimal_exp"], errors="coerce").fillna(0).astype(int)
        df["case_qty"]         = pd.to_numeric(df["case_qty"], errors="coerce").fillna(1).clip(lower=1).astype(int)
        return df

    # ── FC exp ────────────────────────────────────────────────────────────────

    def write_fc_exp(self, df: pd.DataFrame):
        if df is None or df.empty:
            return
        df = df.copy()
        for c in ["fc_min_exp_date","fc_max_exp_date"]:
            if c in df.columns:
                df[c] = df[c].astype(str).replace("NaT","")
        rows = df[["item_number","fc_min_exp_date","fc_max_exp_date"]].values.tolist()
        with self._conn() as conn:
            conn.execute("DELETE FROM fc_exp")
            conn.executemany("INSERT OR REPLACE INTO fc_exp VALUES(?,?,?)", rows)
            self._touch(conn, "fc_exp")

    def read_fc_exp(self, skus: list[str] | None = None) -> pd.DataFrame:
        with self._conn() as conn:
            if skus:
                ph = ",".join("?" * len(skus))
                df = pd.read_sql(
                    f"SELECT * FROM fc_exp WHERE item_number IN ({ph})",
                    conn, params=skus
                )
            else:
                df = pd.read_sql("SELECT * FROM fc_exp", conn)
        if df.empty:
            return pd.DataFrame(columns=["item_number","fc_min_exp_date","fc_max_exp_date"])
        df["fc_min_exp_date"] = pd.to_datetime(df["fc_min_exp_date"].replace("",None), errors="coerce")
        df["fc_max_exp_date"] = pd.to_datetime(df["fc_max_exp_date"].replace("",None), errors="coerce")
        return df

    # ── EXP flags ─────────────────────────────────────────────────────────────

    def write_exp_flags(self, items: list[str]):
        with self._conn() as conn:
            conn.execute("DELETE FROM exp_flags")
            conn.executemany("INSERT OR REPLACE INTO exp_flags VALUES(?)",
                             [(i,) for i in items])
            self._touch(conn, "exp_flags")

    def read_exp_flags(self) -> set[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT item_number FROM exp_flags").fetchall()
        return {r[0] for r in rows}

    # ── Task lists ────────────────────────────────────────────────────────────

    _TASK_COLS = [
        "list_id","item_number","segment","source_type","location_no","loc_type",
        "qty_to_transfer","qty","exp_date","case_qty","fc_d2s","avg_daily_orders",
        "order_lines_30d","rule",
    ]

    def write_task_lists(self, task_lists: dict):
        rows = []
        list_ids = []
        for list_id, df in task_lists.items():
            list_ids.append(list_id)
            if df is None or df.empty:
                continue
            df = df.copy()
            df["list_id"] = list_id
            if "exp_date" in df.columns:
                df["exp_date"] = df["exp_date"].astype(str).replace("NaT", "")
            for col in self._TASK_COLS:
                if col not in df.columns:
                    df[col] = None
            rows.append(df[self._TASK_COLS])
        with self._conn() as conn:
            if rows:
                new_data = pd.concat(rows, ignore_index=True)
                # Atomic: write to staging first, then swap (no empty window)
                new_data.to_sql("task_list_staging", conn, if_exists="replace", index=False)
                ph = ",".join("?" * len(list_ids))
                conn.execute(f"DELETE FROM task_list WHERE list_id IN ({ph})", list_ids)
                conn.execute("INSERT INTO task_list SELECT * FROM task_list_staging")
                conn.execute("DROP TABLE IF EXISTS task_list_staging")
            self._touch(conn, "task_list")

    def read_task_list(self, list_id: str) -> pd.DataFrame:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT item_number,segment,source_type,location_no,loc_type,"
                "qty_to_transfer,qty,exp_date,case_qty,fc_d2s,avg_daily_orders,"
                "order_lines_30d,rule FROM task_list WHERE list_id=?", (list_id,)
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(rows, columns=cols)
        df["qty_to_transfer"]  = pd.to_numeric(df["qty_to_transfer"], errors="coerce").fillna(0)
        df["qty"]              = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        df["exp_date"]         = pd.to_datetime(df["exp_date"].replace("", None), errors="coerce")
        df["fc_d2s"]           = pd.to_numeric(df["fc_d2s"], errors="coerce")
        df["avg_daily_orders"] = pd.to_numeric(df["avg_daily_orders"], errors="coerce").fillna(0)
        df["case_qty"]         = pd.to_numeric(df["case_qty"], errors="coerce").fillna(1).astype(int)
        return df
