"""
refresh_sfc_cache.py — Pre-warm all SFC→FC caches into a single SQLite DB.

Run manually:   python refresh_sfc_cache.py
Run via cron:   */30 * * * * cd /opt/inv-allocation && .venv/bin/python refresh_sfc_cache.py >> results/refresh.log 2>&1

Writes ONLY to:
  results/sfc_cache.db    (tables: sales, sfc_lots, fc_exp, exp_flags, task_list, meta)
  results/refresh_log.txt (human-readable last-run summary)

TTLs (checked against meta.updated_at in SQLite):
  sales      — 12 hours
  exp_flags  — 12 hours
  fc_exp     — 1 hour
  sfc_lots   — every run (always fresh, must match current candidate SKUs)
  task_list  — every run (rebuilt from above)
"""
import sys, os, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in [_HERE, _PARENT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd
import numpy as np
import pymysql
from dotenv import load_dotenv

for parts in [(".",), ("..",), ("..", "..")]:
    env = Path(*parts) / ".env"
    if env.exists():
        load_dotenv(dotenv_path=env)
        break

NJ_TZ = ZoneInfo("America/New_York")
UTC   = ZoneInfo("UTC")

def _now_nj():
    return datetime.now(NJ_TZ)

def _connect():
    _here = os.path.dirname(os.path.abspath(__file__))
    for candidate in [os.path.join(_here, ".env"), os.path.join(_here, "..", ".env")]:
        if os.path.exists(candidate):
            load_dotenv(dotenv_path=candidate)
            break
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"), port=int(os.getenv("MYSQL_PORT", "3306")),
        charset="utf8mb4", connect_timeout=15,
    )

log = []
t_start = time.perf_counter()

# ── Lock: prevent concurrent refresh runs (avoids multiple slow queries) ───────
import fcntl as _fcntl
_LOCK_FILE = Path("results/refresh.lock")
_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

# Stale lock check: if lock file has a PID and that process is dead, remove it
if _LOCK_FILE.exists():
    try:
        _old_pid = int(_LOCK_FILE.read_text().strip())
        # Check if PID is still alive
        os.kill(_old_pid, 0)
        # Process exists — let flock decide below
    except (ValueError, ProcessLookupError, PermissionError):
        # PID dead or unreadable — stale lock, remove it
        _LOCK_FILE.unlink(missing_ok=True)
        print(f"[{_now_nj().strftime('%Y-%m-%d %H:%M ET')}] Removed stale lock (dead PID).")

