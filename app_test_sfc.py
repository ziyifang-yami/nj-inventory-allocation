"""
app_test_sfc.py — SFC→FC test build with SQLite cache. All 4 lists, with timing.
Run: streamlit run app_test_sfc.py --server.port 8511
"""
import sys, os, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in [_HERE, _PARENT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd
import numpy as np
import streamlit as st
import pymysql
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from data.loader import load, load_inventory, load_sfc_lot_data, load_fc_exp_summary
from logic.sfc_to_fc import build_sfc_to_fc_lists
from cache_db import CacheDB
from config import P

st.set_page_config(page_title="SFC→FC Plan", layout="wide")
st.markdown("## SFC → FC Plan")

DB_PATH = "results/sfc_cache.db"
DB_CACHE = CacheDB(DB_PATH)

# Show last sync time — use most recent of sfc_lots / fc_exp (they refresh together)
import sqlite3 as _sq3
_sq3_conn = _sq3.connect(DB_PATH)
def _get_ts(table):
    row = _sq3_conn.execute("SELECT updated_at FROM meta WHERE table_name=?", (table,)).fetchone()
    if not row: return None
    try: return datetime.fromisoformat(row[0]).astimezone(ZoneInfo("America/New_York"))
    except: return None

_ts_sfc = _get_ts("sfc_lots")
_ts_fc  = _get_ts("fc_exp")
_sq3_conn.close()

_ts_sync = max(t for t in [_ts_sfc, _ts_fc] if t is not None) if any([_ts_sfc, _ts_fc]) else None
if _ts_sync:
    _age_sync = (datetime.now(ZoneInfo("America/New_York")) - _ts_sync).total_seconds() / 60
    st.caption(f"🕐 Inventory transit log last sync: {_ts_sync.strftime('%Y-%m-%d %H:%M ET')}  ({_age_sync:.0f} min ago)")

# ── Timing helper ─────────────────────────────────────────────
_timings = {}
class _timed:
    def __init__(self, label): self.label = label
    def __enter__(self): self._t = time.perf_counter(); return self
    def __exit__(self, *a): _timings[self.label] = time.perf_counter() - self._t

# ── Step 1: Sales data — always from SQLite, refresh via sidebar button only ──
_CACHE_MAX_H = 12

# Sidebar — must run before cfg so slider values are available
with st.sidebar:
    st.markdown("### 🔄 Data Refresh")

    # ── Helper to show age badge ──────────────────────────────
    def _age_badge(table: str, ttl_h: float) -> str:
        age_h = DB_CACHE.age_minutes(table) / 60
        if age_h > 9000:       return "❌ not loaded"
        if age_h > ttl_h:      return f"⚠️ {age_h:.1f}h old (overdue)"
        return f"✅ {age_h:.1f}h ago"

    # ── Individual refresh buttons ────────────────────────────
    st.caption("**Sales** (Athena, 12h TTL)")
    st.caption(_age_badge("sales", 12))
    if st.button("↻ Sales", key="btn_sales", use_container_width=True):
        with st.spinner("Querying Athena... (~50s)"):
            try:
                _df_new, _ = load()
                _df_new["item_number"] = _df_new["item_number"].astype(str)
                DB_CACHE.write_sales(_df_new)
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")

    st.caption("**EXP Flags** (MySQL, 12h TTL)")
    st.caption(_age_badge("exp_flags", 12))
    if st.button("↻ EXP Flags", key="btn_exp_flags", use_container_width=True):
        with st.spinner("Querying MySQL..."):
            try:
                import pymysql as _pym
                _conn_r = _pym.connect(
                    host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
                    password=os.getenv("MYSQL_PASS"), port=int(os.getenv("MYSQL_PORT","3306")),
                    charset="utf8mb4", connect_timeout=15,
                )
                _all_items = []
                _in_stock = DB_CACHE.read_sales()["item_number"].astype(str).tolist() if not DB_CACHE.read_sales().empty else []
                _chunks = [_in_stock[i:i+500] for i in range(0, len(_in_stock), 500)]
                with _conn_r.cursor() as _cur:
                    for _chunk in _chunks:
                        _ph = ",".join(["%s"]*len(_chunk))
                        _cur.execute(f"SELECT item_number FROM yamibuy_im.im_item_extend WHERE need_minimal_exp=1 AND item_number IN ({_ph})", _chunk)
                        _all_items.extend(str(r[0]) for r in _cur.fetchall())
                _conn_r.close()
                DB_CACHE.write_exp_flags(_all_items)
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")

    st.caption("**FC Exp Dates** (MySQL, 30min TTL)")
    st.caption(_age_badge("fc_exp", 0.5))
    if st.button("↻ FC Exp", key="btn_fc_exp", use_container_width=True):
        with st.spinner("Querying MySQL..."):
            try:
                from data.loader import load_fc_exp_summary as _load_fc_exp
                _exp_set = DB_CACHE.read_exp_flags()
                _sfc_skus_all = DB_CACHE.read_sfc_lots()["item_number"].unique().tolist() if not DB_CACHE.read_sfc_lots().empty else []
                _exp_sfc = [s for s in _sfc_skus_all if s in _exp_set]
                if _exp_sfc:
                    _df_exp = _load_fc_exp(tuple(_exp_sfc))
                    DB_CACHE.write_fc_exp(_df_exp)
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")

    st.caption("**SFC Lots + Task Lists** (MySQL, 30min TTL)")
    st.caption(_age_badge("sfc_lots", 0.5))
    if st.button("↻ SFC Lots + Tasks", key="btn_sfc", use_container_width=True):
        with st.spinner("Running full refresh (~90s)..."):
            try:
                import subprocess, sys
                subprocess.run(
                    [sys.executable, "refresh_sfc_cache.py"],
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    timeout=180,
                )
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")

    # ── Watchdog status ───────────────────────────────────────
    st.divider()
    st.caption("**Watchdog** (auto-restart if stale)")
    _wd_log = Path(os.path.dirname(os.path.abspath(__file__))) / "results" / "watchdog.log"
    if _wd_log.exists():
        _wd_lines = _wd_log.read_text().strip().splitlines()
        _last_wd  = _wd_lines[-1] if _wd_lines else "—"
        _wd_short = _last_wd.split("] watchdog: ")[-1] if "] watchdog: " in _last_wd else _last_wd
        st.caption(f"✅ {_wd_short[:60]}")
    else:
        st.caption("⚠️ watchdog.log not found")

    st.divider()
    if st.button("🔄 Refresh All Data", key="btn_all", use_container_width=True, type="primary"):
        with st.spinner("Running full refresh (~90s)..."):
            try:
                import subprocess, sys
                subprocess.run(
                    [sys.executable, "refresh_sfc_cache.py"],
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    timeout=180,
                )
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")

# cfg after sidebar so slider values exist
cfg = dict(P)
cfg["t1_top_target_d2s"] = 7
cfg["t1_mid_target_d2s"] = 7

# ── Auto-refresh: if any table is overdue, kick off background refresh ────────
# This is a safety net — cron normally handles this every 30min.
# If cron missed a run (stale lock etc.), this catches it silently.
_OVERDUE = {
    "sales":     12 * 60,   # 12h in minutes
    "exp_flags": 12 * 60,
    "fc_exp":    35,         # 30min + 5min buffer
    "sfc_lots":  35,         # 30min + 5min buffer
}
_overdue_tables = [t for t, ttl in _OVERDUE.items() if DB_CACHE.age_minutes(t) > ttl]
if _overdue_tables:
    st.info(f"⏳ Data overdue: {', '.join(_overdue_tables)}. Triggering background refresh...")
    try:
        import subprocess as _sp, sys as _sys
        _lock = Path("results/refresh.lock")
        _lock_stale = False
        if _lock.exists():
            try:
                _pid = int(_lock.read_text().strip())
                os.kill(_pid, 0)   # raises if PID dead
            except (ValueError, ProcessLookupError, PermissionError):
                _lock.unlink(missing_ok=True)
                _lock_stale = True
        if not _lock.exists():
            _sp.Popen(
                [_sys.executable, "refresh_sfc_cache.py"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
            )
    except Exception:
        pass  # background refresh failure is non-fatal — cron will catch it

with _timed("Sales"):
    df_sales = DB_CACHE.read_sales()
    if df_sales.empty:
        st.error(
            "Sales cache not found in SQLite. Run the refresh script first:\n\n"
            "`python refresh_sfc_cache.py`"
        )
        st.stop()
    _sales_age_h = DB_CACHE.age_minutes("sales") / 60
    try:
        import sqlite3 as _sqlite3
        _conn2 = _sqlite3.connect(DB_PATH)
        _ts_raw = _conn2.execute("SELECT updated_at FROM meta WHERE table_name='sales'").fetchone()
        _conn2.close()
        _ts_sales = datetime.fromisoformat(_ts_raw[0]).astimezone(ZoneInfo("America/New_York"))
        DATA_TS = _ts_sales.strftime("updated %Y-%m-%d %H:%M ET")
    except Exception:
        DATA_TS = "cached"
    if _sales_age_h > _CACHE_MAX_H:
        st.warning(f"⚠️ Sales cache is {_sales_age_h:.1f}h old. Use the sidebar button to refresh.")

# ── Step 2: Inventory (always real-time) ──────────────────────
with _timed("Inventory"):
    df_inv, inv_err = load_inventory()

if inv_err:
    st.error(f"Inventory load failed: {inv_err}")
    st.stop()

# ── Step 3: Merge + classify + need_minimal_exp ───────────────
# need_minimal_exp helper — reads from SQLite (fast)
_EXP_MAX_H = 24

@st.cache_data(ttl=43200, show_spinner=False)  # 12h in-memory — exp flags rarely change
def _cached_exp_flags_from_db():
    """Read EXP flags from SQLite with Streamlit in-memory cache."""
    return DB_CACHE.read_exp_flags()


def _get_exp_flags():
    """Return EXP flags — from Streamlit memory cache if available, else SQLite/MySQL."""
    age_h = DB_CACHE.age_minutes("exp_flags") / 60
    if age_h < 12:
        return _cached_exp_flags_from_db()
    # Re-query MySQL and write to SQLite
    from dotenv import load_dotenv
    load_dotenv(dotenv_path="../.env")
    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"), port=3306,
        charset="utf8mb4", connect_timeout=8,
    )
    all_items = []
    in_stock = df_inv["item_number"].astype(str).tolist()
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
    DB_CACHE.write_exp_flags(all_items)
    _cached_exp_flags_from_db.clear()  # invalidate memory cache
    return set(all_items)

with _timed("EXP Flags"):
    df_sales["item_number"] = df_sales["item_number"].astype(str)
    df_inv["item_number"]   = df_inv["item_number"].astype(str)
    df = df_inv.merge(df_sales.drop(columns=["fc_qty","sfc_qty"], errors="ignore"),
                      on="item_number", how="left")
    for c in ["active_order_days","order_lines_30d","avg_daily_orders",
              "sales_units_30d","unit_volume_cbm"]:
        df[c] = pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0)
    df["fc_qty"]  = pd.to_numeric(df["fc_qty"],  errors="coerce").fillna(0)
    df["sfc_qty"] = pd.to_numeric(df["sfc_qty"], errors="coerce").fillna(0)
    df = df[(df["fc_qty"] > 0) | (df["sfc_qty"] > 0)].copy()

    aod_top, ol_top, ol_tail = P["aod_top"], P["ol_top"], P["ol_tail"]
    _aod = df["active_order_days"].to_numpy(dtype=float)
    _ol  = df["order_lines_30d"].to_numpy(dtype=float)
    df["seg"] = np.where((_aod >= aod_top) & (_ol >= ol_top), "TOP",
                         np.where(_ol < ol_tail, "TAIL", "MIDDLE"))
    df["fc_d2s"] = np.where(df["avg_daily_orders"] > 0,
                            df["fc_qty"] / df["avg_daily_orders"], np.nan)

    # TBD: in-stock days < 30 OR no sales data (NaN) → not enough history, exclude from transfer decisions
    if "inv_days_30" in df.columns:
        df.loc[df["inv_days_30"].isna() | (df["inv_days_30"] < 30), "seg"] = "TBD"

    # need_minimal_exp from SQLite (fast after first run)
    exp_items = _get_exp_flags()
    df["need_minimal_exp"] = df["item_number"].isin(exp_items).astype(int)

