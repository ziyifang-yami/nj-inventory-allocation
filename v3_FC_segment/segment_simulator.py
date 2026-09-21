"""
V3 FC Segment Simulator — Streamlit App
========================================
主页：调整 AOD / OL 阈值，实时看 TOP / MIDDLE / TAIL 分布
详情页：每个 tier 的 SKU 列表，按 AOD 排序

Run: streamlit run segment_simulator.py
"""

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="V3 FC Segment Simulator",
    page_icon="📦",
    layout="wide",
)

# ── Load data ─────────────────────────────────────────────────
@st.cache_data
def load_data():
    import glob, os
    files = sorted(glob.glob("results/segment_sku_detail_*.csv"))
    if not files:
        st.error("No segment_sku_detail_*.csv found in results/")
        st.stop()
    latest = files[-1]
    df = pd.read_csv(latest)
    return df, os.path.basename(latest)

df, fname = load_data()

# ── Sidebar controls ──────────────────────────────────────────
st.sidebar.title("⚙️ Threshold Controls")
st.sidebar.markdown("**TOP Conditions (AND)**")
aod_thresh = st.sidebar.slider(
    "Active Order Days ≥", min_value=5, max_value=30, value=21, step=1
)
ol_thresh = st.sidebar.slider(
    "Order Lines (30D) ≥", min_value=10, max_value=200, value=100, step=5
)
st.sidebar.markdown("**TAIL Condition**")
tail_thresh = st.sidebar.slider(
    "Order Lines (30D) <", min_value=1, max_value=30, value=8, step=1
)

st.sidebar.markdown("---")
st.sidebar.caption(f"Data: `{fname}`")
st.sidebar.caption(f"Total SKUs: {len(df):,}")

# Reference targets
st.sidebar.markdown("**📌 Target (Sheet)**")
st.sidebar.markdown("- SKU: 10.2%")
st.sidebar.markdown("- OL:  70.2%")
st.sidebar.markdown("- Units: 77.7%")
st.sidebar.markdown("- CBM:  80.4%")

# ── Classify ──────────────────────────────────────────────────
def classify(row):
    if row["active_order_days"] >= aod_thresh and row["order_lines_30d"] >= ol_thresh:
        return "TOP"
    if row["order_lines_30d"] < tail_thresh:
        return "TAIL"
    return "MIDDLE"

df["segment"] = df.apply(classify, axis=1)

total       = len(df)
total_ol    = df["order_lines_30d"].sum()
total_units = df["sales_units_30d"].sum()
total_cbm   = df["daily_sales_cbm"].sum()

# ── Navigation ────────────────────────────────────────────────
page = st.sidebar.radio("📄 Page", ["📊 Summary", "📋 SKU Detail"])