_lock_fh = open(_LOCK_FILE, "w")
try:
    _fcntl.flock(_lock_fh, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    _lock_fh.write(str(os.getpid()))
    _lock_fh.flush()
except BlockingIOError:
    print(f"[{_now_nj().strftime('%Y-%m-%d %H:%M ET')}] Another refresh already running — skipping.")
    _lock_fh.close()
    exit(0)

print(f"\n[{_now_nj().strftime('%Y-%m-%d %H:%M ET')}] Starting SFC cache refresh...")

from cache_db import CacheDB
DB_PATH = Path("results/sfc_cache.db")
db = CacheDB(DB_PATH)

# ── Step 1: Inventory (always fresh) ──────────────────────────────────────────
t0 = time.perf_counter()
print("  [1] Loading inventory...")
conn = _connect()
with conn.cursor() as cur:
    cur.execute("""
        SELECT wsli.item_number,
               SUM(CASE WHEN wsl.zone_id != 2
                        THEN wit.quantity ELSE 0 END) AS fc_qty,
               SUM(CASE WHEN wsl.zone_id = 2
                        THEN wit.quantity ELSE 0 END) AS sfc_qty
        FROM yamibuy_wh.wh_storage_location_item wsli
        JOIN yamibuy_wh.wh_inventory_transaction wit
            ON wit.item_number=wsli.item_number AND wit.warehouse_number=wsli.warehouse_number
               AND wit.location_no=wsli.location_no
        JOIN yamibuy_wh.wh_storage_location wsl
            ON wsl.location_no=wsli.location_no AND wsl.warehouse_number=wsli.warehouse_number
        JOIN yamibuy_wh.wh_location_size wls ON wls.size_id=wsl.size_id
        LEFT JOIN yamibuy_im.im_item ii ON ii.item_number=wsli.item_number
        WHERE wsli.warehouse_number='002' AND wit.quantity IS NOT NULL AND wit.quantity!=0
          AND wsl.location_type IN (3,4) AND wsl.size_id NOT IN (13,33)
          AND IFNULL(ii.business_type,0)!=8
        GROUP BY wsli.item_number HAVING fc_qty>0 OR sfc_qty>0
    """)
    cols = [d[0] for d in cur.description]
    df_inv = pd.DataFrame(cur.fetchall(), columns=cols)
conn.close()
df_inv["item_number"] = df_inv["item_number"].astype(str)
df_inv["fc_qty"]  = pd.to_numeric(df_inv["fc_qty"],  errors="coerce").fillna(0)
df_inv["sfc_qty"] = pd.to_numeric(df_inv["sfc_qty"], errors="coerce").fillna(0)
elapsed = time.perf_counter() - t0
print(f"      {len(df_inv):,} SKUs  ({elapsed:.1f}s)")
log.append(f"inventory: {len(df_inv):,} SKUs in {elapsed:.1f}s")
in_stock = df_inv["item_number"].tolist()

# ── Step 2: Sales data (12-hour TTL) ──────────────────────────────────────────
if db.age_minutes("sales") < 12 * 60:
    print("  [2] Sales — cache fresh, reading from SQLite.")
    df_sales = db.read_sales()
    log.append("sales: cache hit (skipped)")
else:
    t0 = time.perf_counter()
    print("  [2] Loading sales data from Athena...")
    try:
        from data.loader import load as _athena_load
        df_sales, _ = _athena_load()
        df_sales["item_number"] = df_sales["item_number"].astype(str)
        db.write_sales(df_sales)
        elapsed = time.perf_counter() - t0
        print(f"      {len(df_sales):,} SKUs  ({elapsed:.1f}s)")
        log.append(f"sales: {len(df_sales):,} SKUs in {elapsed:.1f}s")
    except Exception as e:
        print(f"      Athena failed: {e} — using existing SQLite sales if available")
        df_sales = db.read_sales()
        log.append(f"sales: Athena FAILED ({e}), using cached")

# ── Step 3: EXP flags (12-hour TTL) ───────────────────────────────────────────
if db.age_minutes("exp_flags") < 12 * 60:
    print("  [3] EXP flags — cache fresh, skipping.")
    exp_items = db.read_exp_flags()
    log.append("exp_flags: cache hit (skipped)")
else:
    t0 = time.perf_counter()
    print(f"  [3] Loading EXP flags for {len(in_stock):,} SKUs...")
    conn = _connect()
    all_items = []
    chunks = [in_stock[i:i+500] for i in range(0, len(in_stock), 500)]
    with conn.cursor() as cur:
        for chunk in chunks:
            ph = ",".join(["%s"] * len(chunk))
            cur.execute(
                f"SELECT item_number FROM yamibuy_im.im_item_extend "
                f"WHERE need_minimal_exp=1 AND item_number IN ({ph})", chunk
            )
            all_items.extend(str(r[0]) for r in cur.fetchall())
    conn.close()
    db.write_exp_flags(all_items)
    exp_items = set(all_items)
    elapsed = time.perf_counter() - t0
    print(f"      {len(exp_items):,} EXP SKUs  ({elapsed:.1f}s)")
    log.append(f"exp_flags: {len(exp_items):,} SKUs in {elapsed:.1f}s")

# ── Step 4: Classify candidates ───────────────────────────────────────────────
df = df_inv.copy()
if not df_sales.empty:
    df = df.merge(df_sales.drop(columns=["fc_qty","sfc_qty"], errors="ignore"),
                  on="item_number", how="left")
    print(f"  [4] Sales merged: {len(df_sales):,} SKUs")
else:
    print("  [4] WARNING: No sales data — using all SFC SKUs as candidates")

for c in ["active_order_days","order_lines_30d","avg_daily_orders","sales_units_30d","unit_volume_cbm"]:
    df[c] = pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0)

