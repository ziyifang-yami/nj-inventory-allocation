"""
诊断 SKU 维度问题 — Part 2（只分析 allocation_result.csv，无需 DB 连接）
"""
import pandas as pd

result = pd.read_csv("allocation_result.csv")
print(f"总行数（SKU 数）: {len(result):,}")

# ── fc_qty 分布
print("\n[1] fc_qty 分布")
cuts = [-1, 0, 1, 5, 10, 50, 100, 500, float('inf')]
labs = ['=0', '=1', '2-5', '6-10', '11-50', '51-100', '101-500', '500+']
result['fc_bin'] = pd.cut(result['fc_qty'], bins=cuts, labels=labs)
fc_dist = result['fc_bin'].value_counts().sort_index()
for l, v in fc_dist.items():
    print(f"  fc_qty {l:>8}: {v:>7,} SKU  ({v/len(result)*100:.1f}%)")

# ── sfc_qty 分布
print("\n[2] sfc_qty 分布")
result['sfc_bin'] = pd.cut(result['sfc_qty'], bins=cuts, labels=labs)
sfc_dist = result['sfc_bin'].value_counts().sort_index()
for l, v in sfc_dist.items():
    print(f"  sfc_qty {l:>8}: {v:>7,} SKU  ({v/len(result)*100:.1f}%)")

# ── fc_qty=0 的 SKU（只有 sfc）
fc_zero = result[result['fc_qty'] == 0]
print(f"\n[3] fc_qty=0 的 SKU（只有 SFC 库存，无 FC 库存）: {len(fc_zero):,} 个")
print("  action 分布:")
for a, v in fc_zero['action'].value_counts().items():
    print(f"    {a}: {v:,}")

# ── sfc_qty=0 的 SKU（只有 fc）
sfc_zero = result[result['sfc_qty'] == 0]
print(f"\n[4] sfc_qty=0 的 SKU（只有 FC 库存，无 SFC 库存）: {len(sfc_zero):,} 个")
print("  action 分布:")
for a, v in sfc_zero['action'].value_counts().items():
    print(f"    {a}: {v:,}")

# ── wms_status=0（无 WMS 记录）
no_wms = result[result['wms_status'] == 0]
print(f"\n[5] wms_status=0（无 WMS 在管记录）: {len(no_wms):,} 个")
print(f"  其中 fc_qty>0: {(no_wms['fc_qty']>0).sum():,}")
print(f"  其中 sfc_qty>0: {(no_wms['sfc_qty']>0).sum():,}")
print("  action 分布:")
for a, v in no_wms['action'].value_counts().items():
    print(f"    {a}: {v:,}")

# ── group_tier 分布
print("\n[6] group_tier 分布（所有 59,018 SKU）")
gt = result['group_tier'].value_counts().sort_index()
for tier, cnt in gt.items():
    print(f"  tier {tier:.0f}: {cnt:>7,} SKU  ({cnt/len(result)*100:.1f}%)")

# ── 核心问题：tier 16 里面 fc_qty 很小的占多少
t16 = result[result['group_tier'] == 16]
print(f"\n[7] Tier 16 详细分析（{len(t16):,} SKU）")
t16_fc_bin = pd.cut(t16['fc_qty'], bins=cuts, labels=labs)
for l, v in t16_fc_bin.value_counts().sort_index().items():
    print(f"  fc_qty {l:>8}: {v:>7,} SKU  ({v/len(t16)*100:.1f}%)")

print(f"\n  其中 fc_qty=0（只有 sfc）: {(t16['fc_qty']==0).sum():,}")
print(f"  其中 fc_qty>0 但 <=5: {((t16['fc_qty']>0) & (t16['fc_qty']<=5)).sum():,}")
print(f"  TRANSFER_TO_SFC 中 fc_qty<=10 的: {((t16['action']=='TRANSFER_TO_SFC') & (t16['fc_qty']<=10)).sum():,}")

# ── 问题 SKU：fc_qty 极小（1-5件）却要做 TRANSFER_TO_SFC
tiny_transfer = result[(result['action'] == 'TRANSFER_TO_SFC') & (result['fc_qty'] <= 5) & (result['fc_qty'] > 0)]
print(f"\n[8] TRANSFER_TO_SFC 但 fc_qty 只有 1-5 件: {len(tiny_transfer):,} 个 SKU")
print(tiny_transfer[['item_number','group_tier','mode','fc_qty','sfc_qty','active_days','avg_daily_orders','reason']].head(20).to_string(index=False))

# ── 最后：整体库存总量
print(f"\n[9] 总库存量")
print(f"  总 FC 库存: {result['fc_qty'].sum():>12,.0f} 件")
print(f"  总 SFC 库存: {result['sfc_qty'].sum():>12,.0f} 件")
print(f"  FC+SFC 合计: {(result['fc_qty']+result['sfc_qty']).sum():>12,.0f} 件")
print(f"\n  TRANSFER_TO_SFC 迁出量: {result[result['action']=='TRANSFER_TO_SFC']['transfer_qty'].sum():>12,.0f} 件")
print(f"  RETURN_FROM_SFC 补回量: {result[result['action']=='RETURN_FROM_SFC']['transfer_qty'].abs().sum():>12,.0f} 件")
