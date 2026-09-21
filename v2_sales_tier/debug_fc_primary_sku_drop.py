"""
诊断：FC_PRIMARY tier 里为什么 FC 有货 SKU 在减少
"""
import pandas as pd

df = pd.read_csv("allocation_result.csv")

for gt in [9, 10, 11]:
    sub = df[df["group_tier"] == gt].copy()
    sub["fc_ideal"]  = sub.apply(lambda r:
        max(0, r.fc_qty - r.transfer_qty) if r.action == "TRANSFER_TO_SFC"
        else r.fc_qty + abs(r.transfer_qty) if r.action == "RETURN_FROM_SFC"
        else r.fc_qty, axis=1)

    # FC 有货变没货的 SKU
    drop = sub[(sub["fc_qty"] > 0) & (sub["fc_ideal"] == 0)]
    print(f"\n{'='*70}")
    print(f"Tier {gt} — FC有货→FC无货 的 SKU：{len(drop)} 个")
    if len(drop):
        print(drop[["item_number","fc_qty","sfc_qty","fc_ideal",
                     "action","transfer_qty","avg_daily_orders",
                     "active_days","fc_d2s","reason"]].to_string(index=False))

    # FC 没货但仍然是 KEEP 的 SKU
    fc_zero_keep = sub[(sub["fc_qty"] == 0) & (sub["action"] == "KEEP")]
    print(f"\nTier {gt} — fc_qty=0 且 KEEP 的 SKU：{len(fc_zero_keep)} 个")
    if len(fc_zero_keep):
        print(fc_zero_keep[["item_number","fc_qty","sfc_qty","action",
                             "avg_daily_orders","active_days","reason"]].head(10).to_string(index=False))
