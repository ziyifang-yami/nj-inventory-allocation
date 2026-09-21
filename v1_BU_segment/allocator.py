"""
NJ Inventory Allocation — Core Allocator
==========================================
根据 D2S + Active Selling Days 决定每个 SKU 的调拨动作。
逻辑见 LOGIC.md。
"""

import pandas as pd
import numpy as np

# ──────────────────────────────────────────────────────────
# Mode config: segment → (d2s_upper, d2s_lower, return_trigger)
# ──────────────────────────────────────────────────────────
MODES = {
    # segment value  : (mode_name,    upper, lower, return_trigger)
    "Star":     ("FC_PRIMARY", 45, 35, 35),
    "Hit":      ("FC_PRIMARY", 45, 35, 35),
    "Head":     ("FC_PRIMARY", 45, 35, 35),
    "Middle":   ("DUAL_WH",    35, 28, 10),
    "Longtail": ("DC_DIRECT",  28, 14,  7),
    "TBD":      ("DC_DIRECT",  28, 14,  7),
}

ACTIVE_DAYS_THRESHOLD = 5   # <5 → treat as inactive, transfer all
LOOKBACK_DAYS         = 30  # window for active_days and avg_daily_orders


def _mode(segment: str) -> tuple:
    """Return (mode_name, d2s_upper, d2s_lower, return_trigger) for a segment."""
    return MODES.get(segment, MODES["TBD"])


def _classify(row: pd.Series) -> dict:
    """
    Classify a single SKU and return action + quantities.

    Expected row fields:
      item_number, segment, fc_qty, sfc_qty,
      avg_daily_orders,   -- orders_30d / 30
      active_days         -- COUNT(DISTINCT day_id WHERE orders>0) last 30d
    """
    seg              = str(row.get("segment", "TBD") or "TBD").strip()
    fc_qty           = float(row.get("fc_qty", 0) or 0)
    sfc_qty          = float(row.get("sfc_qty", 0) or 0)
    avg_daily        = float(row.get("avg_daily_orders", 0) or 0)
    active_days      = float(row.get("active_days", 0) or 0)

    mode_name, upper, lower, return_trigger = _mode(seg)

    # D2S for FC (nan if no sales)
    fc_d2s = round(fc_qty / avg_daily, 1) if avg_daily > 0 else np.nan

    # Target FC qty at D2S upper limit
    target_fc_qty = round(avg_daily * upper, 1) if avg_daily > 0 else 0.0

    action        = "KEEP"
    transfer_qty  = 0.0
    reason        = ""

    # ── STEP 2: inactive SKU → transfer all FC to SFC ──────────
    if active_days < ACTIVE_DAYS_THRESHOLD:
        if fc_qty > 0:
            action       = "TRANSFER_TO_SFC"
            transfer_qty = fc_qty
            reason       = f"active_days={active_days:.0f} < {ACTIVE_DAYS_THRESHOLD}"
        else:
            action = "KEEP"
            reason = f"active_days={active_days:.0f} < {ACTIVE_DAYS_THRESHOLD}, fc already 0"

    # ── STEP 3: FC overstocked (D2S > upper) ───────────────────
    elif not np.isnan(fc_d2s) and fc_d2s > upper:
        action       = "TRANSFER_TO_SFC"
        transfer_qty = max(0.0, round(fc_qty - target_fc_qty, 0))
        reason       = f"fc_d2s={fc_d2s} > upper={upper}"

    # ── STEP 4: FC understocked (D2S < return trigger) ─────────
    elif not np.isnan(fc_d2s) and fc_d2s < return_trigger and sfc_qty > 0:
        needed       = max(0.0, round(target_fc_qty - fc_qty, 0))
        transfer_qty = -min(needed, sfc_qty)          # negative = SFC → FC
        action       = "RETURN_FROM_SFC" if transfer_qty < 0 else "KEEP"
        reason       = (f"fc_d2s={fc_d2s} < return_trigger={return_trigger}, "
                        f"returning {abs(transfer_qty):.0f} units to reach target={target_fc_qty:.0f}")

    # ── STEP 5: no sales history + no FC stock → no action ─────
    elif np.isnan(fc_d2s) and fc_qty == 0:
        action = "KEEP"
        reason = "no sales history, fc_qty=0"

    # ── STEP 5: no sales history but has FC stock → transfer ───
    elif np.isnan(fc_d2s) and fc_qty > 0:
        action       = "TRANSFER_TO_SFC"
        transfer_qty = fc_qty
        reason       = "no sales in 30d, fc_qty>0"

    # ── STEP 5: within keep band ────────────────────────────────
    else:
        action = "KEEP"
        reason = f"fc_d2s={fc_d2s} within [{lower}, {upper}]"

    return {
        "item_number":    row.get("item_number"),
        "segment":        seg,
        "mode":           mode_name,
        "action":         action,
        "fc_qty":         fc_qty,
        "sfc_qty":        sfc_qty,
        "fc_d2s":         fc_d2s if not np.isnan(fc_d2s) else None,
        "active_days":    active_days,
        "avg_daily_orders": round(avg_daily, 2),
        "target_fc_qty":  target_fc_qty,
        "transfer_qty":   transfer_qty,   # >0 = FC→SFC, <0 = SFC→FC
        "reason":         reason,
    }


def allocate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Run allocation logic on the full dataset.

    Input DataFrame must have columns:
      item_number, segment, fc_qty, sfc_qty,
      avg_daily_orders, active_days

    Returns DataFrame with all output fields from LOGIC.md.
    """
    if df.empty:
        return pd.DataFrame()

    records = [_classify(row) for _, row in df.iterrows()]
    result  = pd.DataFrame(records)

    # Sort: transfers first, then returns, then keeps
    order_map = {"TRANSFER_TO_SFC": 0, "RETURN_FROM_SFC": 1, "KEEP": 2}
    result["_sort"] = result["action"].map(order_map)
    result = result.sort_values(["_sort", "segment", "fc_d2s"], ascending=[True, True, False])
    result = result.drop(columns="_sort").reset_index(drop=True)

    return result


def summarize(result: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate allocation results by segment + action.
    Returns summary with SKU count, total transfer qty, total FC/SFC units.
    """
    if result.empty:
        return pd.DataFrame()

    summary = (
        result.groupby(["segment", "mode", "action"])
        .agg(
            sku_count          = ("item_number",    "count"),
            total_fc_qty       = ("fc_qty",         "sum"),
            total_sfc_qty      = ("sfc_qty",        "sum"),
            total_transfer_qty = ("transfer_qty",   lambda x: x.abs().sum()),
        )
        .reset_index()
    )
    return summary