df["fc_d2s"] = np.where(df["avg_daily_orders"] > 0,
                        df["fc_qty"] / df["avg_daily_orders"], np.nan)
AOD_TOP, OL_TOP, OL_TAIL = 21, 100, 4
_aod = df["active_order_days"].to_numpy(float)
_ol  = df["order_lines_30d"].to_numpy(float)
df["seg"] = np.where((_aod >= AOD_TOP) & (_ol >= OL_TOP), "TOP",
                     np.where(_ol < OL_TAIL, "TAIL", "MIDDLE"))
df["need_minimal_exp"] = df["item_number"].isin(exp_items).astype(int)

# ── Step 4b: TBD segment — in-stock days < 30 (insufficient sales history) ────
# If a SKU has been in stock fewer than 30 days total (inv_days_30 < 30),
# we don't have enough data to classify it reliably → mark as TBD.
# inv_days_30 is already in df from the Athena sales merge.
_tbd_mask = df["inv_days_30"] < 30
df.loc[_tbd_mask, "seg"] = "TBD"
_n_tbd = _tbd_mask.sum()
# Save to SQLite so apps can read it
_final_tbd = df[df["seg"] == "TBD"]["item_number"].tolist()
db.write_tbd_skus(_final_tbd)
print(f"  [4b] TBD (inv_days_30 < 30): {_n_tbd:,} SKUs")
log.append(f"tbd_skus: {_n_tbd:,}")

_d2s = df["fc_d2s"].fillna(999.0)
_has_sales = (df["avg_daily_orders"] > 0).any()
if _has_sales:
    _mask = (
        (df["sfc_qty"] > 0) & (
            (df["seg"] == "TOP") |
            ((df["seg"] == "MIDDLE") & ((_d2s < 14) | (df["need_minimal_exp"] == 1)))
        )
    )
else:
    _mask = df["sfc_qty"] > 0
    print("  [4] WARNING: No sales — using all SFC SKUs")

sfc_skus = df[_mask]["item_number"].tolist()
print(f"  [4] Transfer candidates: {len(sfc_skus):,} SKUs")
log.append(f"candidates: {len(sfc_skus):,}")

