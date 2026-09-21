"""
理想状态计算
=============
基于 allocation_result_v3 + volume_sku_summary，
模拟执行所有分配建议后的 FC / SFC 状态。

输出：
  results/ideal_state_summary.csv   — 按 segment × zone_type 汇总
  results/ideal_state_sku.csv       — 每个 SKU 的理想 FC/SFC qty + volume
"""
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import glob

OUT = Path(__file__).parent / "results"

# ── Load latest allocation result ─────────────────────────────
# 优先读 ideal_allocation（no cap），fallback 到 allocation_result_v3
alloc_files = sorted(glob.glob(str(OUT / "ideal_allocation_v3_*.csv")))
if not alloc_files:
    alloc_files = sorted(glob.glob(str(OUT / "allocation_result_v3_*.csv")))
if not alloc_files:
    raise FileNotFoundError("No allocation result found")
latest_alloc = alloc_files[-1]
print(f"Using allocation: {latest_alloc}")
df_alloc = pd.read_csv(latest_alloc)
df_alloc["item_number"] = df_alloc["item_number"].astype(str)

# ── Load latest volume sku summary ────────────────────────────
vol_files = sorted(glob.glob(str(OUT / "volume_sku_summary_*.csv")))
if not vol_files:
    raise FileNotFoundError("No volume_sku_summary_*.csv found")
latest_vol = vol_files[-1]
print(f"Using volume:     {latest_vol}")
df_vol = pd.read_csv(latest_vol)
df_vol["item_number"] = df_vol["item_number"].astype(str)

# Pivot volume to wide: fc_vol / sfc_vol per SKU
vol_fc  = df_vol[df_vol["zone"]=="FC"] .groupby("item_number").agg(
    fc_vol_in3=("total_volume","sum"), fc_qty_vol=("total_units","sum")).reset_index()
vol_sfc = df_vol[df_vol["zone"]=="SFC"].groupby("item_number").agg(
    sfc_vol_in3=("total_volume","sum"), sfc_qty_vol=("total_units","sum")).reset_index()

# ── Compute ideal qty ──────────────────────────────────────────
df = df_alloc.copy()
df["fc_qty"]  = pd.to_numeric(df["fc_qty"],  errors="coerce").fillna(0)
df["sfc_qty"] = pd.to_numeric(df["sfc_qty"], errors="coerce").fillna(0)
df["transfer_qty"] = pd.to_numeric(df["transfer_qty"], errors="coerce").fillna(0)

def ideal_qty(row):
    fc  = row["fc_qty"]
    sfc = row["sfc_qty"]
    qty = abs(row["transfer_qty"])
    act = row["action"]
    if act == "DC_TO_FC":
        return fc + qty, max(0, sfc - qty)
    elif act == "FC_TO_DC":
        return max(0, fc - qty), sfc + qty
    else:
        return fc, sfc

df[["fc_ideal","sfc_ideal"]] = df.apply(
    lambda r: pd.Series(ideal_qty(r)), axis=1)

# ── Merge unit volume for volume calculation ───────────────────
df["unit_vol"] = pd.to_numeric(df["unit_vol_in3"], errors="coerce").fillna(0) \
    if "unit_vol_in3" in df.columns else 0.0

df["fc_vol_ideal"]  = df["fc_ideal"]  * df["unit_vol"]
df["sfc_vol_ideal"] = df["sfc_ideal"] * df["unit_vol"]
df["fc_vol_now"]    = df["fc_qty"]    * df["unit_vol"]
df["sfc_vol_now"]   = df["sfc_qty"]   * df["unit_vol"]

# ── Summary by segment ─────────────────────────────────────────
print("\n" + "=" * 65)
print("IDEAL STATE SUMMARY BY SEGMENT")
print("=" * 65)

# Current
cur = df.groupby("seg").agg(
    sku_count   = ("item_number", "count"),
    fc_qty_now  = ("fc_qty",      "sum"),
    sfc_qty_now = ("sfc_qty",     "sum"),
    fc_vol_now  = ("fc_vol_now",  "sum"),
    sfc_vol_now = ("sfc_vol_now", "sum"),
).reset_index()

# Ideal
ideal = df.groupby("seg").agg(
    fc_qty_ideal  = ("fc_ideal",     "sum"),
    sfc_qty_ideal = ("sfc_ideal",    "sum"),
    fc_vol_ideal  = ("fc_vol_ideal", "sum"),
    sfc_vol_ideal = ("sfc_vol_ideal","sum"),
).reset_index()

summary = cur.merge(ideal, on="seg")
summary["fc_qty_change"]  = summary["fc_qty_ideal"]  - summary["fc_qty_now"]
summary["sfc_qty_change"] = summary["sfc_qty_ideal"] - summary["sfc_qty_now"]
summary["fc_vol_change"]  = summary["fc_vol_ideal"]  - summary["fc_vol_now"]
summary["sfc_vol_change"] = summary["sfc_vol_ideal"] - summary["sfc_vol_now"]

print(summary[["seg","sku_count",
               "fc_qty_now","fc_qty_ideal","fc_qty_change",
               "sfc_qty_now","sfc_qty_ideal","sfc_qty_change"]].to_string(index=False))

print("\n── Volume (in³) ──")
print(summary[["seg",
               "fc_vol_now","fc_vol_ideal","fc_vol_change",
               "sfc_vol_now","sfc_vol_ideal","sfc_vol_change"]].round(0).to_string(index=False))

# ── Overall ───────────────────────────────────────────────────
print("\n── Overall ──")
print(f"  FC  qty:  {df['fc_qty'].sum():>12,.0f}  →  {df['fc_ideal'].sum():>12,.0f}  "
      f"({df['fc_ideal'].sum()-df['fc_qty'].sum():>+12,.0f})")
print(f"  SFC qty:  {df['sfc_qty'].sum():>12,.0f}  →  {df['sfc_ideal'].sum():>12,.0f}  "
      f"({df['sfc_ideal'].sum()-df['sfc_qty'].sum():>+12,.0f})")
print(f"  FC  vol:  {df['fc_vol_now'].sum():>12,.0f}  →  {df['fc_vol_ideal'].sum():>12,.0f}  "
      f"({df['fc_vol_ideal'].sum()-df['fc_vol_now'].sum():>+12,.0f})")
print(f"  SFC vol:  {df['sfc_vol_now'].sum():>12,.0f}  →  {df['sfc_vol_ideal'].sum():>12,.0f}  "
      f"({df['sfc_vol_ideal'].sum()-df['sfc_vol_now'].sum():>+12,.0f})")

# ── Action summary ────────────────────────────────────────────
print("\n── Action breakdown ──")
act_sum = df.groupby(["seg","action"]).agg(
    sku_count   =("item_number",   "count"),
    transfer_qty=("transfer_qty",  lambda x: x.abs().sum()),
).reset_index()
print(act_sum.to_string(index=False))

# ── Save ──────────────────────────────────────────────────────
ts = datetime.now().strftime("%Y%m%d_%H%M")
summary.to_csv(OUT / f"ideal_state_summary_{ts}.csv", index=False)

sku_out = df[["item_number","seg","biz_type","action",
              "fc_qty","sfc_qty","fc_ideal","sfc_ideal",
              "transfer_qty","unit_vol",
              "fc_vol_now","sfc_vol_now","fc_vol_ideal","sfc_vol_ideal"]].copy()
sku_out.to_csv(OUT / f"ideal_state_sku_{ts}.csv", index=False)

print(f"\n✅ Saved:")
print(f"   ideal_state_summary_{ts}.csv")
print(f"   ideal_state_sku_{ts}.csv  ({len(sku_out):,} rows)")
