"""
测试分配逻辑 — 用构造数据覆盖所有分支
Run: python test_allocator.py
"""
import pandas as pd
from allocator import allocate, summarize

# ── 构造测试数据 ────────────────────────────────────────────
# avg_daily_orders = orders_30d / 30
# fc_d2s = fc_qty / avg_daily_orders

test_cases = [
    # Hit — FC Primary (upper=45, lower=35, return_trigger=35)
    dict(item_number="HIT_KEEP",        segment="Hit",      fc_qty=400,  sfc_qty=0,    avg_daily_orders=10, active_days=20),  # d2s=40 → KEEP
    dict(item_number="HIT_TRANSFER_D2S",segment="Hit",      fc_qty=600,  sfc_qty=200,  avg_daily_orders=10, active_days=20),  # d2s=60 → TRANSFER 150 (600-450)
    dict(item_number="HIT_TRANSFER_ACT",segment="Hit",      fc_qty=300,  sfc_qty=100,  avg_daily_orders=10, active_days=3),   # active<5 → TRANSFER ALL 300
    dict(item_number="HIT_RETURN",      segment="Hit",      fc_qty=200,  sfc_qty=500,  avg_daily_orders=10, active_days=25),  # d2s=20 < 35 → RETURN 250 (450-200)
    # Middle — Dual WH (upper=35, lower=28, return_trigger=10)
    dict(item_number="MID_KEEP",        segment="Middle",   fc_qty=300,  sfc_qty=0,    avg_daily_orders=10, active_days=15),  # d2s=30 → KEEP
    dict(item_number="MID_TRANSFER",    segment="Middle",   fc_qty=500,  sfc_qty=200,  avg_daily_orders=10, active_days=12),  # d2s=50 → TRANSFER 150 (500-350)
    dict(item_number="MID_RETURN",      segment="Middle",   fc_qty=50,   sfc_qty=800,  avg_daily_orders=10, active_days=20),  # d2s=5 < 10 → RETURN 300 (350-50)
    # Longtail — DC Direct (upper=28, lower=14, return_trigger=7)
    dict(item_number="LT_KEEP",         segment="Longtail", fc_qty=200,  sfc_qty=0,    avg_daily_orders=10, active_days=8),   # d2s=20 → KEEP
    dict(item_number="LT_TRANSFER",     segment="Longtail", fc_qty=400,  sfc_qty=100,  avg_daily_orders=10, active_days=10),  # d2s=40 → TRANSFER 120 (400-280)
    dict(item_number="LT_RETURN",       segment="Longtail", fc_qty=30,   sfc_qty=500,  avg_daily_orders=10, active_days=12),  # d2s=3 < 7 → RETURN 250 (280-30)
    # TBD — same as Longtail
    dict(item_number="TBD_INACTIVE",    segment="TBD",      fc_qty=100,  sfc_qty=50,   avg_daily_orders=5,  active_days=2),   # active<5 → TRANSFER ALL
    dict(item_number="TBD_NO_SALES",    segment="TBD",      fc_qty=50,   sfc_qty=0,    avg_daily_orders=0,  active_days=0),   # no sales → TRANSFER ALL
    dict(item_number="TBD_NO_STOCK",    segment="TBD",      fc_qty=0,    sfc_qty=0,    avg_daily_orders=0,  active_days=0),   # nothing → KEEP
]

df_input = pd.DataFrame(test_cases)

print("=" * 70)
print("INPUT")
print("=" * 70)
print(df_input[["item_number", "segment", "fc_qty", "sfc_qty", "avg_daily_orders", "active_days"]].to_string(index=False))

print("\n" + "=" * 70)
print("ALLOCATION RESULTS")
print("=" * 70)
result = allocate(df_input)
cols = ["item_number", "segment", "mode", "action", "fc_d2s", "active_days",
        "fc_qty", "sfc_qty", "target_fc_qty", "transfer_qty", "reason"]
print(result[cols].to_string(index=False))

print("\n" + "=" * 70)
print("SUMMARY BY SEGMENT + ACTION")
print("=" * 70)
summary = summarize(result)
print(summary.to_string(index=False))

# ── Assertions ──────────────────────────────────────────────
def check(name, condition, msg=""):
    status = "✅" if condition else "❌"
    print(f"  {status} {name}" + (f" — {msg}" if msg else ""))
    return condition

print("\n" + "=" * 70)
print("ASSERTIONS")
print("=" * 70)
r = result.set_index("item_number")
all_pass = True
all_pass &= check("HIT_KEEP → KEEP",           r.loc["HIT_KEEP", "action"] == "KEEP")
all_pass &= check("HIT_TRANSFER_D2S → TRANSFER", r.loc["HIT_TRANSFER_D2S", "action"] == "TRANSFER_TO_SFC")
all_pass &= check("HIT_TRANSFER_D2S qty=150",   r.loc["HIT_TRANSFER_D2S", "transfer_qty"] == 150)
all_pass &= check("HIT_TRANSFER_ACT → TRANSFER all", r.loc["HIT_TRANSFER_ACT", "transfer_qty"] == 300)
all_pass &= check("HIT_RETURN → RETURN",        r.loc["HIT_RETURN", "action"] == "RETURN_FROM_SFC")
all_pass &= check("HIT_RETURN qty=-250",        r.loc["HIT_RETURN", "transfer_qty"] == -250)
all_pass &= check("MID_KEEP → KEEP",            r.loc["MID_KEEP", "action"] == "KEEP")
all_pass &= check("MID_TRANSFER qty=150",       r.loc["MID_TRANSFER", "transfer_qty"] == 150)
all_pass &= check("MID_RETURN qty=-300",        r.loc["MID_RETURN", "transfer_qty"] == -300)
all_pass &= check("LT_KEEP → KEEP",             r.loc["LT_KEEP", "action"] == "KEEP")
all_pass &= check("LT_TRANSFER qty=120",        r.loc["LT_TRANSFER", "transfer_qty"] == 120)
all_pass &= check("LT_RETURN qty=-250",         r.loc["LT_RETURN", "transfer_qty"] == -250)
all_pass &= check("TBD_INACTIVE → TRANSFER all",r.loc["TBD_INACTIVE", "transfer_qty"] == 100)
all_pass &= check("TBD_NO_SALES → TRANSFER all",r.loc["TBD_NO_SALES", "transfer_qty"] == 50)
all_pass &= check("TBD_NO_STOCK → KEEP",        r.loc["TBD_NO_STOCK", "action"] == "KEEP")

print(f"\n{'✅ All tests passed!' if all_pass else '❌ Some tests failed.'}")