# ── Step 5: SFC lot data (always fresh) ───────────────────────────────────────
t0 = time.perf_counter()
print(f"  [5] Loading SFC lot data for {len(sfc_skus):,} SKUs...")
df_lots = pd.DataFrame()
conn = _connect()
SQL_SFC = """
    SELECT li.item_number, li.location_no,
           CASE WHEN wsl.location_type=4 THEN 'Bin' ELSE 'Stock' END AS loc_type,
           SUM(li.quantity) AS qty,
           MAX(CASE WHEN ib.qc_ExpiredDate>0 THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END) AS exp_date,
           MAX(CAST(DATEDIFF(CURDATE(),DATE(FROM_UNIXTIME(
               CASE WHEN lm.lot_id=1 OR lm.lot_id IS NULL THEN NULLIF(ib.in_dtm,0)
                    ELSE NULLIF(lm.receive_time,0) END
           ))) AS SIGNED)) AS inv_age_days,
           CASE WHEN wis.spec IS NULL OR wis.spec='' THEN 1 ELSE CAST(wis.spec AS UNSIGNED) END AS case_qty,
           MAX(IFNULL(iie.need_minimal_exp,0)) AS need_minimal_exp
    FROM yamibuy_wh.wh_lot_inventory li
    JOIN yamibuy_wh.wh_storage_location wsl ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
    LEFT JOIN yamibuy_wh.wh_lot_master lm ON lm.lot_id=li.lot_id
    LEFT JOIN yamibuy_wh.wh_inbound_batch ib ON ib.lot_id=li.lot_id
    LEFT JOIN yamibuy_wh.wh_item_setting wis ON wis.item_number=li.item_number AND wis.warehouse_number='002'
    LEFT JOIN yamibuy_im.im_item_extend iie ON iie.item_number=li.item_number
    WHERE li.warehouse_number='002' AND li.quantity>0
      AND wsl.location_type IN (3,4)
      AND wsl.zone_id = 2
      AND li.item_number IN ({ph})
    GROUP BY li.item_number, li.location_no, wsl.location_type, wis.spec
    HAVING SUM(li.quantity)>0
"""
try:
    all_rows, all_cols = [], None
    for chunk in [sfc_skus[i:i+500] for i in range(0, len(sfc_skus), 500)]:
        ph = ",".join(["%s"]*len(chunk))
        with conn.cursor() as cur:
            cur.execute(SQL_SFC.format(ph=ph), chunk)
            if all_cols is None:
                all_cols = [d[0] for d in cur.description]
            all_rows.extend(cur.fetchall())
    conn.close()
    if all_rows:
        df_lots = pd.DataFrame(all_rows, columns=all_cols)
        df_lots["item_number"]      = df_lots["item_number"].astype(str)
        df_lots["qty"]              = pd.to_numeric(df_lots["qty"],          errors="coerce").fillna(0)
        df_lots["exp_date"]         = pd.to_datetime(df_lots["exp_date"],    errors="coerce")
        df_lots["inv_age_days"]     = pd.to_numeric(df_lots["inv_age_days"], errors="coerce").fillna(0).astype(int)
        df_lots["need_minimal_exp"] = pd.to_numeric(df_lots["need_minimal_exp"], errors="coerce").fillna(0).astype(int)
        df_lots["case_qty"]         = pd.to_numeric(df_lots["case_qty"],     errors="coerce").fillna(1).clip(lower=1).astype(int)
        db.write_sfc_lots(df_lots)
        elapsed = time.perf_counter() - t0
        print(f"      {len(df_lots):,} lot rows  ({elapsed:.1f}s)")
        log.append(f"sfc_lots: {len(df_lots):,} rows in {elapsed:.1f}s")
    else:
        print("      0 lot rows")
        log.append("sfc_lots: 0 rows")
except Exception as e:
    print(f"      SFC lot query FAILED: {e}")
    log.append(f"sfc_lots: FAILED ({e})")
    try: conn.close()
    except: pass

# ── Step 6: FC exp (1-hour TTL) ───────────────────────────────────────────────
exp_sfc_skus = [s for s in sfc_skus if s in exp_items]
df_fc_exp = pd.DataFrame(columns=["item_number","fc_min_exp_date","fc_max_exp_date"])

if db.age_minutes("fc_exp") < 30:
    print("  [6] FC exp — cache fresh, reading from SQLite.")
    df_fc_exp = db.read_fc_exp()
    log.append("fc_exp: cache hit (skipped)")
else:
    t0 = time.perf_counter()
    print(f"  [6] Loading FC exp for {len(exp_sfc_skus):,} EXP SKUs...")
    if exp_sfc_skus:
        conn = _connect()
        SQL_EXP = """
            SELECT li.item_number,
                   MAX(CASE WHEN ib.qc_ExpiredDate>0 THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END) AS fc_max_exp_date,
                   MIN(CASE WHEN ib.qc_ExpiredDate>0 THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END) AS fc_min_exp_date
            FROM yamibuy_wh.wh_lot_inventory li
            JOIN yamibuy_wh.wh_storage_location wsl ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
            LEFT JOIN yamibuy_wh.wh_inbound_batch ib ON ib.lot_id=li.lot_id
            WHERE li.warehouse_number='002' AND li.quantity>0
              AND wsl.location_type IN (3,4)
              AND wsl.zone_id != 2
              AND li.item_number IN ({ph})
            GROUP BY li.item_number
        """
        all_rows, all_cols = [], None
        for chunk in [exp_sfc_skus[i:i+500] for i in range(0, len(exp_sfc_skus), 500)]:
            ph = ",".join(["%s"]*len(chunk))
            with conn.cursor() as cur:
                cur.execute(SQL_EXP.format(ph=ph), chunk)
                if all_cols is None:
                    all_cols = [d[0] for d in cur.description]
                all_rows.extend(cur.fetchall())
        conn.close()
        df_fc_exp = pd.DataFrame(all_rows, columns=all_cols)
        df_fc_exp["item_number"]     = df_fc_exp["item_number"].astype(str)
        df_fc_exp["fc_max_exp_date"] = pd.to_datetime(df_fc_exp["fc_max_exp_date"], errors="coerce")
        df_fc_exp["fc_min_exp_date"] = pd.to_datetime(df_fc_exp["fc_min_exp_date"], errors="coerce")
    db.write_fc_exp(df_fc_exp)
    elapsed = time.perf_counter() - t0
    print(f"      {len(df_fc_exp):,} rows  ({elapsed:.1f}s)")
    log.append(f"fc_exp: {len(df_fc_exp):,} rows in {elapsed:.1f}s")

