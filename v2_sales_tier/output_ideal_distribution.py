"""
理想状态下 FC / SFC 库存分布
============================
基于 allocation_result.csv，模拟执行所有建议动作后：
  - TRANSFER_TO_SFC: fc_qty 减少 transfer_qty，sfc_qty 增加
  - RETURN_FROM_SFC: sfc_qty 减少 |transfer_qty|，fc_qty 增加
  - KEEP: 不变

输出：
  1. ideal_distribution_by_tier.csv  — 按 group_tier 汇总
  2. ideal_distribution_sku.csv      — 每个 SKU 的理想 fc/sfc
  3. 控制台打印汇总
"""
import pandas as pd

df = pd.read_csv("allocation_result.csv")

# ── 计算理想 fc / sfc ──────────────────────────────────
def ideal_qty(row):
    fc  = row["fc_qty"]
    sfc = row["sfc_qty"]
    qty = row["transfer_qty"]   # TRANSFER_TO_SFC: >0, RETURN_FROM_SFC: <0

    if row["action"] == "TRANSFER_TO_SFC":
        fc_ideal  = max(0, fc - qty)
        sfc_ideal = sfc + qty
    elif row["action"] == "RETURN_FROM_SFC":
        # transfer_qty is negative
        fc_ideal  = fc + abs(qty)
        sfc_ideal = max(0, sfc - abs(qty))
    else:  # KEEP
        fc_ideal  = fc
        sfc_ideal = sfc
    return pd.Series({"fc_ideal": fc_ideal, "sfc_ideal": sfc_ideal})

df[["fc_ideal", "sfc_ideal"]] = df.apply(ideal_qty, axis=1)
df["total_ideal"] = df["fc_ideal"] + df["sfc_ideal"]

# ── 1. 按 group_tier 汇总 ─────────────────────────────
tier_summary = (
    df.groupby(["group_tier", "mode"])
    .agg(
        sku_count        = ("item_number",  "count"),
        # 现状
        fc_now           = ("fc_qty",       "sum"),
        sfc_now          = ("sfc_qty",      "sum"),
        # 理想
        fc_ideal         = ("fc_ideal",     "sum"),
        sfc_ideal        = ("sfc_ideal",    "sum"),
        # 变动量
        transfer_out     = ("transfer_qty", lambda x: x[x > 0].sum()),   # FC→SFC
        transfer_in      = ("transfer_qty", lambda x: x[x < 0].abs().sum()),  # SFC→FC
    )
    .reset_index()
)
tier_summary["fc_change"]  = tier_summary["fc_ideal"]  - tier_summary["fc_now"]
tier_summary["sfc_change"] = tier_summary["sfc_ideal"] - tier_summary["sfc_now"]
tier_summary["fc_pct_now"]   = (tier_summary["fc_now"]   / (tier_summary["fc_now"]  + tier_summary["sfc_now"]).replace(0,1) * 100).round(1)
tier_summary["fc_pct_ideal"] = (tier_summary["fc_ideal"] / (tier_summary["fc_ideal"] + tier_summary["sfc_ideal"]).replace(0,1) * 100).round(1)

# ── 2. 整体汇总 ───────────────────────────────────────
total_fc_now    = df["fc_qty"].sum()
total_sfc_now   = df["sfc_qty"].sum()
total_fc_ideal  = df["fc_ideal"].sum()
total_sfc_ideal = df["sfc_ideal"].sum()

print("=" * 70)
print("理想库存分布 — 执行所有分配建议后的双仓状态")
print("=" * 70)

print(f"\n【整体变化】")
print(f"  FC  现在: {total_fc_now:>12,.0f}  →  理想: {total_fc_ideal:>12,.0f}  差值: {total_fc_ideal - total_fc_now:>+12,.0f}")
print(f"  SFC 现在: {total_sfc_now:>12,.0f}  →  理想: {total_sfc_ideal:>12,.0f}  差值: {total_sfc_ideal - total_sfc_now:>+12,.0f}")
print(f"  FC 占比: {total_fc_now/(total_fc_now+total_sfc_now)*100:.1f}% → {total_fc_ideal/(total_fc_ideal+total_sfc_ideal)*100:.1f}%")

print(f"\n【按 group_tier 的理想分布】")
print(f"{'tier':>5} {'mode':>12} {'SKUs':>6} | {'FC现在':>10} {'SFC现在':>10} | {'FC理想':>10} {'SFC理想':>10} | {'FC占比(前)':>10} {'FC占比(后)':>10}")
print("-" * 100)
for _, r in tier_summary.sort_values("group_tier").iterrows():
    print(f"{r['group_tier']:>5.0f} {r['mode']:>12} {r['sku_count']:>6,.0f} | "
          f"{r['fc_now']:>10,.0f} {r['sfc_now']:>10,.0f} | "
          f"{r['fc_ideal']:>10,.0f} {r['sfc_ideal']:>10,.0f} | "
          f"{r['fc_pct_now']:>9.1f}% {r['fc_pct_ideal']:>9.1f}%")

