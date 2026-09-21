"""快速模拟不同阈值下的分级结果，不需要重跑数据"""
import pandas as pd

df = pd.read_csv("results/segment_sku_detail_20260917_2247.csv")
total       = len(df)
total_ol    = df["order_lines_30d"].sum()
total_units = df["sales_units_30d"].sum()
total_cbm   = df["daily_sales_cbm"].sum()

# 目标：10.2% SKU / 70.2% OL / 77.7% Units / 80.4% CBM
print(f"Target: SKU~10.2%  OL~70.2%  Units~77.7%  CBM~80.4%\n")
print(f"{'OL≥':>6} {'AOD≥':>5} | {'TOP SKU':>8} {'SKU%':>5} | {'OL%':>6} | {'Units%':>7} | {'CBM%':>6}")
print("-" * 68)

for aod_threshold in [21, 20, 18, 15]:
    for ol_threshold in [100, 90, 80, 70, 60, 50, 40, 30]:
        def classify(row):
            if row["active_order_days"] >= aod_threshold and row["order_lines_30d"] >= ol_threshold:
                return "TOP"
            if row["order_lines_30d"] < 8:
                return "TAIL"
            return "MIDDLE"

        df["seg"] = df.apply(classify, axis=1)
        top = df[df["seg"] == "TOP"]
        pct_sku   = len(top) / total * 100
        pct_ol    = top["order_lines_30d"].sum() / total_ol * 100
        pct_units = top["sales_units_30d"].sum() / total_units * 100
        pct_cbm   = top["daily_sales_cbm"].sum() / total_cbm * 100 if total_cbm > 0 else 0

        # 标记接近目标的行
        flag = ""
        if 8.5 <= pct_sku <= 12 and 65 <= pct_ol <= 75 and 72 <= pct_units <= 82:
            flag = "  ◀ CLOSE"

        print(f"{ol_threshold:>6} {aod_threshold:>5} | {len(top):>8,} {pct_sku:>4.1f}% | {pct_ol:>5.1f}% | {pct_units:>6.1f}% | {pct_cbm:>5.1f}%{flag}")
    print()
