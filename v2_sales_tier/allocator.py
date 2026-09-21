"""
NJ Inventory Allocation V2 — WMS Sales Tier (group_tier) Based Allocator
==========================================================================
分层来源：yamibuy_wh.wh_lr_product_location.group_tier
这是 WMS 系统里基于日均销量（average_sales）划分的 SKU 分组，
数字越小 = 销量越高。

group_tier 含义（NJ=002 实测）：
  9  — Top 50 SKUs,   日均 94–721，FC_PRIMARY
  10 — Rank 51–200,  日均 50–112，FC_PRIMARY
  11 — Rank 201–450, 日均 17–50， FC_PRIMARY
  12 — Rank 451–950, 日均  7–27，  DUAL_WH
  13 — Rank 951–2450,日均  2–14，  DUAL_WH
  14 — Rank 2451–8850,日均 0.5–5，DC_DIRECT
  15 — Rank 8851+,   日均 0.06–1.3，DC_DIRECT
  16 — 无排名/极低频,  DC_DIRECT
  其他/空 — fallback → DC_DIRECT

逻辑见 LOGIC.md。
"""

import pandas as pd
import numpy as np

# ──────────────────────────────────────────────────────────
# group_tier → (mode_name, d2s_upper, d2s_lower, return_trigger)
# ──────────────────────────────────────────────────────────
MODES = {
    # group_tier : (mode_name,    upper, lower, return_trigger)
    9:  ("FC_PRIMARY", 45, 35, 35),   # Top 50,     日均 94–721
    10: ("FC_PRIMARY", 45, 35, 35),   # Rank 51–200, 日均 50–112
    11: ("FC_PRIMARY", 45, 35, 35),   # Rank 201–450,日均 17–50
    12: ("DUAL_WH",    35, 28, 10),   # Rank 451–950,日均 7–27
    13: ("DUAL_WH",    35, 28, 10),   # Rank 951–2450,日均 2–14
    14: ("DC_DIRECT",  28, 14,  7),   # Rank 2451–8850,日均 0.5–5
    15: ("DC_DIRECT",  28, 14,  7),   # Rank 8851+,  日均 <1.3
    16: ("DC_DIRECT",  28, 14,  7),   # 无排名/极低频
}
MODES_DEFAULT = ("DC_DIRECT", 28, 14, 7)  # fallback

ACTIVE_DAYS_THRESHOLD = 5    # <5 天 → 视为不活跃，触发全迁
LOOKBACK_DAYS         = 30   # active_days 和 avg_daily_orders 的计算窗口


def _mode(group_tier_raw) -> tuple:
    """Parse group_tier (int or str) and return mode config."""
    try:
        gt = int(float(str(group_tier_raw).strip()))
    except (ValueError, TypeError):
        gt = -1
    return MODES.get(gt, MODES_DEFAULT)


