import pandas as pd

seg   = pd.read_csv(r'results/segment_sku_detail_20260917_2247.csv')
alloc = pd.read_csv(r'results/allocation_result_v3_20260918_0955.csv')
seg['item_number']   = seg['item_number'].astype(str)
alloc['item_number'] = alloc['item_number'].astype(str)

for c in ['fc_qty','sfc_qty']:
    seg[c] = pd.to_numeric(seg[c], errors='coerce').fillna(0)

seg_stock = seg[(seg['fc_qty']>0)|(seg['sfc_qty']>0)]

# default thresholds
aod_top, ol_top, ol_tail = 21, 30, 8
def classify(r):
    if r.active_order_days >= aod_top and r.order_lines_30d >= ol_top: return 'TOP'
    if r.order_lines_30d < ol_tail: return 'TAIL'
    return 'MIDDLE'

seg_stock = seg_stock.copy()
seg_stock['seg_live'] = seg_stock.apply(classify, axis=1)

print(f"seg in-stock total : {len(seg_stock):,}")
print(f"alloc total        : {len(alloc):,}")
print()

s_set = set(seg_stock['item_number'])
a_set = set(alloc['item_number'])
print(f"in both            : {len(s_set & a_set):,}")
print(f"only in seg        : {len(s_set - a_set):,}  ← these appear in Overview but NOT in B/A")
print(f"only in alloc      : {len(a_set - s_set):,}  ← these appear in B/A but NOT in Overview")
print()

print("=== Overview seg counts (seg_stock, live classify) ===")
print(seg_stock['seg_live'].value_counts().to_string())
print()

print("=== Before/After seg counts (alloc, with live re-classify) ===")
base = seg_stock[['item_number','active_order_days','order_lines_30d']].copy()
alloc2 = alloc.merge(base, on='item_number', how='left')
for c in ['fc_qty','sfc_qty']:
    alloc2[c] = pd.to_numeric(alloc2[c], errors='coerce').fillna(0)
alloc2 = alloc2[(alloc2['fc_qty']>0)|(alloc2['sfc_qty']>0)].copy()
def classify2(r):
    aod = r.get('active_order_days', 0) or 0
    ol  = r.get('order_lines_30d',   0) or 0
    if aod >= aod_top and ol >= ol_top: return 'TOP'
    if ol < ol_tail: return 'TAIL'
    return 'MIDDLE'
alloc2['seg_live'] = alloc2.apply(classify2, axis=1)
print(alloc2['seg_live'].value_counts().to_string())
