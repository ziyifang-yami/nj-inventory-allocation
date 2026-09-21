"""验证 TOP 分级边界：看 aod 和 order_lines 的分布"""
import pandas as pd

df = pd.read_csv("results/segment_sku_detail_20260917_2247.csv")

print(f"Total SKUs: {len(df):,}")
print(f"\nSegment counts:")
print(df["segment"].value_counts())

# 看 MIDDLE 里 aod >= 21 但 ol < 100 的（差一点进 TOP）
near_top = df[(df["segment"] == "MIDDLE") &
              (df["active_order_days"] >= 21) &
              (df["order_lines_30d"] < 100)]
print(f"\nMIDDLE SKUs with AOD>=21 but OL<100 (差一步进TOP): {len(near_top)}")
print(near_top[["item_number","active_order_days","order_lines_30d","inv_days_30"]].head(20).to_string(index=False))

# 看 MIDDLE 里 ol >= 100 但 aod < 21 的
near_top2 = df[(df["segment"] == "MIDDLE") &
               (df["active_order_days"] < 21) &
               (df["order_lines_30d"] >= 100)]
print(f"\nMIDDLE SKUs with OL>=100 but AOD<21 (差一步进TOP): {len(near_top2)}")
print(near_top2[["item_number","active_order_days","order_lines_30d","inv_days_30"]].head(20).to_string(index=False))

# AOD 和 OL 的分布
print(f"\nAOD 分布（MIDDLE）:")
print(df[df["segment"]=="MIDDLE"]["active_order_days"].describe())

print(f"\nOL 分布（MIDDLE）:")
print(df[df["segment"]=="MIDDLE"]["order_lines_30d"].describe())

# inv_days_30 的分布
print(f"\ninv_days_30 分布（全量）:")
print(df["inv_days_30"].describe())
print(f"\nSKU with inv_days_30 < 21: {(df['inv_days_30'] < 21).sum():,}")
