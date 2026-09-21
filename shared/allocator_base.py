"""
shared/allocator_base.py — 分配引擎核心逻辑
=============================================
D2S + Active Days 决策逻辑，不依赖任何特定分层方案。
V2/V3 只需提供各自的 MODES 字典和 segment 字段即可复用。
"""

import numpy as np
import pandas as pd

# Active days 阈值，低于此值视为不活跃
ACTIVE_DAYS_THRESHOLD = 5
LOOKBACK_DAYS         = 30


def classify_sku(row: pd.Series, modes: dict, default_mode: tuple) -> dict:
    """
    对单个 SKU 进行分配决策。

    Parameters
    ----------
    row          : DataFrame row，需包含 item_number, segment_key,
                   fc_qty, sfc_qty, avg_daily_orders, active_days, wms_status
    modes        : {segment_value: (mode_name, upper, lower, return_trigger)}
    default_mode : fallback tuple if segment not in modes

    Returns
    -------
    dict with allocation fields
    """
    seg          = row.get("segment_key", "")
    fc_qty       = float(row.get("fc_qty", 0) or 0)
    sfc_qty      = float(row.get("sfc_qty", 0) or 0)
    avg_daily    = float(row.get("avg_daily_orders", 0) or 0)
    active_days  = float(row.get("active_days", 0) or 0)
    wms_status   = int(row.get("wms_status", 0) or 0)

    try:
        seg_key = int(float(str(seg).strip())) if str(seg).strip() else None
    except (ValueError, TypeError):
        seg_key = None

    mode_name, upper, lower, return_trigger = (
        modes.get(seg_key, default_mode) if seg_key is not None else default_mode
    )

    fc_d2s        = round(fc_qty / avg_daily, 1) if avg_daily > 0 else np.nan
    target_fc_qty = round(avg_daily * upper, 1)  if avg_daily > 0 else 0.0

    action       = "KEEP"
    transfer_qty = 0.0
    reason       = ""

    # STEP 0: No active WMS record → KEEP (don't generate transfer for unknown locs)
    if wms_status == 0:
        reason = "no active WMS record"
        return _result(row, seg, mode_name, action, fc_qty, sfc_qty, fc_d2s,
                       active_days, avg_daily, target_fc_qty, transfer_qty, reason)

    # STEP 2: Inactive SKU → transfer all FC to SFC
    if active_days < ACTIVE_DAYS_THRESHOLD:
        if fc_qty > 0:
            action, transfer_qty = "TRANSFER_TO_SFC", fc_qty
            reason = f"active_days={active_days:.0f} < {ACTIVE_DAYS_THRESHOLD}"
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

    # STEP 5: No sales + has FC stock
    elif np.isnan(fc_d2s) and fc_qty > 0:
        action, transfer_qty = "TRANSFER_TO_SFC", fc_qty
        reason = "no sales in 30d, fc_qty>0"

    # STEP 5: Keep band
    else:
        action = "KEEP"
        reason = (f"fc_d2s={fc_d2s} within [{lower}, {upper}]"
                  if not np.isnan(fc_d2s) else "no sales history, fc=0")

    return _result(row, seg, mode_name, action, fc_qty, sfc_qty, fc_d2s,
                   active_days, avg_daily, target_fc_qty, transfer_qty, reason)


def _result(row, seg, mode, action, fc_qty, sfc_qty, fc_d2s,
            active_days, avg_daily, target_fc_qty, transfer_qty, reason):
    return {
        "item_number":      row.get("item_number"),
        "segment_key":      seg,
        "mode":             mode,
        "action":           action,
        "fc_qty":           fc_qty,
        "sfc_qty":          sfc_qty,
        "fc_d2s":           fc_d2s if not np.isnan(fc_d2s) else None,
        "active_days":      active_days,
        "avg_daily_orders": round(avg_daily, 2),
        "target_fc_qty":    target_fc_qty,
        "transfer_qty":     transfer_qty,
        "reason":           reason,
    }


def allocate(df: pd.DataFrame, modes: dict, default_mode: tuple) -> pd.DataFrame:
    """Run allocation on full DataFrame."""
    if df.empty:
        return pd.DataFrame()
    records = [classify_sku(row, modes, default_mode) for _, row in df.iterrows()]
    result  = pd.DataFrame(records)
    order   = {"TRANSFER_TO_SFC": 0, "RETURN_FROM_SFC": 1, "KEEP": 2}
    result["_sort"] = result["action"].map(order)
    result = (result.sort_values(["_sort", "segment_key", "fc_d2s"],
                                 ascending=[True, True, False])
              .drop(columns="_sort")
              .reset_index(drop=True))
    return result


def summarize(result: pd.DataFrame, segment_col: str = "segment_key") -> pd.DataFrame:
    """Aggregate by segment + mode + action."""
    if result.empty:
        return pd.DataFrame()
    return (
        result.groupby([segment_col, "mode", "action"])
        .agg(
            sku_count          = ("item_number",  "count"),
            total_fc_qty       = ("fc_qty",        "sum"),
            total_sfc_qty      = ("sfc_qty",       "sum"),
            total_transfer_qty = ("transfer_qty",  lambda x: x.abs().sum()),
        )
        .reset_index()
        .sort_values([segment_col, "action"])
    )
