"""
v4_DC_allocation/app.py — NJ Inventory Allocation v4 Streamlit UI

Tabs:
  1. Overview        — TOP/MIDDLE/TAIL metric cards + target bars + D2S + Capacity
  2. Before vs After — ideal state comparison (FC↔SFC shift)
  3. FC→SFC Plan     — MIDDLE evict list + TAIL clear list
  4. SFC→FC Plan     — T1/T2/T3/T4 task lists
  5. Putaway         — putaway decision table

Run: streamlit run app.py  (from v4_DC_allocation/)
"""

from __future__ import annotations

import sys
import os

# Ensure v4_DC_allocation/ is importable as a package when run as `streamlit run app.py`
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in [_HERE, _PARENT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

# ── v4 module imports ─────────────────────────────────────────────────────────
from data.loader import (
    load_inventory,
    load_capacity,
    load,
    load_fc_lot_data,
    load_sfc_lot_data,
    load_sku_loc_stats,
    load_fc_exp_summary,
    load_fc_exp_summary_all,
)
from logic.classify import classify_df
from logic.fc_to_sfc import (
    compute_fc_to_sfc,
    select_middle_fc_locations,
    select_tail_fc_locations,
    build_fc_to_sfc_queue,
    reorder_by_location,
)
from logic.sfc_to_fc import (
    compute_top_sfc_to_fc,
    build_top_queue,
    route_middle_sku,
    build_middle_lists,
    select_middle_sfc_locations,
    build_sfc_to_fc_lists,
    estimate_pending_counts,
)
from logic.putaway import decide_putaway, run_putaway
from config import P


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NJ Inventory Allocation",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global style ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
  .block-container { padding-top: 1.5rem; }

  /* segment metric cards */
  .seg-card {
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 8px;
    color: #fff;
  }
  .seg-card h2 { margin: 0 0 4px 0; font-size: 1rem; font-weight: 500; opacity: .85; }
  .seg-card .big { font-size: 2rem; font-weight: 700; margin: 0; }
  .seg-card .sub { font-size: .85rem; opacity: .8; margin-top: 6px; }
  .seg-card .row { display: flex; gap: 20px; margin-top: 12px; }
  .seg-card .stat { flex: 1; }
  .seg-card .stat-label { font-size: .7rem; opacity: .7; text-transform: uppercase; letter-spacing: .04em; }
  .seg-card .stat-val   { font-size: 1.1rem; font-weight: 600; }

  /* sticky tabs */
  div[data-testid="stTabs"] > div:first-child {
    position: sticky;
    top: 0;
    z-index: 999;
    background: white;
    padding: 8px 0 4px 0;
    border-bottom: 1px solid #eee;
  }
</style>
""", unsafe_allow_html=True)

# ── Palette ───────────────────────────────────────────────────────────────────
COLORS = {
    "TOP":    "#E57373",   # 淡红
    "MIDDLE": "#5B8DEF",   # 蓝色
    "TAIL":   "#F0A500",   # 黄色
    "TBD":    "#9E9E9E",   # 灰色
}
TARGETS = {"SKU %": 10.2, "OL %": 70.2, "Units %": 77.7, "CBI %": 80.4}


# ── Refresh helper ────────────────────────────────────────────────────────────
def _do_refresh():
    from pathlib import Path
    Path("results/athena_cache.csv").unlink(missing_ok=True)
    Path("results/athena_cache_ts.txt").unlink(missing_ok=True)
    load.clear()
    load_inventory.clear()
    load_capacity.clear()
    st.session_state.pop("df_main", None)


# ── Data loading ──────────────────────────────────────────────────────────────
with st.spinner("Loading sales & inventory data…"):
    try:
        df_sales, DATA_TS = load()
    except FileNotFoundError as exc:
        st.error(f"Data load failed: {exc}")
        st.stop()

df_inv_rt, inv_err = load_inventory()

if df_inv_rt is not None:
    df_sales["item_number"] = df_sales["item_number"].astype(str)
    df_inv_rt["item_number"] = df_inv_rt["item_number"].astype(str)

    # Merge live inventory (fc_qty, sfc_qty) into sales data
    df = df_inv_rt.merge(
        df_sales.drop(columns=["fc_qty", "sfc_qty"], errors="ignore"),
        on="item_number",
        how="left",
    )
    df["fc_qty"]  = df["fc_qty"].fillna(0)
    df["sfc_qty"] = df["sfc_qty"].fillna(0)
    INV_TS = "live"
else:
    df = df_sales.copy()
    if "fc_qty" not in df.columns:
        df["fc_qty"]  = 0
        df["sfc_qty"] = 0
    INV_TS = f"CSV only ({DATA_TS})"

# Ensure numeric sales columns
for _col in ["active_order_days", "order_lines_30d", "avg_daily_orders",
             "sales_units_30d", "unit_volume_cbm"]:
    if _col in df.columns:
        df[_col] = pd.to_numeric(df[_col], errors="coerce").fillna(0)
    else:
        df[_col] = 0

# Keep only SKUs with stock on hand
df["fc_qty"]  = pd.to_numeric(df["fc_qty"], errors="coerce").fillna(0)
df["sfc_qty"] = pd.to_numeric(df["sfc_qty"], errors="coerce").fillna(0)
df = df[(df["fc_qty"] > 0) | (df["sfc_qty"] > 0)].copy()

# Derive fc_d2s
df["fc_d2s"] = df.apply(
    lambda r: (r["fc_qty"] / r["avg_daily_orders"])
    if r["avg_daily_orders"] > 0 else float("nan"),
    axis=1,
)


# ── calc_ideal (from server v3) ───────────────────────────────────────────────
def calc_ideal(df_in, aod_top, ol_top, ol_tail,
               top_trigger=28, top_target=28,
               mid_routine=28, mid_emergency=14,
               mid_evict=30, mid_target=28,
               min_loc_qty=5):
    """
    Vectorized ideal-state calculation — replaces the row-by-row Python loop.
    ~50x faster on 44K SKUs (avoids iterrows overhead).
    """
    import numpy as np

    df = df_in.copy()
    n  = len(df)

    fc  = df["fc_qty"].to_numpy(dtype=float)
    sfc = df["sfc_qty"].to_numpy(dtype=float)
    adl = df["avg_daily_orders"].to_numpy(dtype=float)

    # volume: prefer unit_volume_cbi, fall back to unit_volume_cbm
    if "unit_volume_cbi" in df.columns:
        vol = df["unit_volume_cbi"].fillna(0).to_numpy(dtype=float)
        vol = np.where(vol > 0, vol, df.get("unit_volume_cbm", pd.Series(0, index=df.index)).fillna(0).to_numpy(dtype=float))
    else:
        vol = df.get("unit_volume_cbm", pd.Series(0, index=df.index)).fillna(0).to_numpy(dtype=float)

    seg = df["seg"].to_numpy()

    with np.errstate(divide="ignore", invalid="ignore"):
        fc_d2s = np.where(adl > 0, fc / adl, np.nan)

    target_qty = np.where(adl > 0, np.round(adl * mid_target), 0.0)

    action        = np.full(n, "KEEP",  dtype=object)
    transfer_qty  = np.zeros(n)
    fc_ideal      = fc.copy()
    sfc_ideal     = sfc.copy()
    priority      = np.full(n, 999.0)
    reason        = np.full(n, "",      dtype=object)

    # ── TOP ──────────────────────────────────────────────────
    m_top = (seg == "TOP") & (sfc > 0)
    action[m_top]       = "DC_TO_FC"
    transfer_qty[m_top] = np.round(sfc[m_top])
    fc_ideal[m_top]     = fc[m_top] + sfc[m_top]
    sfc_ideal[m_top]    = 0.0
    d2s_top             = np.where(np.isnan(fc_d2s[m_top]), 0.0, fc_d2s[m_top])
    reason[m_top]       = np.array([f"TOP full SFC→FC transfer (FC D2S={v:.1f})" for v in d2s_top])

    # ── MIDDLE DC→FC ─────────────────────────────────────────
    d2s_safe = np.where(np.isnan(fc_d2s), 999.0, fc_d2s)
    needed   = np.maximum(0.0, target_qty - fc)
    xfer_mid = np.minimum(needed, sfc)

    m_mid_in = (
        (seg == "MIDDLE") & (adl > 0) &
        (d2s_safe <= mid_routine) & (sfc > 0) & (needed > 0)
    )
    action[m_mid_in]       = "DC_TO_FC"
    transfer_qty[m_mid_in] = np.round(xfer_mid[m_mid_in])
    fc_ideal[m_mid_in]     = fc[m_mid_in] + xfer_mid[m_mid_in]
    sfc_ideal[m_mid_in]    = sfc[m_mid_in] - xfer_mid[m_mid_in]
    batch_vol_mid          = xfer_mid * vol
    emerg                  = d2s_safe <= mid_emergency
    reason[m_mid_in]       = np.array([
        f"FC D2S={d:.1f} {'[EMERGENCY]' if e else '[ROUTINE]'}"
        for d, e in zip(d2s_safe[m_mid_in], emerg[m_mid_in])
    ])
    priority[m_mid_in] = (
        d2s_safe[m_mid_in] * 1e6
        - batch_vol_mid[m_mid_in] * 1e3
        - adl[m_mid_in]
    )

    # ── MIDDLE FC→DC ─────────────────────────────────────────
    keep_qty   = np.where(adl > 0, np.round(adl * mid_evict), fc)
    excess     = fc - keep_qty
    excess_vol = excess * vol

    m_mid_out = (
        (seg == "MIDDLE") & (adl > 0) &
        (d2s_safe > mid_evict) & (excess > min_loc_qty) &
        (action == "KEEP")
    )
    action[m_mid_out]       = "FC_TO_DC"
    transfer_qty[m_mid_out] = np.round(excess[m_mid_out])
    fc_ideal[m_mid_out]     = fc[m_mid_out] - excess[m_mid_out]
    sfc_ideal[m_mid_out]    = sfc[m_mid_out] + excess[m_mid_out]
    reason[m_mid_out]       = np.array([
        f"FC D2S={d:.1f} > {mid_evict}, excess={e:.0f}"
        for d, e in zip(d2s_safe[m_mid_out], excess[m_mid_out])
    ])
    priority[m_mid_out] = -excess_vol[m_mid_out] * 1e3 - excess[m_mid_out]

    # ── TAIL ─────────────────────────────────────────────────
    m_tail = (seg == "TAIL") & (fc > 0)
    action[m_tail]       = "FC_TO_DC"
    transfer_qty[m_tail] = fc[m_tail]
    fc_ideal[m_tail]     = 0.0
    sfc_ideal[m_tail]    = sfc[m_tail] + fc[m_tail]
    reason[m_tail]       = "TAIL: clear FC"
    priority[m_tail]     = 100.0

    # ── Assemble ──────────────────────────────────────────────
    fc_d2s_rounded = np.where(np.isnan(fc_d2s), None, np.round(fc_d2s, 1))

    return pd.DataFrame({
        "item_number":   df["item_number"].astype(str).values,
        "seg":           seg,
        "biz_type":      df["biz_type"].astype(str).values if "biz_type" in df.columns else "",
        "fc_qty":        fc,
        "sfc_qty":       sfc,
        "fc_d2s":        fc_d2s_rounded,
        "avg_daily":     np.round(adl, 2),
        "unit_vol":      np.round(vol, 4),
        "action":        action,
        "transfer_qty":  transfer_qty,
        "fc_ideal":      fc_ideal,
        "sfc_ideal":     sfc_ideal,
        "fc_vol_now":    np.round(fc       * vol),
        "sfc_vol_now":   np.round(sfc      * vol),
        "fc_vol_ideal":  np.round(fc_ideal * vol),
        "sfc_vol_ideal": np.round(sfc_ideal * vol),
        "priority":      np.round(priority, 3),
        "reason":        reason,
    })


# ── Session state initialisation ─────────────────────────────────────────────
if "aod_top_val" not in st.session_state:
    st.session_state.aod_top_val = P["aod_top"]
if "ol_top_val"  not in st.session_state:
    st.session_state.ol_top_val  = P["ol_top"]
if "ol_tail_val" not in st.session_state:
    st.session_state.ol_tail_val = P["ol_tail"]


# ── Sidebar: Tier Thresholds ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🎛 Tier Thresholds")
    st.markdown("---")

    if "aod_top_val" not in st.session_state: st.session_state.aod_top_val = P.get("aod_top", 21)
    if "ol_top_val"  not in st.session_state: st.session_state.ol_top_val  = P.get("ol_top", 100)
    if "ol_tail_val" not in st.session_state: st.session_state.ol_tail_val = P.get("ol_tail", 8)

    st.markdown("**TOP** — FC Primary")
    aod_top = st.slider("Active Order Days ≥", 1, 30,
                        value=st.session_state.aod_top_val, key="aod_top_sld")
    st.session_state.aod_top_val = aod_top

    ol_top = st.slider("30D Order Lines ≥", 1, 300,
                       value=st.session_state.ol_top_val, step=1, key="ol_top_sld")
    st.session_state.ol_top_val = ol_top

    st.markdown("---")
    st.markdown("**TAIL** — SFC Primary")
    ol_tail = st.slider("30D Order Lines <", 1, 50,
                        value=st.session_state.ol_tail_val, key="ol_tail_sld")
    st.session_state.ol_tail_val = ol_tail

    st.markdown("---")
    st.caption(f"Rolling 30 inv-days · wh=002 · 1P+FBY · {DATA_TS}")
    st.markdown("---")
    if st.button("🔄 Refresh Data", key="refresh_btn", use_container_width=True,
                 help="Re-query Athena and reload inventory"):
        _do_refresh()
        st.rerun()


# ── Classify ─────────────────────────────────────────────────
# Vectorized classify — avoid row-by-row apply (44K SKUs × Python call overhead)
import numpy as _np
_aod_arr = df["active_order_days"].to_numpy(dtype=float)
_ol_arr  = df["order_lines_30d"].to_numpy(dtype=float)
_seg_arr = _np.where(
    (_aod_arr >= aod_top) & (_ol_arr >= ol_top), "TOP",
    _np.where(_ol_arr < ol_tail, "TAIL", "MIDDLE")
)
df["seg"] = _seg_arr
df["biz_type"] = df["item_number"].astype(str).str[0].map({"1": "自营", "5": "FBY"}).fillna("Other")

# ── TBD: in-stock days < 30 → not enough sales history ───────
from cache_db import CacheDB as _CacheDB_main
_db_main = _CacheDB_main("results/sfc_cache.db")
if "inv_days_30" in df.columns:
    df.loc[df["inv_days_30"] < 30, "seg"] = "TBD"

# ── 只保留库内有库存的 SKU（fc_qty>0 或 sfc_qty>0）────────────
for c in ["fc_qty", "sfc_qty"]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
n_all_skus = len(df)
df = df[(df["fc_qty"] > 0) | (df["sfc_qty"] > 0)].copy()

# 重新算分母（基于有库存 SKU）
N   = len(df)
TOL = df["order_lines_30d"].sum()
TU  = df["sales_units_30d"].sum()
TC  = df["daily_sales_cbi"].sum() if "daily_sales_cbi" in df.columns else df["daily_sales_cbm"].sum()

def stats(seg):
    s = df[df["seg"] == seg]
    return dict(
        n=len(s),
        psku=len(s)/N*100,
        pol=s["order_lines_30d"].sum()/TOL*100 if TOL else 0,
        pu=s["sales_units_30d"].sum()/TU*100 if TU else 0,
        pc=s["daily_sales_cbm"].sum()/TC*100 if TC else 0,
        fc=int(s["fc_qty"].sum()),
        sfc=int(s["sfc_qty"].sum()),
    )

T = stats("TOP"); M = stats("MIDDLE"); TL = stats("TAIL")
TBD = stats("TBD")

# ── Header ────────────────────────────────────────────────────
st.markdown("## NJ Inventory Allocation")
_inv_status = '🟢 Live' if INV_TS == 'live' else '🟡 ' + INV_TS

st.markdown(
    f"<span style='color:#999;font-size:.9rem'>"
    f"In-stock SKUs: {N:,} &nbsp;|&nbsp; "
    f"Sales data: {DATA_TS} &nbsp;|&nbsp; "
    f"Inventory: {_inv_status}"
    f"</span>",
    unsafe_allow_html=True
)

# ── Top-level page tabs ───────────────────────────────────────
tab_dash, tab_compare, tab_fc2sfc, tab_sfc2fc, tab_putaway = st.tabs([
    "📊 Overview",
    "⚖️ Before vs After",
    "📤 FC→SFC Plan",
    "📥 SFC→FC Plan",
    "📦 Putaway",
])


with tab_dash:

    # ── Tier Cards — 4 columns including TBD ─────────────────────
    c1, c2, c3, c4 = st.columns(4)

    def card(col, label, st_data, color):
        with col:
            st.markdown(f"""
            <div class="seg-card" style="background:{color}">
              <h2>{label}</h2>
              <p class="big">{st_data['n']}</p>
              <p class="sub">{st_data['psku']:.1f}% of total SKUs</p>
              <div class="row">
                <div class="stat">
                  <div class="stat-label">Order Lines</div>
                  <div class="stat-val">{st_data['pol']:.1f}%</div>
                </div>
                <div class="stat">
                  <div class="stat-label">Sales Units</div>
                  <div class="stat-val">{st_data['pu']:.1f}%</div>
                </div>
                <div class="stat">
                  <div class="stat-label">Daily in³</div>
                  <div class="stat-val">{st_data['pc']:.1f}%</div>
                </div>
                <div class="stat">
                  <div class="stat-label">FC Qty</div>
                  <div class="stat-val">{st_data['fc']:,}</div>
                </div>
              </div>
            </div>
            """, unsafe_allow_html=True)

    card(c1, "TOP — FC Primary",      T,   COLORS["TOP"])
    card(c2, "MIDDLE — Dual WH",      M,   COLORS["MIDDLE"])
    card(c3, "TAIL — DC Primary",     TL,  COLORS["TAIL"])
    card(c4, "TBD — New Products",    TBD, "#888888")

    st.divider()

    _ALL_SEGS   = ["TOP", "MIDDLE", "TAIL", "TBD"]
    _ALL_COLORS = [COLORS[s] for s in _ALL_SEGS]
    _ALL_DATA   = [T, M, TL, TBD]

    # ── Three charts in three equal columns ───────────────────────
    ch1, ch2, ch3 = st.columns(3)

    with ch1:
        seg_df = pd.DataFrame([
            {"Tier": seg, "SKUs": d["n"]}
            for seg, d in zip(_ALL_SEGS, _ALL_DATA)
        ])
        fig_s = go.Figure(go.Bar(
            x=seg_df["Tier"], y=seg_df["SKUs"],
            marker_color=_ALL_COLORS,
            text=seg_df["SKUs"].apply(lambda v: f"{v:,}"),
            textposition="outside",
        ))
        fig_s.update_layout(
            margin=dict(t=40,b=0,l=0,r=0), height=300,
            title=dict(text="SKU Count", x=0, font_size=13),
            yaxis=dict(tickformat=",", showgrid=True, gridcolor="#f0f0f0"),
            xaxis=dict(showgrid=False), plot_bgcolor="white", showlegend=False,
        )
        st.plotly_chart(fig_s, use_container_width=True)

    with ch2:
        ol_df = pd.DataFrame([
            {"Tier": seg, "OL%": d["pol"]}
            for seg, d in zip(_ALL_SEGS, _ALL_DATA)
        ])
        fig_o = go.Figure(go.Bar(
            x=ol_df["Tier"], y=ol_df["OL%"],
            marker_color=_ALL_COLORS,
            text=ol_df["OL%"].apply(lambda v: f"{v:.1f}%"),
            textposition="outside",
        ))
        fig_o.update_layout(
            margin=dict(t=40,b=0,l=0,r=0), height=300,
            title=dict(text="Order Lines %", x=0, font_size=13),
            yaxis=dict(ticksuffix="%", showgrid=True, gridcolor="#f0f0f0"),
            xaxis=dict(showgrid=False), plot_bgcolor="white", showlegend=False,
        )
        st.plotly_chart(fig_o, use_container_width=True)

    with ch3:
        # FC and SFC side by side — 8 bars (4 segs × 2 zones)
        fig_inv = go.Figure()
        fig_inv.add_trace(go.Bar(
            name="FC", x=_ALL_SEGS,
            y=[d["fc"] for d in _ALL_DATA],
            marker_color=_ALL_COLORS, opacity=0.9,
        ))
        fig_inv.add_trace(go.Bar(
            name="SFC", x=_ALL_SEGS,
            y=[d["sfc"] for d in _ALL_DATA],
            marker_color=_ALL_COLORS, opacity=0.45,
        ))
        fig_inv.update_layout(
            barmode="group", height=300, margin=dict(t=40,b=0,l=0,r=0),
            title=dict(text="FC vs SFC Qty", x=0, font_size=13),
            legend=dict(orientation="h", x=1, xanchor="right", y=1.15),
            yaxis=dict(tickformat=",", showgrid=True, gridcolor="#f0f0f0"),
            xaxis=dict(showgrid=False), plot_bgcolor="white",
        )
        st.plotly_chart(fig_inv, use_container_width=True)



# ═══════════════════════════════════════════════════════════════════════════════


# TAB 2 — Before vs After (exact server v3)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_compare:
    # ── 动态计算理想状态（实时 sidebar 阈值 + Planning D2S 参数）─────────
    # Read Planning D2S params from session_state (set by Planning tab sliders)
    _top_trig   = st.session_state.get("p_top_trig",   28)
    _top_target = st.session_state.get("p_top_target", 28)
    _mid_rout   = st.session_state.get("p_mid_rout",   28)
    _mid_evict  = st.session_state.get("p_mid_evict",  30)
    _mid_target = st.session_state.get("p_mid_target", 28)

    # Cache key — only recompute when any param changes
    _ideal_key = (aod_top, ol_top, ol_tail,
                  _top_trig, _top_target, _mid_rout, _mid_evict, _mid_target)
    if (st.session_state.get("_ideal_cache_key") != _ideal_key
            or "df_ideal" not in st.session_state):
        st.session_state["df_ideal"] = calc_ideal(
            df, aod_top, ol_top, ol_tail,
            top_trigger=_top_trig,   top_target=_top_target,
            mid_routine=_mid_rout,   mid_emergency=14,
            mid_evict=_mid_evict,    mid_target=_mid_target,
        )
        st.session_state["_ideal_cache_key"] = _ideal_key
    df_ideal = st.session_state["df_ideal"]
    # df 已经是有库存全量，calc_ideal 输出行数与 Overview 完全一致

    # ── CSS helpers ───────────────────────────────────────
    def dc(val): return "#27ae60" if val>0 else "#e74c3c" if val<0 else "#999"
    def arrow(val): return "▲" if val>0 else "▼" if val<0 else "─"

    # ── Page header ───────────────────────────────────────
    st.markdown(
        f"<div style='padding:8px 0 12px 0;border-bottom:1px solid #eee;margin-bottom:16px'>"
        f"<span style='font-size:1.3rem;font-weight:700'>⚖️ Before vs After</span>"
        f"<span style='font-size:.82rem;color:#999;margin-left:12px'>"
        f"TOP: AOD≥{aod_top} & OL≥{ol_top} · TAIL: OL&lt;{ol_tail} · "
        f"{len(df_ideal):,} in-stock SKUs · Dynamic ideal state (no daily cap)"
        f"</span></div>",
        unsafe_allow_html=True
    )

    # ── Section 2: Inventory Allocation by Segment ───────────────
    st.markdown("#### Inventory Allocation by Segment")

    # compute loc cols here so cards can use them
    dists = df_ideal.copy()

    LOCS      = ["FC", "SFC", "Overlap"]
    _LOC_HEX   = {"FC":"#5B8DEF","SFC":"#34C4A4","Overlap":"#F0A500"}
    _LOC_ICONS = {"FC":"🏭","SFC":"🏬","Overlap":"🔄"}

    def dist_table(sub):
        def _cnt(s, fc_col, sfc_col):
            return (int((s[fc_col]>0).sum()),
                    int((s[sfc_col]>0).sum()),
                    int(((s[fc_col]>0)&(s[sfc_col]>0)).sum()))
        b_fc, b_sfc, b_ov = _cnt(sub, "fc_qty",   "sfc_qty")
        a_fc, a_sfc, a_ov = _cnt(sub, "fc_ideal", "sfc_ideal")
        return [
            {"loc":"FC",      "before":b_fc,  "after":a_fc,  "delta":a_fc-b_fc},
            {"loc":"SFC",     "before":b_sfc, "after":a_sfc, "delta":a_sfc-b_sfc},
            {"loc":"Overlap", "before":b_ov,  "after":a_ov,  "delta":a_ov-b_ov},
        ]

    def loc_row(loc, d_rows):
        r = d_rows.get(loc, {"before":0,"after":0,"delta":0})
        b, a, delta = r["before"], r["after"], r["delta"]
        dclr = "#27ae60" if delta>0 else "#e74c3c" if delta<0 else "#aaa"
        arr  = "▲" if delta>0 else "▼" if delta<0 else "─"
        icon = _LOC_ICONS.get(loc, "")
        return (
            f"<div style='display:grid;grid-template-columns:2fr 1.5fr 20px 1.5fr 1.5fr;"
            f"align-items:center;padding:7px 0;border-bottom:1px solid #f0f0f0;column-gap:4px'>"
            f"<span style='font-size:.95rem;color:#444'>{icon} {loc}</span>"
            f"<span style='font-size:.95rem;color:#888'>{b:,}</span>"
            f"<span style='color:#ccc;font-size:.9rem'>→</span>"
            f"<span style='font-size:.95rem;color:#222;font-weight:700'>{a:,}</span>"
            f"<span style='font-size:.95rem;font-weight:700;color:{dclr}'>"
            f"{arr} {abs(delta):,}</span>"
            f"</div>"
        )

    sc1, sc2, sc3 = st.columns(3)
    for col, seg, color in [
        (sc1, "TOP",    COLORS["TOP"]),
        (sc2, "MIDDLE", COLORS["MIDDLE"]),
        (sc3, "TAIL",   COLORS["TAIL"]),
    ]:
        sub   = df_ideal[df_ideal["seg"] == seg]
        sub_d = sub
        if sub.empty: continue

        n     = len(sub)
        fc_b  = int(sub["fc_qty"].sum());   fc_a  = int(sub["fc_ideal"].sum())
        sfc_b = int(sub["sfc_qty"].sum());  sfc_a = int(sub["sfc_ideal"].sum())
        fd    = fc_a - fc_b;  sd = sfc_a - sfc_b
        d_rows = {r["loc"]: r for r in dist_table(sub_d)}

        # volume (in³)
        fc_vb  = sub["fc_vol_now"].sum();   fc_va  = sub["fc_vol_ideal"].sum()
        sfc_vb = sub["sfc_vol_now"].sum();  sfc_va = sub["sfc_vol_ideal"].sum()
        fvd    = fc_va  - fc_vb
        svd    = sfc_va - sfc_vb

        # dual-warehouse (overlap): SKUs present in BOTH FC and SFC
        ov_mask_b = (sub["fc_qty"]    > 0) & (sub["sfc_qty"]    > 0)
        ov_mask_a = (sub["fc_ideal"]  > 0) & (sub["sfc_ideal"]  > 0)
        ov_qty_b  = int(sub.loc[ov_mask_b, "fc_qty"].sum()    + sub.loc[ov_mask_b, "sfc_qty"].sum())
        ov_qty_a  = int(sub.loc[ov_mask_a, "fc_ideal"].sum()  + sub.loc[ov_mask_a, "sfc_ideal"].sum())
        ov_vol_b  = sub.loc[ov_mask_b, "fc_vol_now"].sum()   + sub.loc[ov_mask_b, "sfc_vol_now"].sum()
        ov_vol_a  = sub.loc[ov_mask_a, "fc_vol_ideal"].sum() + sub.loc[ov_mask_a, "sfc_vol_ideal"].sum()
        ov_qty_d  = ov_qty_a - ov_qty_b
        ov_vol_d  = ov_vol_a - ov_vol_b

        # total (FC + SFC combined)
        tot_qty_b = fc_b  + sfc_b;   tot_qty_a = fc_a  + sfc_a
        tot_vol_b = fc_vb + sfc_vb;  tot_vol_a = fc_va + sfc_va
        tot_qty_d = tot_qty_a - tot_qty_b
        tot_vol_d = tot_vol_a - tot_vol_b
        tot_qty_dclr = "#27ae60" if tot_qty_d > 0 else "#e74c3c" if tot_qty_d < 0 else "#aaa"
        tot_vol_dclr = "#27ae60" if tot_vol_d > 0 else "#e74c3c" if tot_vol_d < 0 else "#aaa"
        tot_qty_arr  = "▲" if tot_qty_d > 0 else "▼" if tot_qty_d < 0 else "─"
        tot_vol_arr  = "▲" if tot_vol_d > 0 else "▼" if tot_vol_d < 0 else "─"

        qty_dclr_fc  = "#27ae60" if fd>0 else "#e74c3c" if fd<0 else "#aaa"
        qty_dclr_sfc = "#27ae60" if sd>0 else "#e74c3c" if sd<0 else "#aaa"
        qty_arr_fc   = "▲" if fd>0 else "▼" if fd<0 else "─"
        qty_arr_sfc  = "▲" if sd>0 else "▼" if sd<0 else "─"
        vol_dclr_fc  = "#27ae60" if fvd>0 else "#e74c3c" if fvd<0 else "#aaa"
        vol_dclr_sfc = "#27ae60" if svd>0 else "#e74c3c" if svd<0 else "#aaa"
        vol_arr_fc   = "▲" if fvd>0 else "▼" if fvd<0 else "─"
        vol_arr_sfc  = "▲" if svd>0 else "▼" if svd<0 else "─"

        col.markdown(f"""
        <div style="border:2px solid {color};border-radius:12px;padding:18px 20px">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
            <span style="background:{color};color:#fff;border-radius:6px;
                  padding:4px 14px;font-size:1.05rem;font-weight:700">{seg}</span>
            <span style="color:#555;font-size:1.05rem;font-weight:600">{n:,} SKUs total</span>
          </div>
          <div style="font-size:.88rem;font-weight:700;color:#555;text-transform:uppercase;
               letter-spacing:.05em;margin-bottom:8px">SKU Location (Before → After)
          </div>
          {loc_row("FC",      d_rows)}
          {loc_row("SFC",     d_rows)}
          {loc_row("Overlap", d_rows)}
          <div style="margin-top:14px;padding-top:12px;border-top:2px solid #f4f4f4">
            <div style="font-size:.88rem;font-weight:700;color:#555;text-transform:uppercase;
                 letter-spacing:.05em;margin-bottom:8px">Inventory Qty (Before → After)
              <span style="font-weight:400;color:#888;text-transform:none;letter-spacing:0;margin-left:6px">Total {tot_qty_b:,} qty</span>
            </div>
            <div style="display:flex;gap:16px">
              <div style="flex:1">
                <div style="font-size:.85rem;color:#666;margin-bottom:3px">🏭 FC</div>
                <div style="font-size:1rem;font-weight:700;color:#222">
                  {fc_b:,}<span style="font-size:.88rem;color:#bbb"> → </span>{fc_a:,}</div>
                <div style="font-size:.9rem;font-weight:700;color:{qty_dclr_fc};margin-top:2px">
                  {qty_arr_fc} {abs(fd):,}</div>
              </div>
              <div style="flex:1">
                <div style="font-size:.85rem;color:#666;margin-bottom:3px">🏬 SFC</div>
                <div style="font-size:1rem;font-weight:700;color:#222">
                  {sfc_b:,}<span style="font-size:.88rem;color:#bbb"> → </span>{sfc_a:,}</div>
                <div style="font-size:.9rem;font-weight:700;color:{qty_dclr_sfc};margin-top:2px">
                  {qty_arr_sfc} {abs(sd):,}</div>
              </div>
            </div>
          </div>
          <div style="margin-top:10px;padding-top:10px;border-top:1px solid #f4f4f4">
            <div style="font-size:.88rem;font-weight:700;color:#555;text-transform:uppercase;
                 letter-spacing:.05em;margin-bottom:8px">Volume in³ (Before → After)
              <span style="font-weight:400;color:#888;text-transform:none;letter-spacing:0;margin-left:6px">Total {tot_vol_b/1000:,.0f}K in³</span>
            </div>
            <div style="display:flex;gap:16px">
              <div style="flex:1">
                <div style="font-size:.85rem;color:#666;margin-bottom:3px">🏭 FC</div>
                <div style="font-size:1rem;font-weight:700;color:#222">
                  {fc_vb/1000:,.0f}K<span style="font-size:.88rem;color:#bbb"> → </span>{fc_va/1000:,.0f}K</div>
                <div style="font-size:.9rem;font-weight:700;color:{vol_dclr_fc};margin-top:2px">
                  {vol_arr_fc} {abs(fvd)/1000:,.0f}K</div>
              </div>
              <div style="flex:1">
                <div style="font-size:.85rem;color:#666;margin-bottom:3px">🏬 SFC</div>
                <div style="font-size:1rem;font-weight:700;color:#222">
                  {sfc_vb/1000:,.0f}K<span style="font-size:.88rem;color:#bbb"> → </span>{sfc_va/1000:,.0f}K</div>
                <div style="font-size:.9rem;font-weight:700;color:{vol_dclr_sfc};margin-top:2px">
                  {vol_arr_sfc} {abs(svd)/1000:,.0f}K</div>
              </div>
            </div>
          </div>
        </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Section 3: Overall Inventory Shift ────────────────
    st.markdown("#### Overall Inventory Shift")

    fc_now    = int(df_ideal["fc_qty"].sum())
    fc_after  = int(df_ideal["fc_ideal"].sum())
    sfc_now   = int(df_ideal["sfc_qty"].sum())
    sfc_after = int(df_ideal["sfc_ideal"].sum())

    fc_sku_b  = int((df_ideal["fc_qty"]>0).sum())
    fc_sku_a  = int((df_ideal["fc_ideal"]>0).sum())
    sfc_sku_b = int((df_ideal["sfc_qty"]>0).sum())
    sfc_sku_a = int((df_ideal["sfc_ideal"]>0).sum())

    fc_vol_b  = df_ideal["fc_vol_now"].sum()
    fc_vol_a  = df_ideal["fc_vol_ideal"].sum()
    sfc_vol_b = df_ideal["sfc_vol_now"].sum()
    sfc_vol_a = df_ideal["sfc_vol_ideal"].sum()

    def _fmt_num(v):
        if abs(v) >= 1_000_000: return f"{v/1_000_000:.1f}M"
        if abs(v) >= 1_000:     return f"{v/1_000:.0f}K"
        return f"{int(v):,}"

    def _fmt_sku(v):   return f"{int(v):,}"
    def _fmt_qty(v):   return f"{int(v):,}"
    def _fmt_vol(v):
        if abs(v) >= 1_000_000: return f"{v/1_000_000:.1f}M"
        if abs(v) >= 1_000:     return f"{v/1_000:.0f}K"
        return f"{v:.0f}"

    dc_to_fc = df_ideal[df_ideal["action"]=="DC_TO_FC"]
    fc_to_dc = df_ideal[df_ideal["action"]=="FC_TO_DC"]

    out_qty = int(fc_to_dc["transfer_qty"].sum())
    out_sku = len(fc_to_dc)
    out_vol = float(fc_to_dc["fc_vol_now"].sum() - fc_to_dc["fc_vol_ideal"].sum())

    in_qty  = int(dc_to_fc["transfer_qty"].sum())
    in_sku  = len(dc_to_fc)
    in_vol  = float(dc_to_fc["fc_vol_ideal"].sum() - dc_to_fc["fc_vol_now"].sum())

    st.markdown("""
    <style>
    @keyframes flow-r { from { stroke-dashoffset: 0; } to { stroke-dashoffset: -30; } }
    @keyframes flow-l { from { stroke-dashoffset: 0; } to { stroke-dashoffset: -30; } }
    .ois2 { border-radius:16px; border:1.5px solid #e8eaf0; padding:28px 36px;
            background:#fff; box-shadow:0 2px 12px rgba(0,0,0,.05); }
    .ois2-hdr { display:flex; align-items:center; padding-bottom:14px;
                border-bottom:2px solid #f0f0f0; margin-bottom:4px; }
    .ois2-row { display:flex; align-items:center; padding:18px 0;
                border-bottom:1px solid #f4f4f4; }
    .ois2-row:last-child { border-bottom:none; }
    .ois2-dim { width:80px; font-size:.82rem; font-weight:700; color:#bbb;
                text-transform:uppercase; letter-spacing:.07em; }
    .ois2-zone { flex:1; text-align:center; }
    .ois2-b { font-size:.9rem; font-weight:400; color:#888; }
    .ois2-a { font-size:1.8rem; font-weight:800; color:#222; line-height:1.1; margin-top:2px; }
    .ois2-mid { width:260px; display:flex; flex-direction:column;
                align-items:center; gap:6px; }
    .ois2-arrow-row { display:flex; align-items:center; gap:8px; width:100%; }
    .ois2-badge-red  { font-size:.85rem; font-weight:700; background:#fde8e8;
                       color:#e74c3c; border-radius:99px; padding:3px 12px;
                       white-space:nowrap; flex-shrink:0; }
    .ois2-badge-grn  { font-size:.85rem; font-weight:700; background:#e8f8f0;
                       color:#27ae60; border-radius:99px; padding:3px 12px;
                       white-space:nowrap; flex-shrink:0; }
    </style>
    """, unsafe_allow_html=True)

    def _arr_r(label):
        return (
            f"<div style='display:flex;flex-direction:column;align-items:center;gap:2px;width:100%'>"
            f"  <span style='font-size:.85rem;font-weight:700;color:#e74c3c'>{label}</span>"
            f"  <svg width='200' height='16' viewBox='0 0 200 16' xmlns='http://www.w3.org/2000/svg'>"
            f"  <defs><marker id='mr' markerWidth='6' markerHeight='6' refX='3' refY='3' orient='auto'>"
            f"  <path d='M0,0 L6,3 L0,6 Z' fill='#e74c3c'/></marker></defs>"
            f"  <line x1='2' y1='8' x2='188' y2='8' stroke='#e74c3c' stroke-width='2.5' "
            f"  stroke-dasharray='10,5' marker-end='url(#mr)' style='animation:flow-r 0.9s linear infinite'/>"
            f"  </svg>"
            f"</div>"
        )

    def _arr_l(label):
        return (
            f"<div style='display:flex;flex-direction:column;align-items:center;gap:2px;width:100%'>"
            f"  <span style='font-size:.85rem;font-weight:700;color:#27ae60'>{label}</span>"
            f"  <svg width='200' height='16' viewBox='0 0 200 16' xmlns='http://www.w3.org/2000/svg'>"
            f"  <defs><marker id='mg' markerWidth='6' markerHeight='6' refX='3' refY='3' orient='auto'>"
            f"  <path d='M0,0 L6,3 L0,6 Z' fill='#27ae60'/></marker></defs>"
            f"  <line x1='198' y1='8' x2='12' y2='8' stroke='#27ae60' stroke-width='2.5' "
            f"  stroke-dasharray='10,5' marker-end='url(#mg)' style='animation:flow-l 0.9s linear infinite'/>"
            f"  </svg>"
            f"</div>"
        )

    rows_data = [
        ("SKUs",   fc_sku_b, fc_sku_a, out_sku, sfc_sku_b, sfc_sku_a, in_sku, _fmt_sku),
        ("Qty",    fc_now,   fc_after, out_qty, sfc_now,   sfc_after,  in_qty, _fmt_qty),
        ("Volume", fc_vol_b, fc_vol_a, out_vol, sfc_vol_b, sfc_vol_a,  in_vol, _fmt_vol),
    ]

    hdr = (
        "<div class='ois2-hdr'>"
        "  <div class='ois2-dim'></div>"
        "  <div style='flex:1;text-align:center;font-size:1rem;font-weight:700;color:#444'>🏭 FC</div>"
        "  <div style='width:260px'></div>"
        "  <div style='flex:1;text-align:center;font-size:1rem;font-weight:700;color:#444'>🏬 SFC</div>"
        "</div>"
    )

    body = ""
    for dim, fc_b, fc_a, out_v, sfc_b, sfc_a, in_v, fmt in rows_data:
        body += (
            f"<div class='ois2-row'>"
            f"  <div class='ois2-dim'>{dim}</div>"
            f"  <div class='ois2-zone'>"
            f"    <div class='ois2-b'>{fmt(fc_b)}</div>"
            f"    <div class='ois2-a'>{fmt(fc_a)}</div>"
            f"  </div>"
            f"  <div class='ois2-mid' style='width:260px;gap:8px'>"
            f"    {_arr_r(fmt(out_v))}"
            f"    {_arr_l(fmt(in_v))}"
            f"  </div>"
            f"  <div class='ois2-zone'>"
            f"    <div class='ois2-b'>{fmt(sfc_b)}</div>"
            f"    <div class='ois2-a'>{fmt(sfc_a)}</div>"
            f"  </div>"
            f"</div>"
        )

    st.markdown(f"<div class='ois2'>{hdr}{body}</div>", unsafe_allow_html=True)

    with st.expander("🔍 FC Capacity Check (click to load)", expanded=False):
        df_cap, cap_err = load_capacity()
        if df_cap is not None:
            fc_cap  = df_cap[df_cap["zone"]=="FC"]["allowable_cap"].sum()
            fc_used = df_cap[df_cap["zone"]=="FC"]["used_volume"].sum()
            fc_free = fc_cap - fc_used
            fc_inbound_vol = max(0, fc_vol_a - fc_vol_b)
            fc_free_after  = fc_free - fc_inbound_vol
            util_before    = fc_used / max(fc_cap, 1) * 100
            util_after     = (fc_used + fc_inbound_vol) / max(fc_cap, 1) * 100
            feasible       = fc_free_after >= 0
            status_color = "#27ae60" if feasible else "#e74c3c"
            status_icon  = "✅" if feasible else "⚠️"
            status_text  = "Feasible" if feasible else "Capacity Risk"
            def _fv(v): return f"{v/1_000_000:.1f}M in³" if abs(v)>=1e6 else f"{v/1_000:.0f}K in³"
            st.markdown(f"""
            <div style="border:1.5px solid {status_color}33;border-radius:12px;
                 padding:16px 24px;background:#fafafa">
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
                <span style="font-size:1.1rem;font-weight:700;color:#333">
                  {status_icon} {status_text}</span>
              </div>
              <div style="display:flex;gap:32px;flex-wrap:wrap">
                <div><div style="font-size:.72rem;color:#aaa">FC Allowable Cap</div>
                     <div style="font-size:1.2rem;font-weight:700">{_fv(fc_cap)}</div></div>
                <div><div style="font-size:.72rem;color:#aaa">Currently Used</div>
                     <div style="font-size:1.2rem;font-weight:700">{_fv(fc_used)} ({util_before:.1f}%)</div></div>
                <div><div style="font-size:.72rem;color:#aaa">Need to Move In</div>
                     <div style="font-size:1.2rem;font-weight:700;color:#3d6fbe">{_fv(fc_inbound_vol)}</div></div>
                <div><div style="font-size:.72rem;color:#aaa">Free After Transfer</div>
                     <div style="font-size:1.2rem;font-weight:700;color:{status_color}">{_fv(fc_free_after)} ({util_after:.1f}%)</div></div>
              </div>
            </div>""", unsafe_allow_html=True)
        elif cap_err:
            st.caption(f"⚠️ {cap_err[:80]}")

    st.divider()

    # ── Section 4: Distribution — Before vs After ─────────
    _h1, _h2 = st.columns([3, 1])
    _h1.markdown("#### Distribution — Before vs After")
    dist_metric = _h2.selectbox(
        "metric", ["SKU Count", "Inventory Qty", "Volume in³"],
        key="dist_metric", label_visibility="collapsed"
    )

    ov_rows = dist_table(dists)

    # build y values based on selected metric
    if dist_metric == "SKU Count":
        y_before = [r["before"] for r in ov_rows]
        y_after  = [r["after"]  for r in ov_rows]
        y_title  = "SKU Count"
        fmt      = lambda v: f"{int(v):,}"
    elif dist_metric == "Inventory Qty":
        def _qty(zone, when):
            col_fc  = "fc_qty"   if when == "before" else "fc_ideal"
            col_sfc = "sfc_qty"  if when == "before" else "sfc_ideal"
            if zone == "FC":      return int(dists[col_fc].sum())
            if zone == "SFC":     return int(dists[col_sfc].sum())
            # Overlap = SKUs in both
            mask = (dists[col_fc] > 0) & (dists[col_sfc] > 0)
            return int(dists.loc[mask, col_fc].sum() + dists.loc[mask, col_sfc].sum())
        y_before = [_qty(r["loc"], "before") for r in ov_rows]
        y_after  = [_qty(r["loc"], "after")  for r in ov_rows]
        y_title  = "Qty"
        fmt      = lambda v: f"{int(v):,}"
    else:  # Volume
        def _vol(zone, when):
            col_fc  = "fc_vol_now"   if when == "before" else "fc_vol_ideal"
            col_sfc = "sfc_vol_now"  if when == "before" else "sfc_vol_ideal"
            if zone == "FC":      return dists[col_fc].sum()
            if zone == "SFC":     return dists[col_sfc].sum()
            mask = (dists["fc_qty"] > 0) & (dists["sfc_qty"] > 0) if when == "before" \
                   else (dists["fc_ideal"] > 0) & (dists["sfc_ideal"] > 0)
            return dists.loc[mask, col_fc].sum() + dists.loc[mask, col_sfc].sum()
        y_before = [_vol(r["loc"], "before") / 1000 for r in ov_rows]
        y_after  = [_vol(r["loc"], "after")  / 1000 for r in ov_rows]
        y_title  = "Volume (K in³)"
        fmt      = lambda v: f"{v:,.0f}K"

    x_labels = [f"{_LOC_ICONS[r['loc']]} {r['loc']}" for r in ov_rows]
    colors   = [_LOC_HEX[r["loc"]] for r in ov_rows]

    fig_ba = go.Figure()
    fig_ba.add_trace(go.Bar(
        name="Before", x=x_labels, y=y_before,
        marker_color=colors, opacity=0.4,
        text=[fmt(v) for v in y_before],
        textposition="outside", textfont=dict(size=14, color="#444"),
    ))
    fig_ba.add_trace(go.Bar(
        name="After", x=x_labels, y=y_after,
        marker_color=colors, opacity=1.0,
        text=[fmt(v) for v in y_after],
        textposition="outside", textfont=dict(size=14, color="#222"),
    ))
    fig_ba.update_layout(
        barmode="group", height=420,
        margin=dict(t=55, b=40, l=0, r=0),
        plot_bgcolor="white",
        yaxis=dict(showgrid=True, gridcolor="#e0e0e0", title=y_title,
                   tickformat=",", tickfont=dict(size=13, color="#444")),
        xaxis=dict(showgrid=False, tickfont=dict(size=15, color="#333")),
        legend=dict(orientation="h", x=1, xanchor="right", y=1.08, font=dict(size=14, color="#333")),
        bargap=0.35, bargroupgap=0.05,
    )
    st.plotly_chart(fig_ba, use_container_width=True)

    # summary row under chart
    dcols = st.columns(len(ov_rows))
    for col, row, yb, ya in zip(dcols, ov_rows, y_before, y_after):
        loc   = row["loc"]
        delta = ya - yb
        dclr  = "#27ae60" if delta>0 else "#e74c3c" if delta<0 else "#aaa"
        arr   = "▲" if delta>0 else "▼" if delta<0 else "─"
        col.markdown(f"""
        <div style="border-top:3px solid {_LOC_HEX[loc]};padding:10px 4px 4px 4px;text-align:center">
          <div style="font-size:.85rem;color:#666;margin-bottom:6px;font-weight:600">
            {_LOC_ICONS[loc]} {loc}</div>
          <div style="font-size:.92rem;color:#444">
            <span style="opacity:.6">{fmt(yb)}</span>
            <span style="color:#ddd;padding:0 5px">→</span>
            <span style="font-weight:700;color:#222">{fmt(ya)}</span>
          </div>
          <div style="font-size:.92rem;font-weight:700;color:{dclr};margin-top:4px">
            {arr} {fmt(abs(delta))}</div>
        </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Section 4: SKU Changes ────────────────────────────
    st.markdown("#### SKU-level Quantity Changes")

    kf1, kf2, _ = st.columns([1, 1, 2])
    seg_f = kf1.selectbox("Tier", ["ALL","TOP","MIDDLE","TAIL"],
                           key="ba_seg", label_visibility="collapsed")
    act_f = kf2.selectbox("Action", ["ALL","DC_TO_FC","FC_TO_DC","KEEP"],
                           key="ba_act", label_visibility="collapsed")

    show_ba = df_ideal.copy()
    if seg_f != "ALL": show_ba = show_ba[show_ba["seg"] == seg_f]
    if act_f != "ALL": show_ba = show_ba[show_ba["action"] == act_f]
    show_ba = show_ba[show_ba["action"] != "KEEP"]  # 默认只显示有动作的
    show_ba = show_ba.sort_values("priority", ascending=True)
    show_ba["fc_change"]   = show_ba["fc_ideal"]  - show_ba["fc_qty"]
    show_ba["sfc_change"]  = show_ba["sfc_ideal"] - show_ba["sfc_qty"]
    show_ba["item_number"] = show_ba["item_number"].astype(str)

    st.caption(f"{len(show_ba):,} SKUs with action (KEEP excluded) — sorted by priority")
    st.dataframe(
        show_ba[["item_number","seg","biz_type","action","reason",
                 "fc_d2s","avg_daily",
                 "fc_qty","fc_ideal","fc_change",
                 "sfc_qty","sfc_ideal","sfc_change",
                 "transfer_qty"]].rename(columns={
            "item_number": "SKU",    "seg": "Tier",   "biz_type": "Biz",
            "action":      "Action", "reason": "Reason",
            "fc_d2s":      "FC D2S", "avg_daily": "Avg Daily",
            "fc_qty":      "FC Before",  "fc_ideal":    "FC After",  "fc_change":   "FC Δ",
            "sfc_qty":     "SFC Before", "sfc_ideal":   "SFC After", "sfc_change":  "SFC Δ",
            "transfer_qty":"Transfer Qty",
        }),
        use_container_width=True, hide_index=True, height=420,
        column_config={
            "SKU":          st.column_config.TextColumn(width="medium"),
            "Tier":         st.column_config.TextColumn(width="small"),
            "Biz":          st.column_config.TextColumn(width="small"),
            "Action":       st.column_config.TextColumn(width="medium"),
            "Reason":       st.column_config.TextColumn(width="large"),
            "FC D2S":       st.column_config.NumberColumn(format="%.1f"),
            "Avg Daily":    st.column_config.NumberColumn(format="%.1f"),
            "FC Before":    st.column_config.NumberColumn(format="%d"),
            "FC After":     st.column_config.NumberColumn(format="%d"),
            "FC Δ":         st.column_config.NumberColumn(format="%+d"),
            "SFC Before":   st.column_config.NumberColumn(format="%d"),
            "SFC After":    st.column_config.NumberColumn(format="%d"),
            "SFC Δ":        st.column_config.NumberColumn(format="%+d"),
            "Transfer Qty": st.column_config.NumberColumn(format="%d"),
        }
    )
    st.download_button("⬇ Download", show_ba.to_csv(index=False),
                       "before_vs_after.csv", "text/csv")



# TAB 3 — FC → SFC Plan  (reads from SQLite cache built by refresh_sfc_cache.py)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_fc2sfc:
    from cache_db import CacheDB as _CacheDB2
    _db2     = _CacheDB2("results/sfc_cache.db")
    _df_mid  = _db2.read_fc2sfc_task("MIDDLE_EVICT")
    _df_tail = _db2.read_fc2sfc_task("TAIL_CLEAR")

    def _nskus2(tdf): return tdf["item_number"].nunique() if tdf is not None and not tdf.empty and "item_number" in tdf.columns else 0
    _c1, _c2 = st.columns(2)
    _c1.metric("🗑️ TAIL Clear", f"{_nskus2(_df_tail):,} SKUs")
    _c2.metric("📤 MIDDLE Evict", f"{_nskus2(_df_mid):,} SKUs")

    _fc2sfc_tail_tab, _fc2sfc_mid_tab = st.tabs([
        f"🗑️ TAIL Clear — FC→SFC  ({len(_df_tail):,} rows)",
        f"📤 MIDDLE Evict — FC→SFC  ({len(_df_mid):,} rows)",
    ])

    _FC_COLS = ["item_number","location_no","loc_type","qty_to_transfer","qty",
                "exp_date","fc_d2s","avg_daily_orders","excess_qty","rule"]

    def _render_fc2sfc(tab, tdf, key, caption_txt):
        with tab:
            if tdf is None or tdf.empty:
                st.info("No tasks. Run the refresh script or click Refresh All in the sidebar.")
                return
            cols = [c for c in _FC_COLS if c in tdf.columns]
            tdf = tdf[cols].copy()
            for c in ["qty_to_transfer","qty","fc_d2s","avg_daily_orders","excess_qty"]:
                if c in tdf.columns:
                    tdf[c] = pd.to_numeric(tdf[c], errors="coerce")

            # ── Filters + Rows/Page — single row ─────────────
            _fc1, _fc2, _fc3 = st.columns([1.5, 1, 0.7])
            with _fc1:
                _s = st.text_input("🔍 SKU", key=f"fc2_s_{key}", placeholder="Item number")
            with _fc2:
                _loctypes = ["All"] + sorted(tdf["loc_type"].dropna().unique().tolist()) if "loc_type" in tdf.columns else ["All"]
                _lf = st.selectbox("Loc Type", _loctypes, key=f"fc2_lf_{key}")
            with _fc3:
                st.selectbox("Rows/Page", options=[50, 100, 200, 500, "All"],
                             index=1, key=f"rowlimit_fc2_{key}", label_visibility="visible")

            if _s:
                tdf = tdf[tdf["item_number"].astype(str).str.contains(_s.strip(), case=False, na=False)]
            if _lf != "All" and "loc_type" in tdf.columns:
                tdf = tdf[tdf["loc_type"] == _lf]

            # Sort: location_no ASC
            if "location_no" in tdf.columns:
                tdf = tdf.sort_values("location_no", ascending=True, na_position="last")
            tdf = tdf.reset_index(drop=True); tdf.index = tdf.index + 1

            # ── Pagination state ──────────────────────────────
            total_rows = len(tdf)
            n_skus = tdf["item_number"].nunique() if "item_number" in tdf.columns else "?"
            caption = f"Total {total_rows:,} rows · {n_skus} SKUs · {caption_txt}"
            _preset = st.session_state.get(f"rowlimit_fc2_{key}", 100)
            _page   = st.session_state.get(f"fc2_page_{key}", 1)
            _per_page = total_rows if _preset == "All" else int(_preset)
            _total_pages = max(1, -(-total_rows // _per_page))
            _page = max(1, min(_page, _total_pages))
            st.session_state[f"fc2_page_{key}"] = _page
            tdf_show = tdf.iloc[(_page - 1) * _per_page : _page * _per_page]

            # ── Caption + Download ────────────────────────────
            _cap_col, _dl_col = st.columns([4, 1])
            with _cap_col:
                st.caption(caption)
            with _dl_col:
                st.download_button(
                    "⬇️ Download CSV", tdf_show.to_csv(index=False).encode(),
                    file_name=f"fc_to_sfc_{key.lower()}.csv", mime="text/csv",
                    key=f"dl_fc2_{key}", use_container_width=True,
                )

            st.dataframe(tdf_show, use_container_width=True)

            # ── CSS + page nav ────────────────────────────────
            st.markdown("""<style>
            div[data-testid="stTextInput"] > div > div > input { max-width: 32px !important; }
            </style>""", unsafe_allow_html=True)

            if _total_pages > 1:
                _nav_left, _nav_right = st.columns([5.0, 0.8])
                with _nav_right:
                    _n1, _n2, _n3, _n4, _n5 = st.columns([0.2, 0.2, 0.2, 0.2, 0.2])
                    with _n1:
                        if st.button("‹", key=f"fc2_prev_{key}", disabled=_page == 1):
                            st.session_state[f"fc2_page_{key}"] = _page - 1; st.rerun()
                    with _n2:
                        st.markdown('<p style="font-size:1rem;color:#555;margin:0;padding-top:8px;white-space:nowrap">Page</p>', unsafe_allow_html=True)
                    with _n3:
                        _go = st.text_input("pg", value=str(_page), key=f"fc2_page_input_{key}", label_visibility="collapsed")
                        try:
                            _go_int = int(_go)
                            if 1 <= _go_int <= _total_pages and _go_int != _page:
                                st.session_state[f"fc2_page_{key}"] = _go_int; st.rerun()
                        except ValueError:
                            pass
                    with _n4:
                        st.markdown(f'<p style="font-size:1rem;color:#555;margin:0;padding-top:8px;white-space:nowrap">/ {_total_pages}</p>', unsafe_allow_html=True)
                    with _n5:
                        if st.button("›", key=f"fc2_next_{key}", disabled=_page == _total_pages):
                            st.session_state[f"fc2_page_{key}"] = _page + 1; st.rerun()

    _render_fc2sfc(_fc2sfc_tail_tab, _df_tail, "tail_clear",
                   "TAIL SKUs with FC stock. Full clear — move everything from FC to SFC.")
    _render_fc2sfc(_fc2sfc_mid_tab,  _df_mid,  "middle_evict",
                   "SKUs where FC has too much stock (D2S > 30). Transfer excess back to SFC, keep 30-day supply in FC.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — SFC → FC Plan
# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — SFC → FC Plan  (reads from SQLite cache built by refresh_sfc_cache.py)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_sfc2fc:
    from cache_db import CacheDB as _CacheDB
    from logic.sfc_to_fc import build_sfc_to_fc_lists as _build_sfc2fc

    _DB_PATH  = "results/sfc_cache.db"
    _db       = _CacheDB(_DB_PATH)

    # ── Sub-tabs ──────────────────────────────────────────────────────────────
    _t2 = _db.read_task_list("T2")
    _t3 = _db.read_task_list("T3")
    _t4 = _db.read_task_list("T4")

    def _n(tdf): return len(tdf) if tdf is not None and not tdf.empty else 0
    def _nskus(tdf): return tdf["item_number"].nunique() if tdf is not None and not tdf.empty and "item_number" in tdf.columns else 0

    # T1 uses 7d cached list
    _t1_cached = _db.read_task_list("T1_7d")
    _n_t1 = _n(_t1_cached) if not (_t1_cached is None or _t1_cached.empty) else _n(_db.read_task_list("T1"))

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("🚨 T1 — Emergency", f"{_nskus(_t1_cached):,} SKUs")
    h2.metric("📦 T2 — Replenishment", f"{_nskus(_t2):,} SKUs")
    h3.metric("🔄 T3 — Bin→Stock", f"{_nskus(_t3):,} SKUs")
    h4.metric("📅 T4 — Date Switch", f"{_nskus(_t4):,} SKUs")

    _sfc_t1, _sfc_t2, _sfc_t3, _sfc_t4 = st.tabs([
        f"🚨 T1 — Emergency Transfer SFC→FC  ({_n_t1:,} rows)",
        f"📦 T2 — Off-Peak Replenishment  ({_n(_t2):,} rows)",
        f"🔄 T3 — SFC Bin → SFC Stock  ({_n(_t3):,} rows)",
        f"📅 T4 — Cross-Zone Date Switch  ({_n(_t4):,} rows)",
    ])

    _DCOLS = [
        "segment","item_number","location_no","loc_type","qty_to_transfer",
        "exp_date","case_qty","fc_d2s","avg_daily_orders","source_type",
    ]
    _RENAME = {"case_qty": "case_spec"}

    def _render_sfc(tab, tdf, key, label, extra=""):
        with tab:
            if tdf is None or tdf.empty:
                st.info(f"No {label} tasks.")
                return
            cols = [c for c in _DCOLS if c in tdf.columns]
            tdf = tdf[cols].copy()
            for c in ["qty_to_transfer","case_qty","fc_d2s","avg_daily_orders"]:
                if c in tdf.columns:
                    tdf[c] = pd.to_numeric(tdf[c], errors="coerce")

            # ── Filters + Rows/Page — single row ─────────────
            _fc1, _fc2, _fc3, _fc4 = st.columns([1.2, 1, 1, 0.7])
            with _fc1:
                _s = st.text_input("🔍 SKU", key=f"s_{key}", placeholder="Item number")
            with _fc2:
                _segs = ["All"] + sorted(tdf["segment"].dropna().unique().tolist()) if "segment" in tdf.columns else ["All"]
                _sf = st.selectbox("Segment", _segs, key=f"sf_{key}")
            with _fc3:
                _loctypes = ["All"] + sorted(tdf["loc_type"].dropna().unique().tolist()) if "loc_type" in tdf.columns else ["All"]
                _lf = st.selectbox("Loc Type", _loctypes, key=f"lf_{key}")
            with _fc4:
                st.selectbox("Rows/Page", options=[50, 100, 200, 500, "All"],
                             index=1, key=f"rowlimit_{key}", label_visibility="visible")

            if _s:
                tdf = tdf[tdf["item_number"].astype(str).str.contains(_s.strip(), case=False, na=False)]
            if _sf != "All" and "segment" in tdf.columns:
                tdf = tdf[tdf["segment"] == _sf]
            if _lf != "All" and "loc_type" in tdf.columns:
                tdf = tdf[tdf["loc_type"] == _lf]

            # ── Sort ──────────────────────────────────────────
            if "segment" in tdf.columns and "location_no" in tdf.columns:
                tdf["_seg_order"] = tdf["segment"].map({"TOP": 0, "MIDDLE": 1}).fillna(2)
                tdf = tdf.sort_values(["_seg_order", "location_no"], ascending=[True, True], na_position="last")
                tdf = tdf.drop(columns=["_seg_order"])
            elif "location_no" in tdf.columns:
                tdf = tdf.sort_values("location_no", ascending=True, na_position="last")

            tdf = tdf.reset_index(drop=True); tdf.index = tdf.index + 1
            tdf = tdf.rename(columns=_RENAME)

            # ── Pagination state ──────────────────────────────
            total_rows = len(tdf)
            n_skus = tdf["item_number"].nunique() if "item_number" in tdf.columns else "?"
            caption = f"Total {total_rows:,} rows · {n_skus} SKUs"
            if extra:
                caption += f" · {extra}"
            _preset = st.session_state.get(f"rowlimit_{key}", 100)
            _page   = st.session_state.get(f"page_{key}", 1)
            _per_page = total_rows if _preset == "All" else int(_preset)
            _total_pages = max(1, -(-total_rows // _per_page))
            _page = max(1, min(_page, _total_pages))
            st.session_state[f"page_{key}"] = _page
            tdf_show = tdf.iloc[(_page - 1) * _per_page : _page * _per_page]

            # ── Caption + Download ────────────────────────────
            _cap_col, _dl_col = st.columns([4, 1])
            with _cap_col:
                st.caption(caption)
            with _dl_col:
                st.download_button(
                    "⬇️ Download CSV", tdf_show.to_csv(index=False).encode(),
                    file_name=f"sfc_to_fc_{key.lower()}.csv", mime="text/csv",
                    key=f"dl_main_{key}", use_container_width=True,
                )

            st.dataframe(tdf_show, use_container_width=True)

            # ── CSS + page nav ────────────────────────────────
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

    # ── T1 tab: slider + cache ────────────────────────────────────────────────
    with _sfc_t1:
        with st.expander("⚙️ T1 Transfer Limit", expanded=False):
            st.caption("Transfer enough SFC to bring FC up to this many days of stock.")
            _c1, _c2 = st.columns(2)
            with _c1:
                st.slider("TOP — Target D2S", 1, 30,
                          st.session_state.get("main_t1_top", 7), key="main_t1_top")
            with _c2:
                st.slider("MIDDLE — Target D2S", 1, 30,
                          st.session_state.get("main_t1_mid", 7), key="main_t1_mid")

        _t1_top = st.session_state.get("main_t1_top", 7)
        _t1_mid = st.session_state.get("main_t1_mid", 7)

        if _t1_top == 7 and _t1_mid == 7:
            _tdf_t1 = _db.read_task_list("T1_7d")
            if _tdf_t1 is None or _tdf_t1.empty:
                # fallback: build live
                _cfg1 = dict(P); _cfg1["t1_top_target_d2s"]=7; _cfg1["t1_mid_target_d2s"]=7
                _df_r = df[df["item_number"].isin(df[df["sfc_qty"]>0]["item_number"])].copy()
                _df_fc_exp = _db.read_fc_exp()
                if not _df_fc_exp.empty:
                    _df_r = _df_r.merge(_df_fc_exp[["item_number","fc_min_exp_date","fc_max_exp_date"]], on="item_number", how="left")
                _df_lots = _db.read_sfc_lots()
                _tdf_t1 = _build_sfc2fc(_df_lots, _df_r, _cfg1, target_lists=["T1"]).get("T1", pd.DataFrame())
            _render_sfc(_sfc_t1, _tdf_t1, "T1", "T1", extra="TOP=7d · MID=7d (cached)")
        else:
            _cfg1 = dict(P); _cfg1["t1_top_target_d2s"]=_t1_top; _cfg1["t1_mid_target_d2s"]=_t1_mid
            _df_r = df[df["item_number"].isin(df[df["sfc_qty"]>0]["item_number"])].copy()
            _df_fc_exp = _db.read_fc_exp()
            if not _df_fc_exp.empty:
                _df_r = _df_r.merge(_df_fc_exp[["item_number","fc_min_exp_date","fc_max_exp_date"]], on="item_number", how="left")
            _df_lots = _db.read_sfc_lots()
            _tdf_t1 = _build_sfc2fc(_df_lots, _df_r, _cfg1, target_lists=["T1"]).get("T1", pd.DataFrame())
            _render_sfc(_sfc_t1, _tdf_t1, "T1", "T1", extra=f"TOP={_t1_top}d · MID={_t1_mid}d (live)")

    _render_sfc(_sfc_t2, _t2, "T2", "T2")
    _render_sfc(_sfc_t3, _t3, "T3", "T3")
    _render_sfc(_sfc_t4, _t4, "T4", "T4")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Putaway  (live RECG pending putaway data)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_putaway:
    from data.loader import load_pending_putaway
    from cache_db import CacheDB as _CacheDB_pa
    _db_pa = _CacheDB_pa("results/sfc_cache.db")

    st.markdown("### Putaway Decision Table")
    st.caption("Items currently in RECG receiving staging (location_type=1) — received but not yet put away.")

    # ── Load pending putaway from SQLite cache (refreshed every 5 min by cron) ─
    df_pending = _db_pa.read_pending_putaway()
    _pa_age = _db_pa.age_minutes("pending_putaway")

    if _pa_age > 9000:
        st.info("⏳ Putaway data not yet loaded. Cron will populate within 30 min. Use sidebar to refresh manually.")
        st.stop()

    if _pa_age > 35:
        st.warning(f"⚠️ Putaway cache is {_pa_age:.0f} min old. Cron will refresh within 30 min.")

    if df_pending.empty:
        st.info("✅ No items currently pending putaway in RECG staging locations.")
    else:
        # ── Build inventory reference with fc_min_exp_date ────────────────────
        _fc_exp_pa = _db_pa.read_fc_exp()

        df_inv_ref_pa = df[["item_number","sfc_qty","fc_d2s"]].drop_duplicates("item_number").copy()
        if not _fc_exp_pa.empty:
            df_inv_ref_pa = df_inv_ref_pa.merge(
                _fc_exp_pa[["item_number","fc_min_exp_date","fc_max_exp_date"]],
                on="item_number", how="left"
            )
        else:
            df_inv_ref_pa["fc_min_exp_date"] = None
            df_inv_ref_pa["fc_max_exp_date"] = None

        # ── Merge classification + inventory into pending ─────────────────────
        df_pa = df_pending.copy()
        df_pa["item_number"] = df_pa["item_number"].astype(str)

        df_pa = df_pa.merge(
            df[["item_number","seg","avg_daily_orders"]].drop_duplicates("item_number"),
            on="item_number", how="left"
        )
        # need_minimal_exp from SQLite exp_flags cache
        _exp_flags_pa = _db_pa.read_exp_flags()  # returns set of item_numbers
        df_pa["need_minimal_exp"] = df_pa["item_number"].isin(_exp_flags_pa).astype(int)
        df_pa = df_pa.merge(
            df_inv_ref_pa[["item_number","sfc_qty","fc_d2s","fc_min_exp_date"]],
            on="item_number", how="left"
        )

        # Get new_exp_date from wh_inbound_batch via RECG lots (use last_update as proxy for now)
        # new_exp_date not available from wh_inventory_transaction — set None, triggers D2S path
        df_pa["new_exp_date"] = None
        df_pa["sfc_qty"] = df_pa["sfc_qty"].fillna(0)
        df_pa["fc_d2s"]  = df_pa["fc_d2s"].fillna(0)
        df_pa["need_minimal_exp"] = df_pa["need_minimal_exp"].fillna(0).astype(int)
        df_pa["seg"] = df_pa["seg"].fillna("MIDDLE")  # default MIDDLE if unknown

        # ── Apply putaway decision ─────────────────────────────────────────────
        _decisions = df_pa.apply(lambda r: decide_putaway(r, dict(P)), axis=1, result_type="expand")
        df_pa = pd.concat([df_pa, _decisions], axis=1)

        # ── Summary metrics ───────────────────────────────────────────────────
        _n_fc  = (df_pa["putaway_zone"] == "FC").sum()
        _n_sfc = (df_pa["putaway_zone"] == "SFC").sum()
        _m1, _m2, _m3 = st.columns(3)
        _m1.metric("📦 Pending Putaway", f"{len(df_pa):,} SKUs")
        _m2.metric("→ FC",  f"{_n_fc:,}")
        _m3.metric("→ SFC", f"{_n_sfc:,}")

        # ── Display ───────────────────────────────────────────────────────────
        _show = [c for c in [
            "item_number","po_reference","location_no","waiting_putaway_qty","seg",
            "fc_d2s","sfc_qty","putaway_zone","putaway_rule","putaway_reason"
        ] if c in df_pa.columns]

        _fc1, _fc2, _fc3 = st.columns([2, 1, 1])
        with _fc1:
            _pa_search = st.text_input("🔍 Item Number", key="pa_search", placeholder="e.g. 100123")
        with _fc2:
            _pa_zone = st.selectbox("Zone", ["All", "FC", "SFC"], key="pa_zone")
        with _fc3:
            _pa_seg = st.selectbox("Segment", ["All"] + sorted(df_pa["seg"].dropna().unique().tolist()), key="pa_seg")

        df_pa_show = df_pa[_show].copy()
        if _pa_search:
            df_pa_show = df_pa_show[df_pa_show["item_number"].astype(str).str.contains(_pa_search.strip(), case=False, na=False)]
        if _pa_zone != "All":
            df_pa_show = df_pa_show[df_pa_show["putaway_zone"] == _pa_zone]
        if _pa_seg != "All":
            df_pa_show = df_pa_show[df_pa_show["seg"] == _pa_seg]

        for c in ["waiting_putaway_qty","fc_d2s","sfc_qty"]:
            if c in df_pa_show.columns:
                df_pa_show[c] = pd.to_numeric(df_pa_show[c], errors="coerce")

        df_pa_show = df_pa_show.sort_values("putaway_zone", ascending=True).reset_index(drop=True)
        df_pa_show.index = df_pa_show.index + 1

        st.dataframe(
            df_pa_show,
            use_container_width=True,
            column_config={
                "po_reference":        st.column_config.TextColumn("PO Ref",       width=120),
                "item_number":         st.column_config.TextColumn("Item",         width=110),
                "location_no":         st.column_config.TextColumn("Location",     width=90),
                "waiting_putaway_qty": st.column_config.NumberColumn("Qty",        width=60),
                "seg":                 st.column_config.TextColumn("Seg",          width=70),
                "fc_d2s":              st.column_config.NumberColumn("FC D2S",     width=75, format="%.1f"),
                "sfc_qty":             st.column_config.NumberColumn("SFC Qty",    width=75),
                "putaway_zone":        st.column_config.TextColumn("Zone",         width=60),
                "putaway_rule":        st.column_config.TextColumn("Rule",         width=140),
                "putaway_reason":      st.column_config.TextColumn("Reason",       width=220),
            },
        )
        st.download_button(
            "⬇ Download Putaway CSV",
            df_pa_show.to_csv(index=False).encode(),
            file_name="putaway_decisions.csv",
            mime="text/csv",
            key="dl_putaway",
        )
