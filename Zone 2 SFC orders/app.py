"""
Zone 2 SFC Orders Dashboard
=============================
Real-time MySQL query of wh_picking_order_item for Zone 2 (zone_id=2)
Metrics: orders, units, unique SKUs, units/order, SKUs/order — past 30 days

Run: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta

st.set_page_config(
    page_title="Zone 2 SFC Orders",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
  .block-container { padding-top: 1.5rem; }
</style>
""", unsafe_allow_html=True)

# ── Helper ─────────────────────────────────────────────────────
def _load_env():
    from dotenv import load_dotenv
    from pathlib import Path
    env = Path(".") / ".env"
    if not env.exists():
        env = Path("..") / ".env"
    load_dotenv(dotenv_path=env, override=True)

# ── Data load ─────────────────────────────────────────────────
@st.cache_data(ttl=1800)
def load_zone2_orders(date_from: str, date_to: str):
    """Real-time MySQL: Zone 2 (zone_id=2) picking orders for date range."""
    import os, pymysql
    _load_env()
    try:
        conn = pymysql.connect(
            host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
            connect_timeout=10,
        )
        from datetime import date as date_type
        dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        dt_to   = datetime.strptime(date_to,   "%Y-%m-%d") + timedelta(days=1)  # inclusive
        ts_from = int(dt_from.timestamp())
        ts_to   = int(dt_to.timestamp())

        # Get Zone 2 location prefixes
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT LEFT(location_no, 1) AS prefix
                FROM yamibuy_wh.wh_storage_location
                WHERE warehouse_number='002' AND zone_id=2
            """)
            prefixes = [r[0] for r in cur.fetchall()]

        like_conds = " OR ".join([f"poi.location_no LIKE '{p}%%'" for p in prefixes])

        # Daily stats — per-order averages + distinct SKUs covered that day
        # Two-pass: (1) per-order stats for avg; (2) distinct SKU count per day
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    o.pick_date,
                    o.orders,
                    o.units,
                    o.units_per_order,
                    o.skus_per_order,
                    IFNULL(s.sku_coverage, 0) AS sku_coverage
                FROM (
                    SELECT
                        pick_date,
                        COUNT(*)                     AS orders,
                        SUM(order_units)             AS units,
                        ROUND(AVG(order_units), 2)   AS units_per_order,
                        ROUND(AVG(order_skus),  3)   AS skus_per_order
                    FROM (
                        SELECT
                            DATE(FROM_UNIXTIME(poi.picking_dtm)) AS pick_date,
                            poi.order_no,
                            SUM(poi.picking_quantity)            AS order_units,
                            COUNT(DISTINCT poi.item_number)      AS order_skus
                        FROM yamibuy_wh.wh_picking_order_item poi
                        WHERE poi.warehouse_number = '002'
                          AND poi.picking_dtm >= %s
                          AND poi.picking_dtm <  %s
                          AND poi.picking_quantity > 0
                          AND poi.skip_flag = 0
                          AND ({like_conds})
                        GROUP BY DATE(FROM_UNIXTIME(poi.picking_dtm)), poi.order_no
                    ) per_order
                    GROUP BY pick_date
                ) o
                JOIN (
                    SELECT
                        DATE(FROM_UNIXTIME(poi.picking_dtm)) AS pick_date,
                        COUNT(DISTINCT poi.item_number)      AS sku_coverage
                    FROM yamibuy_wh.wh_picking_order_item poi
                    WHERE poi.warehouse_number = '002'
                      AND poi.picking_dtm >= %s
                      AND poi.picking_dtm <  %s
                      AND poi.picking_quantity > 0
                      AND poi.skip_flag = 0
                      AND ({like_conds})
                    GROUP BY DATE(FROM_UNIXTIME(poi.picking_dtm))
                ) s ON s.pick_date = o.pick_date
                ORDER BY o.pick_date
            """, (ts_from, ts_to, ts_from, ts_to))
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()

        conn.close()

        df_daily = pd.DataFrame(rows, columns=cols)
        df_daily["pick_date"] = pd.to_datetime(df_daily["pick_date"])
        for c in ["orders","units","sku_coverage"]:
            df_daily[c] = pd.to_numeric(df_daily[c], errors="coerce").fillna(0).astype(int)
        for c in ["units_per_order","skus_per_order"]:
            df_daily[c] = pd.to_numeric(df_daily[c], errors="coerce").fillna(0).astype(float)

        return df_daily, None
    except Exception as e:
        return None, str(e)