# ── Step 4: Pre-filter candidates ────────────────────────────
_d2s = df["fc_d2s"].fillna(999.0)
_mask = (
    (df["sfc_qty"] > 0) & (
        (df["seg"] == "TOP") |
        ((df["seg"] == "MIDDLE") & ((_d2s < 14) | (df["need_minimal_exp"] == 1)))
    )
)
sfc_skus = df[_mask]["item_number"].tolist()
st.caption(
    f"Total SFC SKUs: {(df['sfc_qty']>0).sum():,}  |  "
    f"Transfer candidates: **{len(sfc_skus):,}**"
)

# ── Step 5: SFC lot data — SQLite cache ───────────────────────
_SFC_MAX_H = 2

def _get_sfc_lots():
    """Read SFC lots from SQLite. Re-query MySQL if older than 2h."""
    age_min = DB_CACHE.age_minutes("sfc_lots")
    if age_min < _SFC_MAX_H * 60:
        # Fast path: read from SQLite, filter to candidates in Python
        df_all = DB_CACHE.read_sfc_lots()
        if not df_all.empty:
            return df_all[df_all["item_number"].isin(sfc_skus)].copy(), None
    # Re-query MySQL
    df_new, err = load_sfc_lot_data(tuple(sfc_skus))
    if err:
        return pd.DataFrame(), err
    DB_CACHE.write_sfc_lots(df_new)
    return df_new, None

