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
               SUM(CASE WHEN wsli.location_no NOT LIKE 'S%%' AND wsli.location_no NOT LIKE 'DC%%'
                        THEN wit.quantity ELSE 0 END) AS fc_qty,
               SUM(CASE WHEN wsli.location_no LIKE 'S%%' OR wsli.location_no LIKE 'DC%%'
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
      AND (li.location_no LIKE 'S%%' OR li.location_no LIKE 'DC%%')
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
              AND li.location_no NOT LIKE 'S%%' AND li.location_no NOT LIKE 'DC%%'
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