# ── Sidebar ───────────────────────────────────────────────────
with st.sidebar:
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

# ── Load ──────────────────────────────────────────────────────
st.markdown("## 📦 Zone 2 SFC — Orders Dashboard")

# ── Date range row ────────────────────────────────────────────
today        = datetime.now().date()
default_from = today - timedelta(days=29)
dr1, dr2, dr3, _ = st.columns([1, 1, 1, 4])
with dr1:
    date_from = st.date_input("From", value=default_from, max_value=today, key="dr_from")
with dr2:
    date_to   = st.date_input("To",   value=today,        max_value=today, key="dr_to")
with dr3:
    st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

if date_from > date_to:
    st.error("From must be ≤ To")
    st.stop()

# ── Load ──────────────────────────────────────────────────────
with st.spinner("Loading Zone 2 order data..."):
    df_daily, err = load_zone2_orders(str(date_from), str(date_to))

st.caption(f"wh=002 · zone_id=2 · {date_from} → {date_to} · updated {datetime.now().strftime('%Y-%m-%d %H:%M')}")

# Auto-refresh every 30 minutes using meta refresh
st.markdown('<meta http-equiv="refresh" content="1800">', unsafe_allow_html=True)

if err:
    st.error(f"MySQL error: {err}")
    st.stop()

if df_daily is None or df_daily.empty:
    st.warning("No data found.")
    st.stop()

# ── KPI cards ─────────────────────────────────────────────────
total_orders = int(df_daily["orders"].sum())
total_units  = int(df_daily["units"].sum())
avg_upo      = df_daily["units_per_order"].mean()   # avg of daily avgs (per-order correct)
avg_sku_o    = df_daily["skus_per_order"].mean()    # avg of daily avgs (per-order correct)
avg_daily_ord= total_orders / max(len(df_daily), 1)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total Orders",     f"{total_orders:,}")
k2.metric("Total Units",      f"{total_units:,}")
k3.metric("Avg Units/Order",  f"{avg_upo:.2f}")
k4.metric("Avg SKUs/Order",   f"{avg_sku_o:.3f}")
k5.metric("Avg Orders/Day",   f"{avg_daily_ord:,.0f}")

st.divider()

# ── Trend charts ──────────────────────────────────────────────
st.markdown("#### Daily Trends")

# Only label the last 7 data points to avoid clutter on long ranges
n = len(df_daily)
label_mask = [""]*n
for col_name in ["orders","units","sku_coverage","units_per_order","skus_per_order"]:
    pass  # built per-chart below

def sparse_labels(series, fmt="{:.0f}"):
    """Show ~8 evenly spaced labels based on total data points."""
    n = len(series)
    step = max(1, n // 8)
    labels = [""] * n
    for i in range(0, n, step):
        labels[i] = fmt.format(series.iloc[i])
    # always label the last point
    labels[-1] = fmt.format(series.iloc[-1])
    return labels

def _text_positions(n):
    """First point → right, last point → left, rest → top center."""
    pos = ["top center"] * n
    if n > 0: pos[0]  = "middle right"
    if n > 1: pos[-1] = "middle left"
    return pos

def make_bar(col, title, color):
    dates_fmt = df_daily["pick_date"].dt.strftime("%b %d %Y")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df_daily["pick_date"], y=df_daily[col],
        marker_color=color,
        customdata=dates_fmt,
        hovertemplate="%{customdata},  %{y:,}<extra></extra>",
    ))
    col_max = df_daily[col].max()
    fig.update_layout(
        title=title, height=340,
        margin=dict(t=35,b=20,l=40,r=40), plot_bgcolor="white",
        yaxis=dict(gridcolor="#f0f0f0", rangemode="tozero",
                   range=[0, col_max * 1.1]),
        xaxis=dict(showgrid=False),
        showlegend=False,
    )
    return fig