# ══════════════════════════════════════════════════════════════
# PAGE 1: Summary
# ══════════════════════════════════════════════════════════════
if page == "📊 Summary":
    st.title("📦 V3 FC Segment Simulator")
    st.caption(f"TOP: AOD ≥ {aod_thresh} AND OL ≥ {ol_thresh}  |  TAIL: OL < {tail_thresh}  |  MIDDLE: everything else")

    # KPI cards per segment
    cols = st.columns(3)
    COLORS = {"TOP": "#1f77b4", "MIDDLE": "#ff7f0e", "TAIL": "#d62728"}

    summary_rows = []
    for i, seg in enumerate(["TOP", "MIDDLE", "TAIL"]):
        sub = df[df["segment"] == seg]
        pct_sku   = len(sub) / total * 100
        pct_ol    = sub["order_lines_30d"].sum() / total_ol * 100 if total_ol else 0
        pct_units = sub["sales_units_30d"].sum() / total_units * 100 if total_units else 0
        pct_cbm   = sub["daily_sales_cbm"].sum() / total_cbm * 100 if total_cbm else 0
        avg_aod   = sub["active_order_days"].mean()
        avg_ol    = sub["order_lines_30d"].mean()
        fc_qty    = sub["fc_qty"].sum()
        sfc_qty   = sub["sfc_qty"].sum()

        summary_rows.append({
            "Segment": seg, "SKU": len(sub), "SKU%": pct_sku,
            "OL%": pct_ol, "Units%": pct_units, "CBM%": pct_cbm,
            "FC Qty": fc_qty, "SFC Qty": sfc_qty,
        })

        with cols[i]:
            st.markdown(f"### {seg}")
            st.metric("SKU Count", f"{len(sub):,}", f"{pct_sku:.1f}% of total")
            st.metric("Order Lines%", f"{pct_ol:.1f}%")
            st.metric("Sales Units%", f"{pct_units:.1f}%")
            st.metric("Daily CBM%", f"{pct_cbm:.1f}%")
            st.metric("Avg AOD", f"{avg_aod:.1f} days")
            st.metric("Avg OL/30d", f"{avg_ol:.1f}")
            st.metric("FC Qty", f"{fc_qty:,.0f}")
            st.metric("SFC Qty", f"{sfc_qty:,.0f}")

    # Summary table
    st.markdown("---")
    st.subheader("Summary Table")
    summary_df = pd.DataFrame(summary_rows)
    st.dataframe(
        summary_df.style.format({
            "SKU": "{:,}", "SKU%": "{:.1f}%", "OL%": "{:.1f}%",
            "Units%": "{:.1f}%", "CBM%": "{:.1f}%",
            "FC Qty": "{:,.0f}", "SFC Qty": "{:,.0f}",
        }),
        use_container_width=True,
        hide_index=True,
    )

    # vs target
    st.markdown("---")
    st.subheader("vs Target (Sheet Impact)")
    top_row = next((r for r in summary_rows if r["Segment"] == "TOP"), None)
    if top_row:
        targets = {"SKU%": 10.2, "OL%": 70.2, "Units%": 77.7, "CBM%": 80.4}
        comp_cols = st.columns(4)
        for j, (metric, target) in enumerate(targets.items()):
            actual = top_row[metric]
            delta  = actual - target
            comp_cols[j].metric(
                f"TOP {metric}",
                f"{actual:.1f}%",
                f"{delta:+.1f}% vs target {target}%",
                delta_color="normal",
            )

    # Stacked bar chart
    st.markdown("---")
    st.subheader("Distribution Charts")
    chart_data = pd.DataFrame({
        "Segment": ["TOP", "MIDDLE", "TAIL"],
        "SKU%":    [r["SKU%"] for r in summary_rows],
        "OL%":     [r["OL%"] for r in summary_rows],
        "Units%":  [r["Units%"] for r in summary_rows],
        "CBM%":    [r["CBM%"] for r in summary_rows],
    }).set_index("Segment")
    st.bar_chart(chart_data)

# ══════════════════════════════════════════════════════════════
# PAGE 2: SKU Detail
# ══════════════════════════════════════════════════════════════
else:
    st.title("📋 SKU Detail")

    seg_filter = st.selectbox("Select Segment", ["TOP", "MIDDLE", "TAIL"])
    sub = df[df["segment"] == seg_filter].copy()

    # Sort by AOD desc (MIDDLE will get special sort later)
    sub = sub.sort_values("active_order_days", ascending=False)

    st.markdown(f"**{seg_filter}** — {len(sub):,} SKUs")

    display_cols = [
        "item_number", "segment",
        "active_order_days", "order_lines_30d", "avg_daily_orders",
        "sales_units_30d", "avg_daily_sales",
        "inv_days_30",
        "fc_qty", "sfc_qty",
        "daily_sales_cbm",
    ]
    # only show cols that exist
    display_cols = [c for c in display_cols if c in sub.columns]

    st.dataframe(
        sub[display_cols].reset_index(drop=True).style.format({
            "active_order_days":  "{:.0f}",
            "order_lines_30d":    "{:.0f}",
            "avg_daily_orders":   "{:.2f}",
            "sales_units_30d":    "{:.0f}",
            "avg_daily_sales":    "{:.2f}",
            "inv_days_30":        "{:.0f}",
            "fc_qty":             "{:,.0f}",
            "sfc_qty":            "{:,.0f}",
            "daily_sales_cbm":    "{:.1f}",
        }),
        use_container_width=True,
        height=600,
    )

    # Download
    csv = sub[display_cols].to_csv(index=False)
    st.download_button(
        label=f"⬇️ Download {seg_filter} SKUs",
        data=csv,
        file_name=f"segment_{seg_filter.lower()}_detail.csv",
        mime="text/csv",
    )