with _timed(f"SFC Lots"):
    df_sfc_lots, sfc_err = _get_sfc_lots()

if sfc_err:
    st.warning(f"SFC lot data failed: {sfc_err}")
    df_sfc_lots = pd.DataFrame()

# ── Step 6: FC exp — SQLite cache ────────────────────────────
_FC_EXP_MAX_H = 2

def _get_fc_exp():
    """Read FC exp from SQLite. Re-query MySQL if older than 2h."""
    age_min = DB_CACHE.age_minutes("fc_exp")
    if age_min < _FC_EXP_MAX_H * 60:
        exp_sfc = [s for s in sfc_skus if s in exp_items]
        if exp_sfc:
            return DB_CACHE.read_fc_exp(exp_sfc)
        return pd.DataFrame(columns=["item_number","fc_min_exp_date","fc_max_exp_date"])
    # Re-query MySQL
    exp_sfc = [s for s in sfc_skus if s in exp_items]
    if not exp_sfc:
        return pd.DataFrame(columns=["item_number","fc_min_exp_date","fc_max_exp_date"])
    df_exp = load_fc_exp_summary(tuple(exp_sfc))
    DB_CACHE.write_fc_exp(df_exp)
    return df_exp

with _timed("FC Exp"):
    df_fc_exp = _get_fc_exp()