def make_line(col, title, color, fmt="{:.0f}"):
    labels = sparse_labels(df_daily[col], fmt)
    n = len(labels)
    positions = _text_positions(n)
    # for empty-label positions it doesn't matter, set all non-empty ones
    textpos = [positions[i] if labels[i] else "top center" for i in range(n)]
    dates_fmt = df_daily["pick_date"].dt.strftime("%b %d %Y")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_daily["pick_date"], y=df_daily[col],
        fill="tozeroy",
        line=dict(color=color, width=2),
        fillcolor="rgba(52,196,164,.12)" if color=="#34C4A4" else "rgba(231,76,60,.08)",
        mode="lines+markers+text",
        text=labels,
        textposition=textpos,
        textfont=dict(size=12, color="#333"),
        customdata=dates_fmt,
        hovertemplate="%{customdata},  %{y}<extra></extra>",
    ))
    col_max = df_daily[col].max()
    col_range = col_max - df_daily[col].min()
    y_top = col_max + max(col_range * 0.35, col_max * 0.12)
    fig.update_layout(
        title=title, height=340,
        margin=dict(t=35,b=20,l=40,r=40), plot_bgcolor="white",
        yaxis=dict(gridcolor="#f0f0f0", rangemode="tozero",
                   range=[0, y_top]),
        xaxis=dict(showgrid=False),
        showlegend=False,
    )
    return fig

# 5 charts, 2 pages: page0=(0,1) default, page1=(2,3,4)
charts = [
    ("Units/Order",  make_line("units_per_order", "Units / Order",  "#e74c3c", "{:.1f}")),
    ("SKUs/Order",   make_line("skus_per_order",  "SKUs / Order",   "#34C4A4", "{:.2f}")),
    ("SKUs Covered", make_line("sku_coverage",    "SKUs Covered",   "#34C4A4", "{:.0f}")),
    ("Orders",       make_bar("orders",           "Daily Orders",   "#5B8DEF")),
    ("Units",        make_bar("units",            "Daily Units",    "#F0A500")),
]
pages = [(0, 1), (2, 3, 4)]

if "chart_page" not in st.session_state:
    st.session_state.chart_page = 0

# ── Charts ────────────────────────────────────────────────────
idx = pages[st.session_state.chart_page]
cols = st.columns(len(idx))
for col, i in zip(cols, idx):
    with col:
        st.plotly_chart(charts[i][1], use_container_width=True)

# ── Nav buttons — centered below charts ──────────────────────
names = [" + ".join(charts[i][0] for i in pg) for pg in pages]
_, nb_l, nb_mid, nb_r, _ = st.columns([2, 1, 3, 1, 2])
with nb_l:
    if st.button("◀ Prev", disabled=st.session_state.chart_page == 0, key="chart_prev"):
        st.session_state.chart_page -= 1
        st.rerun()
with nb_r:
    if st.button("Next ▶", disabled=st.session_state.chart_page == len(pages)-1, key="chart_next"):
        st.session_state.chart_page += 1
        st.rerun()
with nb_mid:
    st.markdown(
        f"<div style='text-align:center;padding-top:6px;font-size:.85rem;color:#666'>"
        f"{st.session_state.chart_page+1} / {len(pages)} &nbsp;·&nbsp; "
        f"<b>{names[st.session_state.chart_page]}</b></div>",
        unsafe_allow_html=True,
    )

st.divider()

# ── Raw daily table ────────────────────────────────────────────
st.markdown("#### Daily Detail")
st.dataframe(
    df_daily.sort_values("pick_date", ascending=False)[
        ["pick_date","orders","units","sku_coverage","units_per_order","skus_per_order"]
    ].rename(columns={
        "pick_date":      "Date",
        "orders":         "Orders",
        "units":          "Units",
        "sku_coverage":   "SKUs Covered",
        "units_per_order":"Units/Order",
        "skus_per_order": "SKUs/Order",
    }),
    use_container_width=True, hide_index=True,
    column_config={
        "Date":         st.column_config.DateColumn(format="YYYY-MM-DD"),
        "Orders":       st.column_config.NumberColumn(format="%d"),
        "Units":        st.column_config.NumberColumn(format="%d"),
        "Units/Order":  st.column_config.NumberColumn(format="%.2f"),
        "SKUs/Order":   st.column_config.NumberColumn(format="%.3f"),
        "SKUs Covered": st.column_config.NumberColumn(
            format="%d",
            help="Distinct SKUs picked in Zone 2 that day (not avg — raw count)",
        ),
    }
)
st.download_button("⬇ Download CSV",
                   df_daily.to_csv(index=False),
                   "zone2_orders.csv", "text/csv")