# ── Step 7: Build task lists ───────────────────────────────────────────────────
print(f"\n  [7] Building T1-T4 task lists...")
t0 = time.perf_counter()
try:
    from logic.sfc_to_fc import build_sfc_to_fc_lists
    from config import P
    cfg = dict(P)
    df_routing = df[df["item_number"].isin(sfc_skus)].copy()
    if not df_fc_exp.empty:
        df_routing = df_routing.merge(
            df_fc_exp[["item_number","fc_min_exp_date","fc_max_exp_date"]],
            on="item_number", how="left"
        )
    task_lists = build_sfc_to_fc_lists(df_lots, df_routing, cfg)
    db.write_task_lists(task_lists)

    # Pre-build T1 with 7d target (default slider) and cache as T1_7d
    _cfg_7d = dict(cfg)
    _cfg_7d["t1_top_target_d2s"] = 7
    _cfg_7d["t1_mid_target_d2s"] = 7
    _t1_7d = build_sfc_to_fc_lists(df_lots, df_routing, _cfg_7d, target_lists=["T1"])
    db.write_task_lists({"T1_7d": _t1_7d.get("T1", pd.DataFrame())})
    print(f"        T1_7d: {len(_t1_7d.get('T1', pd.DataFrame())):,} rows (7d target)")

    elapsed = time.perf_counter() - t0
    total_rows = sum(len(v) for v in task_lists.values() if v is not None and not v.empty)
    print(f"      {total_rows:,} rows in {elapsed:.1f}s")
    for k, v in task_lists.items():
        print(f"        {k}: {len(v) if v is not None and not v.empty else 0:,} rows")
    log.append(f"task_lists: {total_rows:,} rows in {elapsed:.1f}s")
except Exception as e:
    import traceback
    print(f"      ERROR: {e}"); traceback.print_exc()
    log.append(f"task_lists: FAILED ({e})")

# ── Step 8a: FC→SFC triggers — full SKU-level (fast, no MySQL lot query) ──────
# Always runs — triggers are derived from existing df (no new MySQL needed)
print(f"\n  [8a] Building FC→SFC trigger summary (all SKUs)...")
t0 = time.perf_counter()
try:
    from logic.fc_to_sfc import compute_fc_to_sfc as _compute_fc2sfc
    from config import P as _P2

    _cfg2 = dict(_P2)
    _df_triggered2 = _compute_fc2sfc(df, _cfg2)

    if not _df_triggered2.empty:
        _mid  = _df_triggered2[_df_triggered2["trigger"]=="MIDDLE_EVICT"].copy()
        _tail = _df_triggered2[_df_triggered2["trigger"]=="TAIL_CLEAR"].copy()

        # Build trigger records: one row per SKU, SKU-level only (no lot detail)
        def _build_trigger_rows(trigger_df, trigger_type):
            if trigger_df.empty:
                return pd.DataFrame()
            t = trigger_df.copy()
            t["trigger_type"] = trigger_type
            t["item_number"]  = t["item_number"].astype(str)
            for col in ["fc_qty","sfc_qty","fc_d2s","avg_daily_orders","excess_qty","keep_qty","seg"]:
                if col not in t.columns:
                    t[col] = None
            return t[["trigger_type","item_number","fc_qty","sfc_qty","fc_d2s",
                       "avg_daily_orders","excess_qty","keep_qty","seg"]]

        _trigger_rows = pd.concat([
            _build_trigger_rows(_mid,  "MIDDLE_EVICT"),
            _build_trigger_rows(_tail, "TAIL_CLEAR"),
        ], ignore_index=True)

        db.write_fc2sfc_triggers(_trigger_rows)
        elapsed = time.perf_counter() - t0
        print(f"      MIDDLE: {len(_mid):,} SKUs, TAIL: {len(_tail):,} SKUs  ({elapsed:.1f}s)")
        log.append(f"fc2sfc_triggers: MIDDLE={len(_mid):,} TAIL={len(_tail):,} in {elapsed:.1f}s")
    else:
        print("      No FC→SFC triggers")
        log.append("fc2sfc_triggers: no triggers")
        _mid  = pd.DataFrame()
        _tail = pd.DataFrame()