# ── Step 7: Build T1-T4 or read from SQLite cache ────────────
_TASK_MAX_H = 24  # task lists rebuilt by cron; keep for a full day

def _get_task_lists():
    age_min = DB_CACHE.age_minutes("task_list")
    if age_min < _TASK_MAX_H * 60:
        return {k: DB_CACHE.read_task_list(k) for k in ["T1","T2","T3","T4"]}, True
    # Build and write to DB
    df_routing = df[df["item_number"].isin(sfc_skus)].copy()
    if not df_fc_exp.empty:
        df_routing = df_routing.merge(
            df_fc_exp[["item_number","fc_min_exp_date","fc_max_exp_date"]],
            on="item_number", how="left"
        )
    lists = build_sfc_to_fc_lists(
        df_sfc_lots if df_sfc_lots is not None and not df_sfc_lots.empty
        else pd.DataFrame(),
        df_routing, cfg,
    )
    DB_CACHE.write_task_lists(lists)
    return lists, False

with _timed("SFC Lots + Tasks"):
    task_lists, _from_cache = _get_task_lists()

# ── Timing ────────────────────────────────────────────────────
total = sum(_timings.values())
st.markdown("### ⏱ Timing")
cols_t = st.columns(len(_timings) + 1)
for i, (label, t) in enumerate(_timings.items()):
    cols_t[i].metric(label, f"{t:.2f}s")