def _classify(row: pd.Series) -> dict:
    """
    Classify a single SKU.
    Expected row fields:
      item_number, group_tier,
      fc_qty, sfc_qty,
      avg_daily_orders,   -- orders_30d / 30
      active_days         -- COUNT(DISTINCT day_id WHERE orders>0) last 30d
    """
    group_tier       = row.get("group_tier", "")
    fc_qty           = float(row.get("fc_qty", 0) or 0)
    sfc_qty          = float(row.get("sfc_qty", 0) or 0)
    avg_daily        = float(row.get("avg_daily_orders", 0) or 0)
    active_days      = float(row.get("active_days", 0) or 0)
    wms_avg_sales    = float(row.get("wms_avg_sales", 0) or 0)  # from wh_lr_product_location
    wms_status       = int(row.get("wms_status", 0) or 0)       # 1=active in WMS, 0=not found

    mode_name, upper, lower, return_trigger = _mode(group_tier)

    # D2S for FC
    fc_d2s        = round(fc_qty / avg_daily, 1) if avg_daily > 0 else np.nan
    target_fc_qty = round(avg_daily * upper, 1) if avg_daily > 0 else 0.0

    action       = "KEEP"
    transfer_qty = 0.0
    reason       = ""

    # STEP 0: SKU not in active WMS records → KEEP, no transfer recommendation
    # These are SKUs with fc_qty but WMS doesn't actively manage their bin location
    if wms_status == 0:
        action = "KEEP"
        reason = "no active WMS record (status=0 or not in wh_lr_product_location)"
        return {
            "item_number":      row.get("item_number"),
            "group_tier":       group_tier,
            "mode":             mode_name,
            "action":           action,
            "fc_qty":           fc_qty,
            "sfc_qty":          sfc_qty,
            "fc_d2s":           fc_d2s if not np.isnan(fc_d2s) else None,
            "active_days":      active_days,
            "avg_daily_orders": round(avg_daily, 2),
            "wms_avg_sales":    wms_avg_sales,
            "target_fc_qty":    target_fc_qty,
            "transfer_qty":     0.0,
            "reason":           reason,
        }

    # STEP 2: inactive SKU → transfer all FC to SFC
    if active_days < ACTIVE_DAYS_THRESHOLD:
        if fc_qty > 0:
            action       = "TRANSFER_TO_SFC"
            transfer_qty = fc_qty
            reason       = f"active_days={active_days:.0f} < {ACTIVE_DAYS_THRESHOLD}"
        else:
            action = "KEEP"
            reason = f"active_days={active_days:.0f} < {ACTIVE_DAYS_THRESHOLD}, fc=0"

    # STEP 3: FC overstocked
    elif not np.isnan(fc_d2s) and fc_d2s > upper:
        action       = "TRANSFER_TO_SFC"
        transfer_qty = max(0.0, round(fc_qty - target_fc_qty, 0))
        reason       = f"fc_d2s={fc_d2s} > upper={upper}"

    # STEP 4: FC understocked
    elif not np.isnan(fc_d2s) and fc_d2s < return_trigger and sfc_qty > 0:
        needed       = max(0.0, round(target_fc_qty - fc_qty, 0))
        transfer_qty = -min(needed, sfc_qty)
        action       = "RETURN_FROM_SFC" if transfer_qty < 0 else "KEEP"
        reason       = (f"fc_d2s={fc_d2s} < return_trigger={return_trigger}, "
                        f"returning {abs(transfer_qty):.0f} to target={target_fc_qty:.0f}")

    # STEP 5: no sales history + has FC stock
    elif np.isnan(fc_d2s) and fc_qty > 0:
        action       = "TRANSFER_TO_SFC"
        transfer_qty = fc_qty
        reason       = "no sales in 30d, fc_qty>0"

    # STEP 5: keep band or nothing
    else:
        action = "KEEP"
        reason = (f"fc_d2s={fc_d2s} within [{lower}, {upper}]"
                  if not np.isnan(fc_d2s) else "no sales history, fc=0")

    return {
        "item_number":      row.get("item_number"),
        "group_tier":       group_tier,
        "mode":             mode_name,
        "action":           action,
        "fc_qty":           fc_qty,
        "sfc_qty":          sfc_qty,
        "fc_d2s":           fc_d2s if not np.isnan(fc_d2s) else None,
        "active_days":      active_days,
        "avg_daily_orders": round(avg_daily, 2),
        "wms_avg_sales":    wms_avg_sales,
        "wms_status":       wms_status,
        "target_fc_qty":    target_fc_qty,
        "transfer_qty":     transfer_qty,
        "reason":           reason,
    }


def allocate(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    records = [_classify(row) for _, row in df.iterrows()]
    result  = pd.DataFrame(records)
    order   = {"TRANSFER_TO_SFC": 0, "RETURN_FROM_SFC": 1, "KEEP": 2}
    result["_sort"] = result["action"].map(order)
    result  = result.sort_values(["_sort", "group_tier", "fc_d2s"],
                                 ascending=[True, True, False])
    return result.drop(columns="_sort").reset_index(drop=True)


def summarize(result: pd.DataFrame) -> pd.DataFrame:
    if result.empty:
        return pd.DataFrame()
    return (
        result.groupby(["group_tier", "mode", "action"])
        .agg(
            sku_count          = ("item_number",  "count"),
            total_fc_qty       = ("fc_qty",        "sum"),
            total_sfc_qty      = ("sfc_qty",       "sum"),
            total_transfer_qty = ("transfer_qty",  lambda x: x.abs().sum()),
        )
        .reset_index()
        .sort_values(["group_tier", "action"])
    )