except Exception as e:
    import traceback
    print(f"      ERROR in Step 8a: {e}"); traceback.print_exc()
    log.append(f"fc2sfc_triggers: FAILED ({e})")
    _mid  = pd.DataFrame()
    _tail = pd.DataFrame()

# ── Step 8b: FC→SFC tasks — top-N lot-level (slow MySQL, 30-min TTL) ──────────
if db.age_minutes("fc2sfc_tasks") < 30:
    print("  [8b] FC→SFC tasks — cache fresh (<30min), skipping.")
    log.append("fc2sfc_tasks: cache hit (skipped)")
else:
    print(f"\n  [8b] Building FC→SFC lot-level tasks (top-N cap)...")
    t0 = time.perf_counter()
    try:
        from logic.fc_to_sfc import (
            select_middle_fc_locations as _sel_mid,
            select_tail_fc_locations as _sel_tail,
        )
        from data.loader import load_fc_lot_data as _load_fc_lots

        if not _mid.empty or not _tail.empty:
            # No cap — query all triggered SKUs for full lot-level tasks
            _mid_skus  = _mid.sort_values("avg_daily_orders", ascending=True)["item_number"].tolist() if not _mid.empty else []
            _tail_skus = _tail.sort_values("avg_daily_orders", ascending=True)["item_number"].tolist() if not _tail.empty else []
            _fc_skus   = list(set(_mid_skus + _tail_skus))
            print(f"      MIDDLE {len(_mid_skus):,} + TAIL {len(_tail_skus):,} → {len(_fc_skus):,} unique SKUs (no cap)")

            _df_fc_lots, _fc_err = _load_fc_lots(_fc_skus)
            if _fc_err:
                print(f"      FC lot query error: {_fc_err}")
                _df_fc_lots = pd.DataFrame()
            else:
                print(f"      FC lots: {len(_df_fc_lots):,} rows")

            # ── LOC_CLEAR pre-computation: query total SKU count per location ──
            # We need to know how many SKUs are in EACH location (including
            # SKUs NOT in the transfer list) to determine if a location can
            # be fully vacated.
            # This is a lightweight aggregation query — no lot detail needed.
            _loc_total_skus: dict = {}  # location_no → total distinct SKUs
            try:
                _conn_lc = _connect()
                with _conn_lc.cursor() as _cur_lc:
                    _cur_lc.execute("""
                        SELECT li.location_no,
                               COUNT(DISTINCT li.item_number) AS total_skus
                        FROM yamibuy_wh.wh_lot_inventory li
                        JOIN yamibuy_wh.wh_storage_location wsl
                            ON wsl.location_no = li.location_no
                            AND wsl.warehouse_number = li.warehouse_number
                        WHERE li.warehouse_number = '002'
                          AND li.quantity > 0
                          AND wsl.location_type IN (3, 4)
                          AND wsl.zone_id != 2
                        GROUP BY li.location_no
                    """)
                    _loc_total_skus = {r[0]: r[1] for r in _cur_lc.fetchall()}
                _conn_lc.close()
                print(f"      Location SKU counts: {len(_loc_total_skus):,} locations")
            except Exception as _elc:
                print(f"      Location SKU count query failed: {_elc} — LOC_CLEAR disabled")

            # Build location-level rows for MIDDLE EVICT
            _mid_rows = []
            if not _df_fc_lots.empty and _mid_skus:
                _mid_set = set(_mid_skus)
                for _, _sku_row in _mid.iterrows():
                    if str(_sku_row["item_number"]) not in _mid_set:
                        continue
                    _locs = _sel_mid(_df_fc_lots, _sku_row)
                    if not _locs.empty:
                        _locs["segment"]          = "MIDDLE"
                        _locs["trigger_type"]     = "MIDDLE_EVICT"
                        _locs["excess_qty"]       = float(_sku_row.get("excess_qty", 0) or 0)
                        _locs["keep_qty"]         = float(_sku_row.get("keep_qty", 0) or 0)
                        _locs["fc_d2s"]           = _sku_row.get("fc_d2s", None)
                        _locs["avg_daily_orders"] = float(_sku_row.get("avg_daily_orders", 0) or 0)
                        _locs["excess_vol"]       = float(_sku_row.get("excess_vol", 0) or 0)
                        _mid_rows.append(_locs)

            # Build location-level rows for TAIL CLEAR
            _tail_rows = []
            if not _df_fc_lots.empty and _tail_skus:
                _tail_set = set(_tail_skus)
                for _, _sku_row in _tail.iterrows():
                    _sku = str(_sku_row["item_number"])
                    if _sku not in _tail_set:
                        continue
                    _locs = _sel_tail(_df_fc_lots, _sku)
                    if not _locs.empty:
                        _locs["segment"]          = "TAIL"
                        _locs["trigger_type"]     = "TAIL_CLEAR"
                        _locs["excess_qty"]       = float(_sku_row.get("fc_qty", 0) or 0)
                        _locs["keep_qty"]         = 0.0
                        _locs["fc_d2s"]           = _sku_row.get("fc_d2s", None)
                        _locs["avg_daily_orders"] = float(_sku_row.get("avg_daily_orders", 0) or 0)
                        _locs["excess_vol"]       = float(_sku_row.get("excess_vol", 0) or 0)
                        _tail_rows.append(_locs)

            _df_mid_locs  = pd.concat(_mid_rows,  ignore_index=True) if _mid_rows  else pd.DataFrame()
            _df_tail_locs = pd.concat(_tail_rows, ignore_index=True) if _tail_rows else pd.DataFrame()

            # Sort by location_no ASC
            if not _df_mid_locs.empty and "location_no" in _df_mid_locs.columns:
                _df_mid_locs = _df_mid_locs.sort_values("location_no", ascending=True).reset_index(drop=True)
            if not _df_tail_locs.empty and "location_no" in _df_tail_locs.columns:
                _df_tail_locs = _df_tail_locs.sort_values("location_no", ascending=True).reset_index(drop=True)

            # ── LOC_CLEAR: locations where ALL SKUs are in the transfer set ────
            # A location can be fully vacated when every SKU stored there is
            # also being transferred out. These get highest priority.
            _df_loc_clear = pd.DataFrame()
            if not _df_fc_lots.empty and _loc_total_skus:
                # For each location in the transfer rows, count how many
                # distinct transfer SKUs are there vs total SKUs in that location
                _transfer_set = set(str(s) for s in _fc_skus)

                # Count transfer SKUs per location (from df_fc_lots — transfer SKUs only)
                _loc_transfer_skus = (
                    _df_fc_lots.groupby("location_no")["item_number"]
                    .apply(lambda x: len(set(x.astype(str))))
                )

                # A location is clearable if its transfer SKU count == total SKU count
                _clearable_locs = set(
                    loc for loc, transfer_cnt in _loc_transfer_skus.items()
                    if _loc_total_skus.get(loc, 0) == transfer_cnt
                    and transfer_cnt > 0
                )

                if _clearable_locs:
                    _mid_lc  = _df_mid_locs[_df_mid_locs["location_no"].isin(_clearable_locs)].copy()
                    _tail_lc = _df_tail_locs[_df_tail_locs["location_no"].isin(_clearable_locs)].copy()
                    for _df_ in [_mid_lc, _tail_lc]:
                        _df_["rule"] = "LOC_CLEAR"
                    _df_loc_clear = pd.concat([_mid_lc, _tail_lc], ignore_index=True)
                    if not _df_loc_clear.empty:
                        _df_loc_clear = _df_loc_clear.sort_values("location_no", ascending=True).reset_index(drop=True)

                    # Remove LOC_CLEAR locs from the original lists
                    _df_mid_locs  = _df_mid_locs[~_df_mid_locs["location_no"].isin(_clearable_locs)].copy()
                    _df_tail_locs = _df_tail_locs[~_df_tail_locs["location_no"].isin(_clearable_locs)].copy()

                    _n_lc_locs = _df_loc_clear["location_no"].nunique() if not _df_loc_clear.empty else 0
                    print(f"      LOC_CLEAR: {_n_lc_locs:,} locations, {len(_df_loc_clear):,} rows")

            db.write_fc2sfc_tasks({
                "LOC_CLEAR":    _df_loc_clear,
                "MIDDLE_EVICT": _df_mid_locs,
                "TAIL_CLEAR":   _df_tail_locs,
            })
            elapsed = time.perf_counter() - t0
            print(f"      MIDDLE_EVICT: {len(_df_mid_locs):,} rows")
            print(f"      TAIL_CLEAR:   {len(_df_tail_locs):,} rows  ({elapsed:.1f}s)")
            log.append(f"fc2sfc_tasks: LOC_CLEAR={len(_df_loc_clear):,} MIDDLE={len(_df_mid_locs):,} TAIL={len(_df_tail_locs):,} in {elapsed:.1f}s")
        else:
            print("      No triggers — skipping lot query")
            log.append("fc2sfc_tasks: no triggers")

    except Exception as e:
        import traceback
        print(f"      ERROR in Step 8b: {e}"); traceback.print_exc()
        log.append(f"fc2sfc_tasks: FAILED ({e})")