cols_t[-1].metric("**Total**", f"{total:.2f}s")

# ── All 4 lists ───────────────────────────────────────────────
_DISPLAY_COLS = [
    "segment","item_number","location_no","loc_type","qty_to_transfer","exp_date",
    "case_qty","fc_d2s","avg_daily_orders","source_type",
]

_COL_RENAME = {
    "case_qty": "case_spec",
}

def _nrows(k):
    t = task_lists.get(k)
    return len(t) if t is not None and not t.empty else 0

t1, t2, t3, t4 = st.tabs([
    f"🚨 T1 — Emergency Transfer SFC→FC  ({_nrows('T1')} rows)",
    f"📦 T2 — Off-Peak Replenishment SFC→FC  ({_nrows('T2')} rows)",
    f"🔄 T3 — SFC Bin → SFC Stock  ({_nrows('T3')} rows)",
    f"📅 T4 — Cross-Zone Date Switch SFC→FC  ({_nrows('T4')} rows)",
])

# ── Shared display helper ─────────────────────────────────────
def _render_table(tdf, key, label, extra_caption=""):
    """Apply search/filter/sort/display to a task list DataFrame."""
    if tdf is None or tdf.empty:
        st.info(f"No {label} tasks.")
        return

    cols = [c for c in _DISPLAY_COLS if c in tdf.columns]
    tdf = tdf[cols].copy()
    for c in ["qty_to_transfer","case_qty","fc_d2s","avg_daily_orders"]:
        if c in tdf.columns:
            tdf[c] = pd.to_numeric(tdf[c], errors="coerce")

    # ── Filters + Rows/Page — single row ─────────────────────
    _fc1, _fc2, _fc3, _fc4 = st.columns([1.5, 1, 1, 0.7])
    with _fc1:
        _search = st.text_input("🔍 SKU", key=f"search_{key}", placeholder="Item number")
    with _fc2:
        _segs = ["All"] + sorted(tdf["segment"].dropna().unique().tolist()) if "segment" in tdf.columns else ["All"]
        _seg_filter = st.selectbox("Segment", _segs, key=f"seg_{key}")
    with _fc3:
        _loctypes = ["All"] + sorted(tdf["loc_type"].dropna().unique().tolist()) if "loc_type" in tdf.columns else ["All"]
        _loc_filter = st.selectbox("Loc Type", _loctypes, key=f"loc_{key}")
    with _fc4:
        st.selectbox(
            "Rows/Page", options=[50, 100, 200, 500, "All"],
            index=1, key=f"rowlimit_{key}", label_visibility="visible",
        )

    if _search:
        tdf = tdf[tdf["item_number"].astype(str).str.contains(_search.strip(), case=False, na=False)]
    if _seg_filter != "All" and "segment" in tdf.columns:
        tdf = tdf[tdf["segment"] == _seg_filter]
    if _loc_filter != "All" and "loc_type" in tdf.columns:
        tdf = tdf[tdf["loc_type"] == _loc_filter]

    # ── Sort: TOP first then MIDDLE, within each segment by location_no ASC ──
    # T1/T2 global sort: segment → fc_d2s ASC → OL DESC → ADL DESC
    # (location_no applied within each page only, not globally)
    if "segment" in tdf.columns and "location_no" in tdf.columns:
        tdf["_seg_order"] = tdf["segment"].map({"TOP": 0, "MIDDLE": 1}).fillna(2)
        if key in ("T1", "T2") and "fc_d2s" in tdf.columns:
            sort_cols = ["_seg_order", "fc_d2s"]
            sort_asc  = [True, True]
            if "order_lines_30d" in tdf.columns:
                sort_cols.append("order_lines_30d"); sort_asc.append(False)
            if "avg_daily_orders" in tdf.columns:
                sort_cols.append("avg_daily_orders"); sort_asc.append(False)
            tdf = tdf.sort_values(sort_cols, ascending=sort_asc, na_position="last")
        else:
            tdf = tdf.sort_values(
                ["_seg_order", "location_no"], ascending=[True, True], na_position="last"
            )
        tdf = tdf.drop(columns=["_seg_order"])
    elif "location_no" in tdf.columns:
        tdf = tdf.sort_values("location_no", ascending=True, na_position="last")

    tdf = tdf.reset_index(drop=True)
    tdf.index = tdf.index + 1
    tdf = tdf.rename(columns=_COL_RENAME)

    n_skus = tdf["item_number"].nunique() if "item_number" in tdf.columns else "?"
    n_locs = tdf["location_no"].nunique() if "location_no" in tdf.columns else "?"
    total_rows = len(tdf)
    caption = f"Total {total_rows:,} rows · {n_skus} SKUs · {n_locs} Locations"
    if extra_caption:
        caption += f" · {extra_caption}"

    # ── rows/page + page state ────────────────────────────────
    _preset   = st.session_state.get(f"rowlimit_{key}", 100)
    _page     = st.session_state.get(f"page_{key}", 1)
    _per_page = total_rows if _preset == "All" else int(_preset)
    _total_pages = max(1, -(-total_rows // _per_page))
    _page = max(1, min(_page, _total_pages))
    st.session_state[f"page_{key}"] = _page
    tdf_show = tdf.iloc[(_page - 1) * _per_page : _page * _per_page].copy()
    # Within-page location sort (only when paginated, not All)
    if _preset != "All" and "location_no" in tdf_show.columns:
        tdf_show = tdf_show.sort_values("location_no", ascending=True, na_position="last")

    # ── Top bar: caption · Download CSV ──────────────────────
    _c1, _c3 = st.columns([4, 1])
    with _c1:
        st.caption(caption)
    with _c3:
        st.download_button(
            "⬇️ Download CSV", tdf_show.to_csv(index=False).encode(),
            file_name=f"sfc_to_fc_{key.lower()}.csv", mime="text/csv",
            key=f"dl_{key}", use_container_width=True,
        )

    st.dataframe(tdf_show, use_container_width=True)

    # ── Inject CSS: narrow page input only ───────────────────
    st.markdown(f"""<style>
    div[data-testid="stTextInput"][data-key="page_input_{key}"] {{
        max-width: 70px !important;
    }}
    div[data-testid="stTextInput"][data-key="page_input_{key}"] input {{
        text-align: center; font-size: 0.85rem !important;
    }}
    </style>""", unsafe_allow_html=True)

    # ── Footer: rows/page left · compact page nav right ───────
    _shown = sorted(set(
        [1, _total_pages] +
        list(range(max(1, _page - 2), min(_total_pages, _page + 2) + 1))
    )) if _total_pages > 1 else []

    _page_items = []
    _prev = None
    for _pn in _shown:
        if _prev and _pn - _prev > 1:
            _page_items.append(("ellipsis", "…"))
        _page_items.append(("current" if _pn == _page else "btn", _pn))
        _prev = _pn

    # ── Footer: page nav far right ────────────────────────────
    if _total_pages > 1:
        # Render nav as HTML — only the input needs a real widget
        _nav_left, _nav_right = st.columns([5.0, 0.8])
        with _nav_right:
            _c1, _c2, _c3, _c4, _c5 = st.columns([0.2, 0.2, 0.2, 0.2, 0.2])
            with _c1:
                if st.button("‹", key=f"prev_{key}", disabled=_page == 1):
                    st.session_state[f"page_{key}"] = _page - 1; st.rerun()
            with _c2:
                st.markdown('<p style="font-size:1 rem;color:#555;margin:0;padding-top:8px;white-space:nowrap">Page</p>', unsafe_allow_html=True)
            with _c3:
                _go = st.text_input("pg", value=str(_page), key=f"page_input_{key}", label_visibility="collapsed")
                try:
                    _go_int = int(_go)
                    if 1 <= _go_int <= _total_pages and _go_int != _page:
                        st.session_state[f"page_{key}"] = _go_int; st.rerun()
                except ValueError:
                    pass
            with _c4:
                st.markdown(f'<p style="font-size:1rem;color:#555;margin:0;padding-top:8px;white-space:nowrap">/ {_total_pages}</p>', unsafe_allow_html=True)
            with _c5:
                if st.button("›", key=f"next_{key}", disabled=_page == _total_pages):
                    st.session_state[f"page_{key}"] = _page + 1; st.rerun()

# ── T1 tab — has transfer limit sliders ──────────────────────
with t1:
    # T1 transfer limit controls
    with st.expander("⚙️ T1 Transfer Limit", expanded=False):
        st.caption("Transfer just enough SFC to bring FC up to this many days of stock.")
        _c1, _c2 = st.columns(2)
        with _c1:
            st.slider(
                "TOP — Target D2S", min_value=1, max_value=30, value=st.session_state.get("t1_top_slider", 7), step=1,
                key="t1_top_slider",
                help="TOP SKUs: cap transfer qty so FC reaches this many D2S."
            )
        with _c2:
            st.slider(
                "MIDDLE — Target D2S", min_value=1, max_value=30, value=st.session_state.get("t1_mid_slider", 7), step=1,
                key="t1_mid_slider",
                help="MIDDLE emergency SKUs: cap transfer qty so FC reaches this many D2S."
            )

    _t1_top = st.session_state.get("t1_top_slider", 7)
    _t1_mid = st.session_state.get("t1_mid_slider", 7)
    _T1_DEFAULT = 7

    if _t1_top == _T1_DEFAULT and _t1_mid == _T1_DEFAULT:
        # Default — read pre-built T1_7d from SQLite cache (instant)
        tdf = DB_CACHE.read_task_list("T1_7d")
        if tdf is None or tdf.empty:
            # T1_7d not in cache yet — build live as fallback
            _cfg_t1 = dict(cfg)
            _cfg_t1["t1_top_target_d2s"] = 7
            _cfg_t1["t1_mid_target_d2s"] = 7
            _df_r = df[df["item_number"].isin(sfc_skus)].copy()
            if not df_fc_exp.empty:
                _df_r = _df_r.merge(
                    df_fc_exp[["item_number","fc_min_exp_date","fc_max_exp_date"]],
                    on="item_number", how="left"
                )
            _t1_build = build_sfc_to_fc_lists(
                df_sfc_lots if df_sfc_lots is not None and not df_sfc_lots.empty else pd.DataFrame(),
                _df_r, _cfg_t1, target_lists=["T1"],
            )
            tdf = _t1_build.get("T1", pd.DataFrame())
            _t1_label = ""
        else:
            _t1_label = ""
    else:
        # Non-default — rebuild live with slider values (~0.05s)
        _cfg_t1 = dict(cfg)
        _cfg_t1["t1_top_target_d2s"] = _t1_top
        _cfg_t1["t1_mid_target_d2s"] = _t1_mid
        _df_r = df[df["item_number"].isin(sfc_skus)].copy()
        if not df_fc_exp.empty:
            _df_r = _df_r.merge(
                df_fc_exp[["item_number","fc_min_exp_date","fc_max_exp_date"]],
                on="item_number", how="left"
            )
        _t1_build = build_sfc_to_fc_lists(
            df_sfc_lots if df_sfc_lots is not None and not df_sfc_lots.empty else pd.DataFrame(),
            _df_r, _cfg_t1, target_lists=["T1"],
        )
        tdf = _t1_build.get("T1", pd.DataFrame())
        _t1_label = ""

    if tdf is None or tdf.empty:
        st.info("No T1 tasks.")
    else:
        _render_table(tdf, "T1", "T1", extra_caption=_t1_label)

# ── T2 / T3 / T4 tabs ────────────────────────────────────────
for tab, key, label in [(t2,"T2","T2"),(t3,"T3","T3"),(t4,"T4","T4")]:
    with tab:
        _render_table(task_lists.get(key), key, label)
