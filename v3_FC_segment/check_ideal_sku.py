"""快速看一下 ideal_state_sku 的 SKU 维度分布"""
import pandas as pd
import glob
from pathlib import Path

OUT = Path(__file__).parent / "results"
f = sorted(glob.glob(str(OUT / "ideal_state_sku_*.csv")))[-1]
df = pd.read_csv(f)
df["item_number"] = df["item_number"].astype(str)

print(f"File: {f}")
print(f"Total SKUs: {len(df):,}")
print(f"Columns: {list(df.columns)}\n")

# 1. SKU 维度：fc/sfc 数量变化分布
df["fc_change"]  = df["fc_ideal"]  - df["fc_qty"]
df["sfc_change"] = df["sfc_ideal"] - df["sfc_qty"]

print("=" * 60)
print("1. FC 变化分布（件数）")
print("=" * 60)
cuts = [-float('inf'), -1000, -100, -10, 0, 10, 100, 1000, float('inf')]
labs = ['<-1000', '-1000~-100', '-100~-10', '-10~0', '0~10', '10~100', '100~1000', '>1000']
df['fc_chg_bucket'] = pd.cut(df['fc_change'], bins=cuts, labels=labs)
print(df['fc_chg_bucket'].value_counts().sort_index().to_string())

print("\n" + "=" * 60)
print("2. SKU 状态分类")
print("=" * 60)
df['sku_status'] = 'KEEP'
df.loc[(df['fc_ideal'] == 0) & (df['fc_qty'] > 0), 'sku_status'] = 'FC_CLEARED'
df.loc[(df['fc_ideal'] > df['fc_qty']), 'sku_status'] = 'FC_INCREASED'
df.loc[(df['fc_ideal'] < df['fc_qty']) & (df['fc_ideal'] > 0), 'sku_status'] = 'FC_REDUCED'
print(df.groupby(['seg','sku_status']).size().to_string())

print("\n" + "=" * 60)
print("3. 样本 SKU（TOP FC增加的）")
print("=" * 60)
top_in = df[(df['seg']=='TOP') & (df['fc_ideal']>df['fc_qty'])].head(10)
print(top_in[['item_number','fc_qty','fc_ideal','fc_change','sfc_qty','sfc_ideal','action']].to_string(index=False))

print("\n" + "=" * 60)
print("4. 样本 SKU（TAIL 全清）")
print("=" * 60)
tail_clear = df[(df['seg']=='TAIL') & (df['action']=='FC_TO_DC')].head(10)
print(tail_clear[['item_number','fc_qty','fc_ideal','sfc_qty','sfc_ideal','unit_vol']].to_string(index=False))
