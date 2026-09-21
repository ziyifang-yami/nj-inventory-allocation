"""
SKU 分布变化计算（Before vs After）
=====================================
从 ideal_state_sku 计算：
  Before: fc_qty>0 的 SKU 集合，sfc_qty>0 的 SKU 集合，两者交集
  After:  fc_ideal>0 的 SKU 集合，sfc_ideal>0 的 SKU 集合，两者交集

输出：
  results/sku_distribution_ba.csv  — 每个 SKU 的 before/after 位置
  控制台打印汇总
"""
import pandas as pd
import glob
from pathlib import Path

OUT = Path(__file__).parent / "results"
f = sorted(glob.glob(str(OUT / "ideal_state_sku_*.csv")))[-1]
print(f"File: {f}")
df = pd.read_csv(f)
df["item_number"] = df["item_number"].astype(str)
for c in ["fc_qty","sfc_qty","fc_ideal","sfc_ideal","transfer_qty"]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

# ── SKU location: Before ──────────────────────────────────────
df["loc_before"] = "none"
df.loc[(df["fc_qty"]>0) & (df["sfc_qty"]==0), "loc_before"] = "FC_only"
df.loc[(df["fc_qty"]==0) & (df["sfc_qty"]>0), "loc_before"] = "SFC_only"
df.loc[(df["fc_qty"]>0) & (df["sfc_qty"]>0),  "loc_before"] = "Both"

# ── SKU location: After ───────────────────────────────────────
df["loc_after"] = "none"
df.loc[(df["fc_ideal"]>0) & (df["sfc_ideal"]==0), "loc_after"] = "FC_only"
df.loc[(df["fc_ideal"]==0) & (df["sfc_ideal"]>0), "loc_after"] = "SFC_only"
df.loc[(df["fc_ideal"]>0) & (df["sfc_ideal"]>0),  "loc_after"] = "Both"

# ── Transition matrix ─────────────────────────────────────────
print("\n" + "="*60)
print("SKU LOCATION TRANSITION (Before → After)")
print("="*60)
matrix = df.groupby(["loc_before","loc_after"]).size().unstack(fill_value=0)
print(matrix.to_string())

# ── Summary by segment ────────────────────────────────────────
print("\n" + "="*60)
print("SKU DISTRIBUTION BY SEGMENT")
print("="*60)
for seg in ["TOP","MIDDLE","TAIL"]:
    sub = df[df["seg"]==seg]
    if sub.empty: continue
    b_fc  = (sub["loc_before"]=="FC_only").sum()
    b_sfc = (sub["loc_before"]=="SFC_only").sum()
    b_both= (sub["loc_before"]=="Both").sum()
    b_none= (sub["loc_before"]=="none").sum()
    a_fc  = (sub["loc_after"]=="FC_only").sum()
    a_sfc = (sub["loc_after"]=="SFC_only").sum()
    a_both= (sub["loc_after"]=="Both").sum()
    a_none= (sub["loc_after"]=="none").sum()
    print(f"\n[{seg}]  {len(sub):,} SKUs")
    print(f"  {'':12} {'Before':>8} {'After':>8} {'Δ':>8}")
    print(f"  {'FC only':12} {b_fc:>8,} {a_fc:>8,} {a_fc-b_fc:>+8,}")
    print(f"  {'SFC only':12} {b_sfc:>8,} {a_sfc:>8,} {a_sfc-b_sfc:>+8,}")
    print(f"  {'Both':12} {b_both:>8,} {a_both:>8,} {a_both-b_both:>+8,}")
    print(f"  {'No stock':12} {b_none:>8,} {a_none:>8,} {a_none-b_none:>+8,}")

# ── Overall ───────────────────────────────────────────────────
print("\n" + "="*60)
print("OVERALL")
print("="*60)
locs = ["FC_only","SFC_only","Both","none"]
labels = {"FC_only":"FC only","SFC_only":"SFC only","Both":"Both (overlap)","none":"No stock"}
print(f"  {'':16} {'Before':>8} {'After':>8} {'Δ':>8}")
for loc in locs:
    b = (df["loc_before"]==loc).sum()
    a = (df["loc_after"]==loc).sum()
    print(f"  {labels[loc]:16} {b:>8,} {a:>8,} {a-b:>+8,}")

# ── Save ──────────────────────────────────────────────────────
from datetime import datetime
ts = datetime.now().strftime("%Y%m%d_%H%M")
out_path = OUT / f"sku_distribution_ba_{ts}.csv"
df[["item_number","seg","biz_type","action",
    "fc_qty","sfc_qty","fc_ideal","sfc_ideal","transfer_qty",
    "loc_before","loc_after"]].to_csv(out_path, index=False)
print(f"\n✅ Saved: {out_path}  ({len(df):,} rows)")
