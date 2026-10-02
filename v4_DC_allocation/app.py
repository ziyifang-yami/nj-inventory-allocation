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
)
from logic.putaway import decide_putaway, run_putaway
from config import P


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NJ Inventory Allocation v4",
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
    "TOP":    "#5B8DEF",
    "MIDDLE": "#F0A500",
    "TAIL":   "#8C8C8C",
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
    动态计算理想状态 —— 基于当前 sidebar 阈值实时运算，不依赖静态 CSV。
    TOP  : SFC > 0 → full SFC→FC transfer
    TAIL : FC > 0 → FC_TO_DC 全清
    MIDDLE: DC→FC (SFC Return) or FC→DC (Move Out)
    """
    import numpy as np

    rows = []
    for _, r in df_in.iterrows():
        fc    = float(r["fc_qty"])
        sfc   = float(r["sfc_qty"])
        adl   = float(r.get("avg_daily_orders", 0) or 0)
        vol   = float(r.get("unit_volume_cbi", 0) or float(r.get("unit_volume_cbm", 0) or 0))
        seg   = str(r["seg"])

        fc_d2s     = (fc / adl) if adl > 0 else float("nan")
        target_qty = round(adl * mid_target, 0) if adl > 0 else 0.0

        action       = "KEEP"
        transfer_qty = 0.0
        fc_ideal     = fc
        sfc_ideal    = sfc
        priority     = 999.0
        reason       = ""

        if seg == "TOP":
            if sfc > 0:
                xfer         = sfc
                action       = "DC_TO_FC"
                transfer_qty = round(xfer, 0)
                fc_ideal     = fc + xfer
                sfc_ideal    = 0.0
                d2s_val      = fc_d2s if not pd.isna(fc_d2s) else 0.0
                reason       = f"TOP full SFC→FC transfer (FC D2S={d2s_val:.1f})"

        elif seg == "MIDDLE":
            if adl > 0:
                d2s = fc_d2s if not pd.isna(fc_d2s) else 0.0
                if d2s <= mid_routine and sfc > 0:
                    needed = max(0.0, target_qty - fc)
                    if needed > 0:
                        xfer         = min(needed, sfc)
                        action       = "DC_TO_FC"
                        transfer_qty = round(xfer, 0)
                        fc_ideal     = fc + xfer
                        sfc_ideal    = sfc - xfer
                        batch_vol    = xfer * vol
                        emerg        = d2s <= mid_emergency
                        reason       = f"FC D2S={d2s:.1f} {'[EMERGENCY]' if emerg else '[ROUTINE]'}"
                        priority     = d2s * 1e6 - batch_vol * 1e3 - adl
                elif d2s > mid_evict:
                    keep_qty = round(adl * mid_evict, 0)
                    excess   = fc - keep_qty
                    if excess > min_loc_qty:
                        excess_vol   = excess * vol
                        action       = "FC_TO_DC"
                        transfer_qty = round(excess, 0)
                        fc_ideal     = fc - excess
                        sfc_ideal    = sfc + excess
                        reason       = f"FC D2S={d2s:.1f} > {mid_evict}, excess={excess:.0f}"
                        priority     = -excess_vol * 1e3 - excess

        elif seg == "TAIL":
            if fc > 0:
                action       = "FC_TO_DC"
                transfer_qty = fc
                fc_ideal     = 0.0
                sfc_ideal    = sfc + fc
                reason       = "TAIL: clear FC"
                priority     = 100.0

        rows.append({
            "item_number":   str(r["item_number"]),
            "seg":           seg,
            "biz_type":      str(r.get("biz_type", "")),
            "fc_qty":        fc,
            "sfc_qty":       sfc,
            "fc_d2s":        round(fc_d2s, 1) if not pd.isna(fc_d2s) else None,
            "avg_daily":     round(adl, 2),
            "unit_vol":      round(vol, 4),
            "action":        action,
            "transfer_qty":  transfer_qty,
            "fc_ideal":      fc_ideal,
            "sfc_ideal":     sfc_ideal,
            "fc_vol_now":    round(fc      * vol, 0),
            "sfc_vol_now":   round(sfc     * vol, 0),
            "fc_vol_ideal":  round(fc_ideal  * vol, 0),
            "sfc_vol_ideal": round(sfc_ideal * vol, 0),
            "priority":      round(priority, 3),
            "reason":        reason,
        })
    return pd.DataFrame(rows)


# ── Session state initialisation ─────────────────────────────────────────────
if "aod_top_val" not in st.session_state:
    st.session_state.aod_top_val = P["aod_top"]
if "ol_top_val"  not in st.session_state:
    st.session_state.ol_top_val  = P["ol_top"]
if "ol_tail_val" not in st.session_state:
    st.session_state.ol_tail_val = P["ol_tail"]


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🎛 Tier Thresholds")
    if st.button("🔄 Refresh Data", key="refresh_btn", help="Re-query Athena and reload inventory"):
        _do_refresh()
        st.rerun()
    st.markdown("---")
    # ── Session state init ──────────────────────────────────
    if "aod_top_val" not in st.session_state: st.session_state.aod_top_val = 21
    if "ol_top_val"  not in st.session_state: st.session_state.ol_top_val  = 100
    if "ol_tail_val" not in st.session_state: st.session_state.ol_tail_val = 4

    def sync_from_slider(val_key, sld_key):
        st.session_state[val_key] = st.session_state[sld_key]
    def sync_from_num(val_key, num_key):
        st.session_state[val_key] = st.session_state[num_key]

    # ── AOD ─────────────────────────────────────────────────
    st.markdown("**TOP** — FC Primary")
    st.slider("Active Order Days ≥", 1, 30, key="aod_top_sld",
              value=st.session_state.aod_top_val,
              on_change=sync_from_slider, args=("aod_top_val", "aod_top_sld"))
    st.number_input("", 1, 30, key="aod_top_num",
                    value=st.session_state.aod_top_val, step=1,
                    label_visibility="collapsed",
                    on_change=sync_from_num, args=("aod_top_val", "aod_top_num"))
    aod_top = st.session_state.aod_top_val

    # ── OL TOP ───────────────────────────────────────────────
    st.slider("30D Order Lines ≥", 1, 300, key="ol_top_sld",
              value=st.session_state.ol_top_val, step=1,
              on_change=sync_from_slider, args=("ol_top_val", "ol_top_sld"))
    st.number_input(" ", 1, 500, key="ol_top_num",
                    value=st.session_state.ol_top_val, step=1,
                    label_visibility="collapsed",
                    on_change=sync_from_num, args=("ol_top_val", "ol_top_num"))
    ol_top = st.session_state.ol_top_val

    st.markdown("---")
    st.markdown("**TAIL** — DC Primary")

    # ── OL TAIL ──────────────────────────────────────────────
    st.slider("30D Order Lines <", 1, 50, key="ol_tail_sld",
              value=st.session_state.ol_tail_val,
              on_change=sync_from_slider, args=("ol_tail_val", "ol_tail_sld"))
    st.number_input("  ", 1, 50, key="ol_tail_num",
                    value=st.session_state.ol_tail_val, step=1,
                    label_visibility="collapsed",
                    on_change=sync_from_num, args=("ol_tail_val", "ol_tail_num"))
    ol_tail = st.session_state.ol_tail_val

    st.markdown("---")
    st.markdown("**Targets**")
    t_sku   = st.number_input("Target SKU %",   value=10.2, step=0.1, format="%.1f")
    t_ol    = st.number_input("Target OL %",    value=70.2, step=0.1, format="%.1f")
    t_units = st.number_input("Target Units %", value=77.7, step=0.1, format="%.1f")
    t_cbi   = st.number_input("Target CBI %",   value=80.4, step=0.1, format="%.1f")

    TARGETS = {"SKU %": t_sku, "OL %": t_ol, "Units %": t_units, "CBI %": t_cbi}

    st.markdown("---")
    st.caption(f"Rolling 30 inv-days · wh=002 · 1P+FBY · {DATA_TS}")


# ── Header ────────────────────────────────────────────────────────────────────
_inv_badge = "🟢 Live" if INV_TS == "live" else f"🟡 {INV_TS}"
st.markdown(
    f"<span style='color:#999;font-size:.9rem'>"
    f"In-stock SKUs: {len(df):,} &nbsp;|&nbsp; "
    f"Sales: {DATA_TS} &nbsp;|&nbsp; "
    f"Inventory: {_inv_badge}"
    f"</span>",
    unsafe_allow_html=True,
)
col_head, col_refresh = st.columns([8, 1])
with col_refresh:
    if st.button("🔄 Refresh", help="Re-query Athena and reload inventory"):
        _do_refresh()
        st.rerun()


# ── Classify ─────────────────────────────────────────────────
def classify(r):
    if r.active_order_days >= aod_top and r.order_lines_30d >= ol_top:
        return "TOP"
    if r.order_lines_30d < ol_tail:
        return "TAIL"
    return "MIDDLE"

df["seg"]      = df.apply(classify, axis=1)
df["biz_type"] = df["item_number"].astype(str).str[0].map({"1": "自营", "5": "FBY"}).fillna("Other")

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

# ── Header ────────────────────────────────────────────────────
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

    # ── Tier Cards ────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)

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

    card(c1, "TOP — FC Primary",   T,  COLORS["TOP"])
    card(c2, "MIDDLE — Dual WH",   M,  COLORS["MIDDLE"])
    card(c3, "TAIL — DC Primary",  TL, COLORS["TAIL"])

    st.divider()

    # ── Two column layout ─────────────────────────────────────────
    left, right = st.columns([1, 1], gap="large")

# LEFT: Donut charts
    with left:
        st.markdown("#### SKU & OL Distribution")

        seg_df = pd.DataFrame([
            {"Tier": "TOP",    "SKUs": T["n"],  "OL": T["pol"]},
            {"Tier": "MIDDLE", "SKUs": M["n"],  "OL": M["pol"]},
            {"Tier": "TAIL",   "SKUs": TL["n"], "OL": TL["pol"]},
        ])

        fc1, fc2 = st.columns(2)
        with fc1:
            fig_s = go.Figure(go.Pie(
                labels=seg_df["Tier"], values=seg_df["SKUs"],
                hole=.55, marker_colors=[COLORS[t] for t in seg_df["Tier"]],
                textinfo="percent", showlegend=False,
            ))
            fig_s.update_layout(margin=dict(t=30,b=0,l=0,r=0), height=200,
                                title=dict(text="SKUs", x=.5, font_size=13))
            st.plotly_chart(fig_s, use_container_width=True)

        with fc2:
            fig_o = go.Figure(go.Pie(
                labels=seg_df["Tier"],
                values=[T["pol"], M["pol"], TL["pol"]],
                hole=.55, marker_colors=[COLORS[t] for t in seg_df["Tier"]],
                textinfo="percent", showlegend=False,
            ))
            fig_o.update_layout(margin=dict(t=30,b=0,l=0,r=0), height=200,
                                title=dict(text="Order Lines", x=.5, font_size=13))
            st.plotly_chart(fig_o, use_container_width=True)

        # Legend
        st.markdown(
            " &nbsp; ".join([
                f"<span style='color:{COLORS[t]};font-size:.85rem'>■ {t}</span>"
                for t in ["TOP","MIDDLE","TAIL"]
            ]),
            unsafe_allow_html=True
        )

    # RIGHT: Target comparison
    with right:
        st.markdown("#### TOP Tier vs Target")

        actuals = {
            "SKU %":   T["psku"],
            "OL %":    T["pol"],
            "Units %": T["pu"],
            "CBI %":   T["pc"],
        }

        for metric, target in TARGETS.items():
            actual = actuals[metric]
            pct    = min(actual / target, 1.0)
            gap    = actual - target
            color  = "#5B8DEF" if gap >= 0 else "#EF5B5B"
            gap_bg = "#e8f0fe" if gap >= 0 else "#fde8e8"
            gap_fg = "#1a56db" if gap >= 0 else "#c81e1e"
            bar_w  = f"{pct*100:.0f}%"

            st.markdown(f"""
            <div class="target-row">
              <div class="target-label">{metric}</div>
              <div style="flex:1">
                <div class="bar-wrap">
                  <div class="bar-fill" style="width:{bar_w};background:{color}"></div>
                </div>
                <div style="font-size:.7rem;color:#999;margin-top:2px">
                  {actual:.1f}% &nbsp;/&nbsp; target {target:.1f}%
                </div>
              </div>
              <span class="gap-badge" style="background:{gap_bg};color:{gap_fg}">
                {gap:+.1f}%
              </span>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # FC vs SFC stacked bar
        inv_df = pd.DataFrame([
            {"Tier": "TOP",    "FC": T["fc"],   "SFC": T["sfc"]},
            {"Tier": "MIDDLE", "FC": M["fc"],   "SFC": M["sfc"]},
            {"Tier": "TAIL",   "FC": TL["fc"],  "SFC": TL["sfc"]},
        ])
        fig_inv = go.Figure()
        fig_inv.add_trace(go.Bar(name="FC",  x=inv_df["Tier"], y=inv_df["FC"],
                                 marker_color=[COLORS[t] for t in inv_df["Tier"]],
                                 opacity=.9))
        fig_inv.add_trace(go.Bar(name="SFC", x=inv_df["Tier"], y=inv_df["SFC"],
                                 marker_color=[COLORS[t] for t in inv_df["Tier"]],
                                 opacity=.45))
        fig_inv.update_layout(
            barmode="stack", height=220, margin=dict(t=30,b=0,l=0,r=0),
            title=dict(text="FC vs SFC Inventory", x=0, font_size=13),
            legend=dict(orientation="h", x=1, xanchor="right", y=1.1),
            yaxis=dict(tickformat=","), plot_bgcolor="white",
            xaxis=dict(showgrid=False), yaxis_gridcolor="#f0f0f0",
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



# TAB 3 — FC → SFC Plan
# ═══════════════════════════════════════════════════════════════════════════════
with tab_fc2sfc:
    st.markdown("### FC → SFC Transfer Plan")

    cfg = dict(P)  # use defaults; Threshold Simulator doesn't change operational params

    df_triggered = compute_fc_to_sfc(df, cfg)

    if df_triggered.empty:
        df_mid_trigger = pd.DataFrame()
        df_tail_trigger = pd.DataFrame()
    else:
        df_mid_trigger  = df_triggered[df_triggered["trigger"] == "MIDDLE_EVICT"].copy() \
            if "trigger" in df_triggered.columns else pd.DataFrame()
        df_tail_trigger = df_triggered[df_triggered["trigger"] == "TAIL_CLEAR"].copy() \
            if "trigger" in df_triggered.columns else pd.DataFrame()

    fc2sfc_mid_tab, fc2sfc_tail_tab = st.tabs(["MIDDLE Evict List", "TAIL Clear List"])

    with fc2sfc_mid_tab:
        st.markdown("#### MIDDLE FC→SFC — Evict List")
        st.caption("SKUs with FC D2S > 30. Sorted by excess_vol DESC.")
        if df_mid_trigger.empty:
            st.info("No MIDDLE FC→SFC tasks at current thresholds.")
        else:
            # Build location-level details using lot data
            mid_skus = df_mid_trigger["item_number"].tolist()
            df_fc_lots, lot_err = load_fc_lot_data(mid_skus)

            if lot_err:
                st.warning(f"FC lot data unavailable: {lot_err}. Showing SKU-level summary.")
                df_mid_trigger.get("item_number")
                _show_cols = [c for c in ["item_number", "excess_qty", "keep_qty",
                                          "excess_vol", "avg_daily_orders", "fc_d2s"]
                              if c in df_mid_trigger.columns]
                st.dataframe(df_mid_trigger[_show_cols], use_container_width=True)
            else:
                loc_rows = []
                for _, sku_row in df_mid_trigger.iterrows():
                    locs = select_middle_fc_locations(df_fc_lots, sku_row)
                    if not locs.empty:
                        loc_rows.append(locs)

                if loc_rows:
                    df_mid_locs = pd.concat(loc_rows, ignore_index=True)
                    _show = [c for c in ["item_number", "location_no", "loc_type",
                                         "qty_to_transfer", "qty", "exp_date", "rule"]
                             if c in df_mid_locs.columns]
                    st.dataframe(df_mid_locs[_show], use_container_width=True)
                    st.download_button(
                        "⬇ Download MIDDLE Evict CSV",
                        df_mid_locs[_show].to_csv(index=False).encode(),
                        file_name="fc_to_sfc_middle_evict.csv",
                        mime="text/csv",
                    )
                else:
                    st.info("No individual locations to transfer (all triggered SKUs have single-location FC stock).")

    with fc2sfc_tail_tab:
        st.markdown("#### TAIL FC→SFC — Clear List")
        st.caption("All TAIL SKUs with FC stock. Full clear, sorted by fc_qty DESC.")
        if df_tail_trigger.empty:
            st.info("No TAIL FC→SFC tasks — no TAIL SKUs with FC stock.")
        else:
            tail_skus = df_tail_trigger["item_number"].tolist()
            df_fc_lots_tail, lot_err_tail = load_fc_lot_data(tail_skus)

            if lot_err_tail:
                st.warning(f"FC lot data unavailable: {lot_err_tail}. Showing SKU-level summary.")
                _show_cols = [c for c in ["item_number", "fc_qty", "avg_daily_orders"]
                              if c in df_tail_trigger.columns]
                st.dataframe(df_tail_trigger[_show_cols], use_container_width=True)
            else:
                tail_loc_rows = []
                for sku in tail_skus:
                    locs = select_tail_fc_locations(df_fc_lots_tail, sku)
                    if not locs.empty:
                        tail_loc_rows.append(locs)

                if tail_loc_rows:
                    df_tail_locs = pd.concat(tail_loc_rows, ignore_index=True)
                    _show = [c for c in ["item_number", "location_no", "loc_type",
                                         "qty_to_transfer", "qty", "exp_date", "rule"]
                             if c in df_tail_locs.columns]
                    st.dataframe(df_tail_locs[_show], use_container_width=True)
                    st.download_button(
                        "⬇ Download TAIL Clear CSV",
                        df_tail_locs[_show].to_csv(index=False).encode(),
                        file_name="fc_to_sfc_tail_clear.csv",
                        mime="text/csv",
                    )
                else:
                    st.info("No individual locations found for TAIL SKUs.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — SFC → FC Plan  (Req 14.1, 14.2, 14.3, 14.4)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_sfc2fc:
    st.markdown("### SFC → FC Transfer Plan")

    cfg = dict(P)  # operational params from config.py

    df_top    = df[df["seg"] == "TOP"].copy()
    df_middle = df[df["seg"] == "MIDDLE"].copy()

    # Load SFC lot data + FC exp summary for EXP routing
    all_skus = df["item_number"].tolist()
    df_sfc_lots, sfc_err = load_sfc_lot_data(all_skus)
    df_fc_exp = load_fc_exp_summary(tuple(all_skus))

    # Merge fc_min_exp_date into df_skus
    df_for_routing = df.merge(
        df_fc_exp[["item_number", "fc_min_exp_date", "fc_max_exp_date"]],
        on="item_number", how="left"
    )

    if sfc_err:
        st.warning(f"SFC lot data unavailable: {sfc_err}")
        df_sfc_lots = pd.DataFrame()

    # Build T1/T2/T3/T4 task lists
    task_lists = build_sfc_to_fc_lists(
        df_sfc_lots if df_sfc_lots is not None and not df_sfc_lots.empty else pd.DataFrame(),
        df_for_routing,
        cfg,
    )

    # ── Dashboard summary cards ───────────────────────────────────────────────
    _T_META = {
        "T1": {"label": "T1 紧急补货",   "color": "#e74c3c", "icon": "🚨",
               "desc": "fc_d2s < 3  /  Zone Fallback"},
        "T2": {"label": "T2 常规补货",   "color": "#5B8DEF", "icon": "📦",
               "desc": "TOP routine  /  MIDDLE fc_d2s < 14"},
        "T3": {"label": "T3 Bin→Stock",  "color": "#F0A500", "icon": "🔄",
               "desc": "SFC Bin → SFC Stock 移位"},
        "T4": {"label": "T4 DateSwitch", "color": "#8C8C8C", "icon": "📅",
               "desc": "SFC Stock → FC  (保质期更差)"},
    }

    # Compute per-list metrics
    def _list_metrics(tl: pd.DataFrame):
        if tl is None or tl.empty:
            return {"skus": 0, "locs": 0, "qty": 0, "rows": 0}
        skus = tl["item_number"].nunique() if "item_number" in tl.columns else 0
        qty  = int(tl["qty_to_transfer"].sum()) if "qty_to_transfer" in tl.columns else 0
        return {"skus": skus, "locs": len(tl), "qty": qty, "rows": len(tl)}

    metrics = {k: _list_metrics(task_lists.get(k)) for k in ["T1", "T2", "T3", "T4"]}
    total_qty  = sum(m["qty"]  for m in metrics.values())
    total_skus = sum(m["skus"] for m in metrics.values())

    # Top-level totals
    tot1, tot2, tot3 = st.columns(3)
    tot1.metric("Total Transfer Qty",  f"{total_qty:,}")
    tot2.metric("Total SKUs",          f"{total_skus:,}")
    tot3.metric("Task Lists Active",
                f"{sum(1 for m in metrics.values() if m['rows'] > 0)} / 4")

    st.divider()

    # Per-list cards
    dc1, dc2, dc3, dc4 = st.columns(4)
    for col, key in zip([dc1, dc2, dc3, dc4], ["T1", "T2", "T3", "T4"]):
        m    = metrics[key]
        meta = _T_META[key]
        clr  = meta["color"]
        has  = m["rows"] > 0
        badge_bg  = clr + "22"
        badge_txt = clr
        with col:
            st.markdown(f"""
            <div style="border:2px solid {clr};border-radius:12px;padding:16px 18px;
                        background:{'#fff' if has else '#fafafa'}">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
                <span style="font-size:1.3rem">{meta['icon']}</span>
                <span style="font-size:1rem;font-weight:700;color:{clr}">{meta['label']}</span>
              </div>
              <div style="font-size:.78rem;color:#888;margin-bottom:12px">{meta['desc']}</div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
                <div style="background:{badge_bg};border-radius:8px;padding:8px 10px">
                  <div style="font-size:.68rem;color:#888;text-transform:uppercase;
                              letter-spacing:.05em">SKUs</div>
                  <div style="font-size:1.4rem;font-weight:800;color:{badge_txt if has else '#ccc'}">
                    {m['skus']:,}</div>
                </div>
                <div style="background:{badge_bg};border-radius:8px;padding:8px 10px">
                  <div style="font-size:.68rem;color:#888;text-transform:uppercase;
                              letter-spacing:.05em">Locs</div>
                  <div style="font-size:1.4rem;font-weight:800;color:{badge_txt if has else '#ccc'}">
                    {m['locs']:,}</div>
                </div>
                <div style="background:{badge_bg};border-radius:8px;padding:8px 10px;
                            grid-column:1/-1">
                  <div style="font-size:.68rem;color:#888;text-transform:uppercase;
                              letter-spacing:.05em">Transfer Qty</div>
                  <div style="font-size:1.6rem;font-weight:800;color:{badge_txt if has else '#ccc'}">
                    {m['qty']:,}</div>
                </div>
              </div>
              {'<div style="margin-top:10px;font-size:.78rem;font-weight:600;color:#27ae60">✓ Has tasks</div>'
               if has else
               '<div style="margin-top:10px;font-size:.78rem;color:#ccc">— No tasks</div>'}
            </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Source type breakdown bar chart ───────────────────────────────────────
    _all_tasks = [task_lists.get(k) for k in ["T1","T2","T3","T4"] if task_lists.get(k) is not None and not task_lists.get(k, pd.DataFrame()).empty]
    if _all_tasks:
        _combined = pd.concat(_all_tasks, ignore_index=True)
        if "source_type" in _combined.columns and "qty_to_transfer" in _combined.columns:
            _st_summary = (
                _combined.groupby("source_type")["qty_to_transfer"]
                .sum().reset_index()
                .sort_values("qty_to_transfer", ascending=False)
            )
            _SOURCE_COLORS = {
                "top_emergency":    "#e74c3c",
                "mid_emergency":    "#e74c3c",
                "mid_zone_fallback":"#e74c3c",
                "top_routine":      "#5B8DEF",
                "mid_routine":      "#5B8DEF",
                "mid_bin_to_stock": "#F0A500",
                "mid_stock_to_fc":  "#8C8C8C",
                "top_stock_to_fc":  "#8C8C8C",
            }
            _bar_colors = [_SOURCE_COLORS.get(s, "#aaa") for s in _st_summary["source_type"]]
            fig_st = go.Figure(go.Bar(
                x=_st_summary["source_type"],
                y=_st_summary["qty_to_transfer"],
                marker_color=_bar_colors,
                text=_st_summary["qty_to_transfer"].apply(lambda v: f"{int(v):,}"),
                textposition="outside",
            ))
            fig_st.update_layout(
                title="Transfer Qty by Source Type",
                xaxis_title=None,
                yaxis_title="Qty",
                height=280,
                margin=dict(t=40, b=20, l=0, r=0),
                plot_bgcolor="white",
                yaxis=dict(gridcolor="#f0f0f0"),
            )
            st.plotly_chart(fig_st, use_container_width=True)

    st.divider()

    # ── Detail tabs ───────────────────────────────────────────────────────────
    sfc2fc_t1, sfc2fc_t2, sfc2fc_t3, sfc2fc_t4 = st.tabs([
        f"🚨 T1 紧急  ({metrics['T1']['rows']} rows)",
        f"📦 T2 常规  ({metrics['T2']['rows']} rows)",
        f"🔄 T3 Bin→Stock  ({metrics['T3']['rows']} rows)",
        f"📅 T4 DateSwitch  ({metrics['T4']['rows']} rows)",
    ])

    def _render_task_table(df_task: pd.DataFrame, tab_label: str, fname: str):
        if df_task is None or df_task.empty:
            st.info(f"No {tab_label} tasks at current thresholds.")
            return
        display_cols = [c for c in [
            "item_number", "segment", "source_type", "location_no", "loc_type",
            "qty_to_transfer", "qty", "exp_date", "case_qty", "fc_d2s",
            "avg_daily_orders", "rule",
        ] if c in df_task.columns]
        if not display_cols:
            display_cols = list(df_task.columns)
        st.caption(f"{len(df_task):,} rows · {df_task['item_number'].nunique() if 'item_number' in df_task.columns else '?'} SKUs")
        st.dataframe(df_task[display_cols], use_container_width=True, height=400)
        st.download_button(
            f"⬇ Download {tab_label} CSV",
            df_task[display_cols].to_csv(index=False).encode(),
            file_name=fname,
            mime="text/csv",
            key=f"dl_{tab_label}",
        )

    with sfc2fc_t1:
        st.markdown("#### T1 — 紧急补货")
        st.caption("TOP fc_d2s < 3 · MIDDLE fc_d2s < 3 · MIDDLE SFC Bin EXP更差 (Zone Fallback)")
        _render_task_table(task_lists.get("T1"), "T1", "sfc_to_fc_t1.csv")

    with sfc2fc_t2:
        st.markdown("#### T2 — 常规补货")
        st.caption("TOP fc_d2s ≥ 3 · MIDDLE SFC Stock fc_d2s < 14 · EXP OK")
        _render_task_table(task_lists.get("T2"), "T2", "sfc_to_fc_t2.csv")

    with sfc2fc_t3:
        st.markdown("#### T3 — SFC Bin → SFC Stock")
        st.caption("MIDDLE SFC Bin位移位（无EXP触发，或SFC比FC更新鲜）")
        _render_task_table(task_lists.get("T3"), "T3", "sfc_to_fc_t3.csv")

    with sfc2fc_t4:
        st.markdown("#### T4 — DateSwitch (SFC Stock → FC)")
        st.caption("EXP trigger，SFC保质期更差 → 搬回FC先出")
        _render_task_table(task_lists.get("T4"), "T4", "sfc_to_fc_t4.csv")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Putaway  (Req 14.1, 14.2, 14.3, 14.4, 8.1–8.9)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_putaway:
    st.markdown("### Putaway Decision Table")
    st.caption(
        "Assign each inbound receipt to FC or SFC. "
        "Upload an inbound CSV or use the current inventory snapshot as a demo."
    )

    uploaded = st.file_uploader(
        "Upload inbound CSV (columns: item_number, new_exp_date)",
        type=["csv"],
        key="putaway_upload",
    )

    # Build per-SKU fc_max_exp_date from the already-loaded fc_exp summary
    if "df_fc_exp" not in dir():
        _all_skus = df["item_number"].tolist()
        df_fc_exp = load_fc_exp_summary(tuple(_all_skus))

    # Build inventory reference: item_number, sfc_qty, fc_max_exp_date
    df_inv_ref = df[["item_number", "sfc_qty"]].copy()
    if not df_fc_exp.empty:
        df_inv_ref = df_inv_ref.merge(
            df_fc_exp[["item_number", "fc_max_exp_date"]],
            on="item_number", how="left"
        )
    else:
        df_inv_ref["fc_max_exp_date"] = None

    if uploaded is not None:
        df_inbound = pd.read_csv(uploaded)
        df_inbound["item_number"] = df_inbound["item_number"].astype(str)
        # Merge segment + fc_d2s from current df
        df_inbound = df_inbound.merge(
            df[["item_number", "seg", "fc_d2s", "fc_qty", "avg_daily_orders"]].drop_duplicates("item_number"),
            on="item_number", how="left",
        )
        if "need_minimal_exp" not in df_inbound.columns:
            df_inbound["need_minimal_exp"] = 0
        if "new_exp_date" not in df_inbound.columns:
            df_inbound["new_exp_date"] = None

        df_putaway_out = run_putaway(df_inbound, df_inv_ref, cfg)
        _show = [c for c in ["item_number", "seg", "fc_d2s", "putaway_zone",
                              "putaway_rule", "putaway_reason"]
                 if c in df_putaway_out.columns]
        st.dataframe(df_putaway_out[_show], use_container_width=True)
        st.download_button(
            "⬇ Download Putaway CSV",
            df_putaway_out[_show].to_csv(index=False).encode(),
            file_name="putaway_decisions.csv",
            mime="text/csv",
        )

    else:
        st.info(
            "No inbound file uploaded. "
            "Upload a CSV with `item_number` and optionally `new_exp_date` "
            "to generate putaway decisions."
        )

        # Demo: show putaway decisions for a 20-SKU sample
        with st.expander("Preview — putaway decisions for a 20-SKU sample"):
            df_sample = df.sample(min(20, len(df)), random_state=42).copy()
            df_sample["new_exp_date"] = None
            if "need_minimal_exp" not in df_sample.columns:
                df_sample["need_minimal_exp"] = 0

            # Merge fc_max_exp_date for the sample
            df_sample = df_sample.merge(
                df_inv_ref[["item_number", "fc_max_exp_date"]],
                on="item_number", how="left"
            )

            results = df_sample.apply(lambda r: decide_putaway(r, cfg), axis=1, result_type="expand")
            df_sample_out = pd.concat([
                df_sample[["item_number", "seg", "fc_d2s", "sfc_qty"]],
                results
            ], axis=1)
            st.dataframe(df_sample_out, use_container_width=True)
