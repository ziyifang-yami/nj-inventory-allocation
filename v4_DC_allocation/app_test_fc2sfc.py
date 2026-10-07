"""
app_test_fc2sfc.py — FC→SFC transfer plan with SQLite cache.
Run: streamlit run app_test_fc2sfc.py --server.port 8513

Two lists per design.md §2:
  MIDDLE Evict  — FC D2S > 30, transfer excess to SFC, keep 30 D2S in FC
  TAIL Clear    — TAIL SKUs, full FC clear to SFC
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
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from data.loader import load, load_inventory
from logic.fc_to_sfc import compute_fc_to_sfc
from cache_db import CacheDB
from config import P

st.set_page_config(page_title="FC→SFC Plan", layout="wide")
st.markdown("## FC → SFC Plan")

DB_PATH  = "results/sfc_cache.db"
DB_CACHE = CacheDB(DB_PATH)

# ── Last sync timestamp ───────────────────────────────────────────────────────
import sqlite3 as _sq3
_sq3_conn = _sq3.connect(DB_PATH)
def _get_ts(table):
    row = _sq3_conn.execute("SELECT updated_at FROM meta WHERE table_name=?", (table,)).fetchone()
    if not row: return None
    try: return datetime.fromisoformat(row[0]).astimezone(ZoneInfo("America/New_York"))
    except: return None
_ts_mid  = _get_ts("fc2sfc_tasks")
_sq3_conn.close()
if _ts_mid:
    _age_min = (datetime.now(ZoneInfo("America/New_York")) - _ts_mid).total_seconds() / 60
    st.caption(f"🕐 FC inventory transit log last sync: {_ts_mid.strftime('%Y-%m-%d %H:%M ET')}  ({_age_min:.0f} min ago)")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔄 Data Refresh")

    def _age_badge(table: str, ttl_h: float) -> str:
        age_h = DB_CACHE.age_minutes(table) / 60
        if age_h > 9000:  return "❌ not loaded"
        if age_h > ttl_h: return f"⚠️ {age_h:.1f}h old (overdue)"
        return f"✅ {age_h:.1f}h ago"

    st.caption("**Triggers** (all SKUs, 30min TTL)")
    st.caption(_age_badge("fc2sfc_triggers", 0.5))
    st.caption("**Tasks** (lot-level, 1h TTL)")
    st.caption(_age_badge("fc2sfc_tasks", 1.0))

    if st.button("↻ FC→SFC Tasks", key="btn_fc2sfc", use_container_width=True):
        with st.spinner("Running full refresh (~7 min for full lot query)..."):
            try:
                import subprocess, sys as _sys
                subprocess.run([_sys.executable, "refresh_sfc_cache.py"],
                               cwd=_HERE, timeout=600)
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")

    st.divider()
    if st.button("🔄 Refresh All Data", key="btn_all", use_container_width=True, type="primary"):
        with st.spinner("Running full refresh (~7 min)..."):
            try:
                import subprocess, sys as _sys
                subprocess.run([_sys.executable, "refresh_sfc_cache.py"],
                               cwd=_HERE, timeout=600)
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")

# ── Auto-refresh if overdue (1h TTL for tasks) ────────────────────────────────
_tasks_age = DB_CACHE.age_minutes("fc2sfc_tasks")
if _tasks_age > 65:  # 1h + 5min buffer
    try:
        import subprocess as _sp, sys as _sys
        _lock = Path("results/refresh.lock")
        if _lock.exists():
            try:
                _pid = int(_lock.read_text().strip())
                os.kill(_pid, 0)  # check if alive
            except (ValueError, ProcessLookupError, PermissionError):
                _lock.unlink(missing_ok=True)
        if not _lock.exists():
            _sp.Popen([_sys.executable, "refresh_sfc_cache.py"],
                      cwd=_HERE, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            st.info("⏳ FC→SFC tasks overdue (>1h). Background refresh triggered.")
    except Exception:
        pass

# ── Timing helper ─────────────────────────────────────────────────────────────
_timings = {}
class _timed:
    def __init__(self, label): self.label = label
    def __enter__(self): self._t = time.perf_counter(); return self
    def __exit__(self, *a): _timings[self.label] = time.perf_counter() - self._t

# ── Step 1: Sales ─────────────────────────────────────────────────────────────
with _timed("Sales"):
    df_sales = DB_CACHE.read_sales()
    if df_sales.empty:
        st.error("Sales cache not found. Run `python refresh_sfc_cache.py` first.")
        st.stop()

# ── Step 2: Inventory ─────────────────────────────────────────────────────────
with _timed("Inventory"):
    df_inv, inv_err = load_inventory()
if inv_err:
    st.error(f"Inventory load failed: {inv_err}")
    st.stop()

# ── Step 3: Classify ──────────────────────────────────────────────────────────
with _timed("Classify"):
    df_inv["item_number"]   = df_inv["item_number"].astype(str)
    df_sales["item_number"] = df_sales["item_number"].astype(str)
    df = df_inv.merge(df_sales.drop(columns=["fc_qty","sfc_qty"], errors="ignore"),
                      on="item_number", how="left")
    for c in ["active_order_days","order_lines_30d","avg_daily_orders","sales_units_30d","unit_volume_cbm"]:
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

    # TBD: in-stock days < 30 → not enough sales history
    if "inv_days_30" in df.columns:
        df.loc[df["inv_days_30"] < 30, "seg"] = "TBD"

# ── Step 4: Trigger counts — read from SQLite fc2sfc_triggers ─────────────────
with _timed("Triggers"):
    df_mid_trigger  = DB_CACHE.read_fc2sfc_triggers("MIDDLE_EVICT")
    df_tail_trigger = DB_CACHE.read_fc2sfc_triggers("TAIL_CLEAR")

# ── Step 5: Read location-level task lists from SQLite ────────────────────────
with _timed("FC→SFC Tasks"):
    df_loc_clear = DB_CACHE.read_fc2sfc_task("LOC_CLEAR")
    df_mid_locs  = DB_CACHE.read_fc2sfc_task("MIDDLE_EVICT")
    df_tail_locs = DB_CACHE.read_fc2sfc_task("TAIL_CLEAR")

# ── Timing ────────────────────────────────────────────────────────────────────
total = sum(_timings.values())
st.markdown("### ⏱ Timing")
cols_t = st.columns(len(_timings) + 1)
for i, (label, t) in enumerate(_timings.items()):
    cols_t[i].metric(label, f"{t:.2f}s")
cols_t[-1].metric("**Total**", f"{total:.2f}s")

# ── Display columns — same format as SFC→FC ───────────────────────────────────
_DISPLAY_COLS = [
    "segment","item_number","location_no","loc_type","qty_to_transfer",
    "qty","exp_date","fc_d2s","avg_daily_orders","rule",
]
_COL_RENAME = {}

def _render_tab(tdf, key, label):
    if tdf is None or tdf.empty:
        st.info(f"No {label} tasks. Data refreshes every 30 min.")
        return

    # Derive segment from trigger_type (don't merge from df — avoids classification drift)
    if "segment" not in tdf.columns or tdf["segment"].isna().all():
        if "trigger_type" in tdf.columns:
            tdf["segment"] = tdf["trigger_type"].map({
                "MIDDLE_EVICT": "MIDDLE",
                "TAIL_CLEAR":   "TAIL",
            }).fillna("")
        elif key == "middle_evict":
            tdf["segment"] = "MIDDLE"
        else:
            tdf["segment"] = "TAIL"

    # Backfill fc_d2s + avg_daily_orders from df if missing
    _missing_d2s = tdf["fc_d2s"].isna().all() if "fc_d2s" in tdf.columns else True
    if _missing_d2s:
        _meta = df[["item_number","fc_d2s","avg_daily_orders"]].drop_duplicates("item_number")
        tdf = tdf.drop(columns=["fc_d2s","avg_daily_orders"], errors="ignore")
        tdf = tdf.merge(_meta, on="item_number", how="left")

    # Numeric cast
    for c in ["qty_to_transfer","qty","fc_d2s","avg_daily_orders","excess_qty","keep_qty"]:
        if c in tdf.columns:
            tdf[c] = pd.to_numeric(tdf[c], errors="coerce")

    # Restrict to display columns
    cols = [c for c in _DISPLAY_COLS if c in tdf.columns]
    tdf  = tdf[cols].copy()

    # ── Filters + Rows/Page — single row ─────────────────────
    _fc1, _fc2, _fc3, _fc4 = st.columns([1.2, 1, 1, 0.7])
    with _fc1:
        _s = st.text_input("🔍 SKU", key=f"s_{key}", placeholder="Item number")
    with _fc2:
        _segs = ["All"] + sorted(tdf["segment"].dropna().unique().tolist()) if "segment" in tdf.columns else ["All"]
        _sf = st.selectbox("Segment", _segs, key=f"sf_{key}")
    with _fc3:
        _locs = ["All"] + sorted(tdf["loc_type"].dropna().unique().tolist()) if "loc_type" in tdf.columns else ["All"]
        _lf = st.selectbox("Loc Type", _locs, key=f"lf_{key}")
    with _fc4:
        st.selectbox("Rows/Page", options=[50, 100, 200, 500, "All"],
                     index=1, key=f"rowlimit_{key}", label_visibility="visible")

    if _s:
        tdf = tdf[tdf["item_number"].astype(str).str.contains(_s.strip(), case=False, na=False)]
    if _sf != "All" and "segment" in tdf.columns:
        tdf = tdf[tdf["segment"] == _sf]
    if _lf != "All" and "loc_type" in tdf.columns:
        tdf = tdf[tdf["loc_type"] == _lf]

    # Sort: location_no ASC
    if "location_no" in tdf.columns:
        tdf = tdf.sort_values("location_no", ascending=True, na_position="last")
    tdf = tdf.reset_index(drop=True)
    tdf.index = tdf.index + 1
    tdf = tdf.rename(columns=_COL_RENAME)

    # ── Pagination state ──────────────────────────────────────
    total_rows = len(tdf)
    n_skus = tdf["item_number"].nunique() if "item_number" in tdf.columns else "?"
    caption = f"Total {total_rows:,} rows · {n_skus} SKUs"
    _preset = st.session_state.get(f"rowlimit_{key}", 100)
    _page   = st.session_state.get(f"page_{key}", 1)
    _per_page = total_rows if _preset == "All" else int(_preset)
    _total_pages = max(1, -(-total_rows // _per_page))
    _page = max(1, min(_page, _total_pages))
    st.session_state[f"page_{key}"] = _page
    tdf_show = tdf.iloc[(_page - 1) * _per_page : _page * _per_page]

    # ── Caption + Download ────────────────────────────────────
    _cap_col, _dl_col = st.columns([4, 1])
    with _cap_col:
        st.caption(caption)
    with _dl_col:
        st.download_button(
            "⬇️ Download CSV", tdf_show.to_csv(index=False).encode(),
            file_name=f"fc_to_sfc_{key.lower()}.csv", mime="text/csv",
            key=f"dl_{key}", use_container_width=True,
        )

    st.dataframe(tdf_show, use_container_width=True)

    # ── CSS + page nav ────────────────────────────────────────
    st.markdown("""<style>
    div[data-testid="stTextInput"] > div > div > input { max-width: 32px !important; }
    </style>""", unsafe_allow_html=True)

    if _total_pages > 1:
        _nav_left, _nav_right = st.columns([5.0, 0.8])
        with _nav_right:
            _n1, _n2, _n3, _n4, _n5 = st.columns([0.2, 0.2, 0.2, 0.2, 0.2])
            with _n1:
                if st.button("‹", key=f"prev_{key}", disabled=_page == 1):
                    st.session_state[f"page_{key}"] = _page - 1; st.rerun()
            with _n2:
                st.markdown('<p style="font-size:1rem;color:#555;margin:0;padding-top:8px;white-space:nowrap">Page</p>', unsafe_allow_html=True)
            with _n3:
                _go = st.text_input("pg", value=str(_page), key=f"page_input_{key}", label_visibility="collapsed")
                try:
                    _go_int = int(_go)
                    if 1 <= _go_int <= _total_pages and _go_int != _page:
                        st.session_state[f"page_{key}"] = _go_int; st.rerun()
                except ValueError:
                    pass
            with _n4:
                st.markdown(f'<p style="font-size:1rem;color:#555;margin:0;padding-top:8px;white-space:nowrap">/ {_total_pages}</p>', unsafe_allow_html=True)
            with _n5:
                if st.button("›", key=f"next_{key}", disabled=_page == _total_pages):
                    st.session_state[f"page_{key}"] = _page + 1; st.rerun()

# ── Tabs — LOC_CLEAR first (highest priority), then TAIL, then MIDDLE ─────────
tab_loc, tab_tail, tab_mid = st.tabs([
    f"🏷️ LOC Clear — Vacate Location  ({len(df_loc_clear):,} rows)",
    f"🗑️ TAIL Clear — FC→SFC  ({len(df_tail_locs):,} rows)",
    f"📤 MIDDLE Evict — FC→SFC  ({len(df_mid_locs):,} rows)",
])

with tab_loc:
    _n_loc_skus = df_loc_clear["item_number"].nunique() if not df_loc_clear.empty else 0
    _n_loc_locs = df_loc_clear["location_no"].nunique() if not df_loc_clear.empty else 0
    st.caption(
        f"**Highest priority.** Locations where ALL SKUs are being transferred out — "
        f"clearing these frees up entire locations.  "
        f"**{_n_loc_locs:,} locations · {_n_loc_skus:,} SKUs** (MIDDLE + TAIL combined)"
    )
    _render_tab(df_loc_clear, "loc_clear", "LOC Clear")

with tab_tail:
    _n_tail_total = len(df_tail_trigger) if not df_tail_trigger.empty else 0
    _n_tail_tasks = df_tail_locs["item_number"].nunique() if not df_tail_locs.empty else 0
    st.caption(f"TAIL SKUs (OL < 8) with FC stock. Full clear — move all FC lots to SFC. "
               f"LOC_CLEAR SKUs excluded.  "
               f"**{_n_tail_total:,} total triggered · {_n_tail_tasks:,} remaining SKUs**")
    _render_tab(df_tail_locs, "tail_clear", "TAIL Clear")

with tab_mid:
    _n_mid_total = len(df_mid_trigger) if not df_mid_trigger.empty else 0
    _n_mid_tasks = df_mid_locs["item_number"].nunique() if not df_mid_locs.empty else 0
    st.caption(f"SKUs where FC D2S > 30. Transfer excess to SFC, keep 30 D2S in FC. "
               f"LOC_CLEAR SKUs excluded.  "
               f"**{_n_mid_total:,} total triggered · {_n_mid_tasks:,} remaining SKUs**")
    _render_tab(df_mid_locs, "middle_evict", "MIDDLE Evict")
