"""
V2 — WMS Sales Tier (group_tier) Allocation
============================================
分层来源: yamibuy_wh.wh_lr_product_location.group_tier
库存基数: shared.inventory.load_fc_sfc_inventory (zone_id based)
D2S逻辑:  shared.allocator_base.allocate

Run: python run_allocation.py
"""
import sys
import os
import pandas as pd
import pymysql
from pathlib import Path
from dotenv import load_dotenv

# ── Add shared to path ───────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.inventory      import load_fc_sfc_inventory
from shared.athena         import load_active_selling_days
from shared.allocator_base import allocate, summarize

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# ══════════════════════════════════════════════════════════
# group_tier → (mode_name, d2s_upper, d2s_lower, return_trigger)
#
# group_tier 含义 (NJ=002, wh_lr_product_location):
#   9  = Top 50,      日均 94-721  → FC_PRIMARY
#   10 = Rank 51-200, 日均 50-112  → FC_PRIMARY
#   11 = Rank 201-450,日均 17-50   → FC_PRIMARY
#   12 = Rank 451-950,日均 7-27    → DUAL_WH
#   13 = Rank 951-2450,日均 2-14   → DUAL_WH
#   14 = Rank 2451-8850,日均 0.5-5 → DC_DIRECT
#   15 = Rank 8851+,  日均 <1.3    → DC_DIRECT
#   16 = 无排名/极低频              → DC_DIRECT
# ══════════════════════════════════════════════════════════
MODES = {
     9: ("FC_PRIMARY", 45, 35, 35),
    10: ("FC_PRIMARY", 45, 35, 35),
    11: ("FC_PRIMARY", 45, 35, 35),
    12: ("DUAL_WH",    35, 28, 10),
    13: ("DUAL_WH",    35, 28, 10),
    14: ("DC_DIRECT",  28, 14,  7),
    15: ("DC_DIRECT",  28, 14,  7),
    16: ("DC_DIRECT",  28, 14,  7),
}
DEFAULT_MODE = ("DC_DIRECT", 28, 14, 7)


def load_wms_tier(warehouse: str = "002") -> pd.DataFrame:
    """Load group_tier from wh_lr_product_location (status=1 only)."""
    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "rds.g3.yamibuy.net"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"),
        port=3306, charset="utf8mb4",
    )
    print("  [MySQL] WMS group_tier (wh_lr_product_location, status=1)...", flush=True)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT item_number,
                   MIN(group_tier)    AS group_tier,
                   MAX(average_sales) AS wms_avg_sales,
                   MIN(sales_tier)    AS sales_rank,
                   MAX(status)        AS wms_status
            FROM yamibuy_wh.wh_lr_product_location
            WHERE warehouse_number = '{warehouse}'
              AND is_deleted = 0
              AND status = 1
            GROUP BY item_number
        """)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    conn.close()
    df = pd.DataFrame(rows, columns=cols)
    for col in ["group_tier", "wms_avg_sales", "sales_rank", "wms_status"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["wms_status"] = df["wms_status"].fillna(0)
    print(f"    → {len(df):,} rows", flush=True)
    return df


# ──────────────────────────────────────────────────────────
print("=" * 60)
print("NJ Inventory Allocation V2 — WMS Sales Tier (group_tier)")
print("=" * 60)

df_inv  = load_fc_sfc_inventory(warehouse="002")
df_sal  = load_active_selling_days(warehouse="002", lookback_days=30)
df_tier = load_wms_tier(warehouse="002")

# ── Merge ───────────────────────────────────────────────
print("\nMerging...", flush=True)
df = df_inv.merge(df_tier[["item_number", "group_tier", "wms_avg_sales",
                             "sales_rank", "wms_status"]],
                  on="item_number", how="left")
df = df.merge(df_sal, on="item_number", how="left")

df["wms_status"]       = df["wms_status"].fillna(0).astype(int)
df["group_tier"]       = df["group_tier"].fillna(16)
df["active_days"]      = df["active_days"].fillna(0)
df["avg_daily_orders"] = df["avg_daily_orders"].fillna(0)

# segment_key is the column allocator_base uses for mode lookup
df["segment_key"] = df["group_tier"]

print(f"  → {len(df):,} SKUs total")
wms_active = (df["wms_status"] == 1).sum()
print(f"  → {wms_active:,} with active WMS record ({wms_active/len(df)*100:.1f}%)")
print("\n  group_tier distribution:")
print(df.groupby("group_tier")["item_number"].count().sort_index().to_string())

# ── Run allocator ───────────────────────────────────────
print("\nRunning allocation engine...", flush=True)
result  = allocate(df, MODES, DEFAULT_MODE)
summary = summarize(result, segment_col="segment_key")

# ── Print results ───────────────────────────────────────
print("\n" + "=" * 60)
print("SUMMARY BY GROUP_TIER + ACTION")
print("=" * 60)
print(summary.to_string(index=False))

print("\n── Action totals ──")
print(result.groupby("action").agg(
    sku_count=("item_number", "count"),
    transfer_qty=("transfer_qty", lambda x: x.abs().sum())
).to_string())

# ── Save ────────────────────────────────────────────────
result.to_csv("allocation_result.csv", index=False)
summary.to_csv("allocation_summary.csv", index=False)
print(f"\n✅ Saved: allocation_result.csv ({len(result):,} rows)")
print(f"✅ Saved: allocation_summary.csv ({len(summary):,} rows)")