# ── Step 9: Pending putaway (30-min TTL — same as main cron cycle) ───────────
if db.age_minutes("pending_putaway") < 30:
    print("  [9] Pending putaway — cache fresh, skipping.")
    log.append("pending_putaway: cache hit (skipped)")
else:
    t0 = time.perf_counter()
    print("  [9] Loading pending putaway from RECG locations...")
    try:
        from data.loader import load_pending_putaway as _load_pa
        _df_pa, _pa_err = _load_pa()
        if _pa_err:
            print(f"      Pending putaway query failed: {_pa_err}")
            log.append(f"pending_putaway: FAILED ({_pa_err})")
        else:
            db.write_pending_putaway(_df_pa)
            elapsed = time.perf_counter() - t0
            print(f"      {len(_df_pa):,} SKUs pending putaway  ({elapsed:.1f}s)")
            log.append(f"pending_putaway: {len(_df_pa):,} SKUs in {elapsed:.1f}s")
    except Exception as e:
        print(f"      ERROR in Step 9: {e}")
        log.append(f"pending_putaway: FAILED ({e})")

# ── Clean up legacy parquet/csv files if they exist ───────────────────────────
for legacy in [
    "results/sfc_lot_cache.parquet", "results/sfc_lot_cache_ts.txt",
    "results/exp_flags_cache.parquet", "results/exp_flags_cache_ts.txt",
    "results/fc_exp_cache.parquet", "results/fc_exp_cache_ts.txt",
    "results/athena_cache_ts.txt",   # keep athena_cache.csv for manual inspection
]:
    p = Path(legacy)
    if p.exists():
        p.unlink()
        print(f"  Removed legacy file: {legacy}")

# ── Write refresh log ──────────────────────────────────────────────────────────
total_time = time.perf_counter() - t_start
ts_str = _now_nj().strftime("%Y-%m-%d %H:%M ET")
summary = f"[{ts_str}] Refresh done in {total_time:.1f}s | " + " | ".join(log)
print(f"\n{summary}")
LOG_FILE = Path("results/refresh_log.txt")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
existing = LOG_FILE.read_text().splitlines() if LOG_FILE.exists() else []
(LOG_FILE).write_text("\n".join((existing + [summary])[-50:]) + "\n")
print(f"Log written to {LOG_FILE}")