# ── action 分布 ───────────────────────────────────────
print(f"\n【动作分布】")
action_summary = (
    df.groupby(["group_tier", "mode", "action"])
    .agg(sku_count=("item_number","count"),
         transfer_qty=("transfer_qty", lambda x: x.abs().sum()))
    .reset_index()
)
print(action_summary.sort_values(["group_tier","action"]).to_string(index=False))

# ── 3. 各 tier 中 FC/SFC 分配比例 ────────────────────
print(f"\n【D2S 视角（平均）】")
df_active = df[df["avg_daily_orders"] > 0].copy()
df_active["fc_d2s_ideal"]  = df_active["fc_ideal"]  / df_active["avg_daily_orders"]
df_active["sfc_d2s_ideal"] = df_active["sfc_ideal"] / df_active["avg_daily_orders"]
d2s_summary = (
    df_active.groupby(["group_tier","mode"])
    .agg(
        fc_d2s_median  = ("fc_d2s_ideal",  "median"),
        fc_d2s_mean    = ("fc_d2s_ideal",  "mean"),
        sfc_d2s_median = ("sfc_d2s_ideal", "median"),
    )
    .round(1)
    .reset_index()
)
print(d2s_summary.to_string(index=False))

# ── 4. SKU 变化：各 tier 现在/理想 FC 有货 vs SFC 有货 SKU 数 ─────
print(f"\n【SKU 分布变化：有货 SKU 数（现在 vs 理想）】")
print(f"{'tier':>5} {'mode':>12} {'SKUs':>6} | "
      f"{'FC有货(前)':>10} {'FC有货(后)':>10} {'FC变化':>8} | "
      f"{'SFC有货(前)':>11} {'SFC有货(后)':>11} {'SFC变化':>9}")
print("-" * 105)

for _, r in tier_summary.sort_values("group_tier").iterrows():
    gt   = r["group_tier"]
    sub  = df[df["group_tier"] == gt]
    # 现在
    fc_skus_now  = (sub["fc_qty"]   > 0).sum()
    sfc_skus_now = (sub["sfc_qty"]  > 0).sum()
    # 理想
    fc_skus_ideal  = (sub["fc_ideal"]  > 0).sum()
    sfc_skus_ideal = (sub["sfc_ideal"] > 0).sum()

    print(f"{gt:>5.0f} {r['mode']:>12} {r['sku_count']:>6,.0f} | "
          f"{fc_skus_now:>10,} {fc_skus_ideal:>10,} {fc_skus_ideal - fc_skus_now:>+8,} | "
          f"{sfc_skus_now:>11,} {sfc_skus_ideal:>11,} {sfc_skus_ideal - sfc_skus_now:>+9,}")

# 整体
fc_skus_now_total   = (df["fc_qty"]    > 0).sum()
sfc_skus_now_total  = (df["sfc_qty"]   > 0).sum()
fc_skus_ideal_total = (df["fc_ideal"]  > 0).sum()
sfc_skus_ideal_total= (df["sfc_ideal"] > 0).sum()
print("-" * 105)
print(f"{'合计':>5} {'':>12} {len(df):>6,} | "
      f"{fc_skus_now_total:>10,} {fc_skus_ideal_total:>10,} {fc_skus_ideal_total - fc_skus_now_total:>+8,} | "
      f"{sfc_skus_now_total:>11,} {sfc_skus_ideal_total:>11,} {sfc_skus_ideal_total - sfc_skus_now_total:>+9,}")

# ── 5. 数量变化汇总 ───────────────────────────────────
print(f"\n【数量变化汇总（件）】")
print(f"{'tier':>5} {'mode':>12} | "
      f"{'FC现在':>10} {'FC理想':>10} {'FC变化':>10} | "
      f"{'SFC现在':>10} {'SFC理想':>10} {'SFC变化':>10}")
print("-" * 100)
for _, r in tier_summary.sort_values("group_tier").iterrows():
    print(f"{r['group_tier']:>5.0f} {r['mode']:>12} | "
          f"{r['fc_now']:>10,.0f} {r['fc_ideal']:>10,.0f} {r['fc_change']:>+10,.0f} | "
          f"{r['sfc_now']:>10,.0f} {r['sfc_ideal']:>10,.0f} {r['sfc_change']:>+10,.0f}")
print("-" * 100)
fc_change_total  = total_fc_ideal  - total_fc_now
sfc_change_total = total_sfc_ideal - total_sfc_now
print(f"{'合计':>5} {'':>12} | "
      f"{total_fc_now:>10,.0f} {total_fc_ideal:>10,.0f} {fc_change_total:>+10,.0f} | "
      f"{total_sfc_now:>10,.0f} {total_sfc_ideal:>10,.0f} {sfc_change_total:>+10,.0f}")

# ── Save ─────────────────────────────────────────────
tier_summary.to_csv("ideal_distribution_by_tier.csv", index=False)
df[["item_number","group_tier","mode","action",
    "fc_qty","sfc_qty","fc_ideal","sfc_ideal",
    "transfer_qty","avg_daily_orders","fc_d2s","reason"]].to_csv(
    "ideal_distribution_sku.csv", index=False)

print(f"\n✅ ideal_distribution_by_tier.csv ({len(tier_summary)} rows)")
print(f"✅ ideal_distribution_sku.csv      ({len(df):,} rows)")
