"""
logic/putaway.py — Putaway Decision Engine
NJ Inventory Allocation v4

Assigns each inbound receipt to FC or SFC based on segment,
FC D2S, SFC stock state, and expiration date comparison.

Decision tree (confirmed 2026-10-01):

    TOP  → FC  (putaway_rule="top_fc")

    TAIL → SFC (putaway_rule="tail_sfc")

    MIDDLE:
      1. need_minimal_exp=1 AND new_exp < MAX(fc_exp)
         → FC  (putaway_rule="mid_exp_worse_fc")
         新货保质期比FC最好那批更差，进FC先出（FIFO）

      2. No EXP trigger (need_minimal_exp=0 OR new_exp >= MAX(fc_exp)):
         a. fc_d2s > putaway_d2s (default 28)
            → SFC (putaway_rule="mid_fc_sufficient")
         b. fc_d2s <= putaway_d2s AND sfc_qty > 0
            → SFC (putaway_rule="mid_sfc_has_stock")
         c. fc_d2s <= putaway_d2s AND sfc_qty == 0
            → FC  (putaway_rule="mid_sfc_empty")

Requirements: 8.1 – 8.9
"""

import math
import pandas as pd


def _safe_float(val, default=0.0) -> float:
    """Return val as float; return default if NaN, None, or unconvertible."""
    try:
        f = float(val)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _get(row, key, default=None):
    """Unified field accessor for both dict-like rows and pandas Series."""
    if hasattr(row, "get"):
        return row.get(key, default)
    return getattr(row, key, default)


def decide_putaway(sku_row, config: dict) -> dict:
    """
    Decide putaway zone for a single inbound SKU row.

    Parameters
    ----------
    sku_row : dict-like (row from a DataFrame or plain dict)
        Required fields:
          seg              – "TOP", "MIDDLE", or "TAIL"
          fc_d2s           – days-to-sell in FC (float; NaN treated as 0)
          sfc_qty          – current SFC quantity (float; 0 if missing)
          need_minimal_exp – 0 or 1 flag
          new_exp_date     – new inbound item expiry date (date/datetime or None)
          fc_max_exp_date  – MAX exp_date across all FC lots for this SKU
                             (date/datetime or None)
    config : dict
        Expected key: putaway_d2s (default 28)

    Returns
    -------
    dict with keys: putaway_zone, putaway_rule, putaway_reason
    """
    putaway_d2s = config.get("putaway_d2s", 28)

    seg      = _get(sku_row, "seg", "")
    fc_d2s   = _safe_float(_get(sku_row, "fc_d2s", 0))
    sfc_qty  = _safe_float(_get(sku_row, "sfc_qty", 0))
    need_exp = int(_get(sku_row, "need_minimal_exp", 0) or 0)
    new_exp  = _get(sku_row, "new_exp_date", None)
    fc_max_exp = _get(sku_row, "fc_max_exp_date", None)

    # ── Branch 1: TOP ──────────────────────────────────────────────────────────
    if seg == "TOP":
        return {
            "putaway_zone": "FC",
            "putaway_rule": "top_fc",
            "putaway_reason": "TOP SKU — always stock in FC",
        }

    # ── Branch 2: BOTTOM / TAIL ────────────────────────────────────────────────
    if seg in ("TAIL", "BOTTOM"):
        return {
            "putaway_zone": "SFC",
            "putaway_rule": "tail_sfc",
            "putaway_reason": "TAIL/BOTTOM SKU — always stock in SFC",
        }

    # ── Branch 3: MIDDLE ───────────────────────────────────────────────────────

    # Step 1 — EXP check (must come before D2S check)
    # Trigger: need_minimal_exp=1 AND new item's exp is WORSE than FC's best batch
    # new_exp < fc_max_exp → new item expires sooner than FC's freshest lot → go to FC for FIFO
    exp_trigger = (
        need_exp == 1
        and new_exp is not None
        and fc_max_exp is not None
        and new_exp < fc_max_exp
    )
    if exp_trigger:
        return {
            "putaway_zone": "FC",
            "putaway_rule": "mid_exp_worse_fc",
            "putaway_reason": "新货保质期比FC最好那批更差，进FC先出（FIFO）",
        }

    # Step 2 — D2S check (no EXP trigger: need_minimal_exp=0, or new_exp >= fc_max_exp)
    # 2a: FC already has sufficient stock
    if fc_d2s > putaway_d2s:
        return {
            "putaway_zone": "SFC",
            "putaway_rule": "mid_fc_sufficient",
            "putaway_reason": f"FC库存充足(D2S>{putaway_d2s})，新货入SFC",
        }

    # 2b: FC needs replenishment but SFC still has stock — wait for T2 transfer
    if sfc_qty > 0:
        return {
            "putaway_zone": "SFC",
            "putaway_rule": "mid_sfc_has_stock",
            "putaway_reason": "FC需补货但SFC有货，新货入SFC等T2补回",
        }

    # 2c: FC needs replenishment and SFC is empty — stock FC directly
    return {
        "putaway_zone": "FC",
        "putaway_rule": "mid_sfc_empty",
        "putaway_reason": "FC需补货且SFC无货，新货直接入FC",
    }


def run_putaway(
    df_inbound: pd.DataFrame,
    df_inventory: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """
    Apply the putaway decision tree to all inbound rows.

    Parameters
    ----------
    df_inbound : DataFrame
        Must contain at minimum: item_number, seg, fc_d2s,
        need_minimal_exp, new_exp_date
    df_inventory : DataFrame
        Must contain at minimum: item_number, sfc_qty, fc_max_exp_date
    config : dict
        Allocation configuration (see config.py P dict)

    Returns
    -------
    DataFrame with all original inbound columns plus:
        putaway_zone, putaway_rule, putaway_reason
    """
    df = df_inbound.merge(
        df_inventory[["item_number", "sfc_qty", "fc_max_exp_date"]],
        on="item_number",
        how="left",
    )

    # Fill missing inventory fields with safe defaults
    df["sfc_qty"] = df["sfc_qty"].fillna(0)
    df["fc_max_exp_date"] = df["fc_max_exp_date"].where(df["fc_max_exp_date"].notna(), None)

    results = df.apply(
        lambda row: decide_putaway(row, config),
        axis=1,
        result_type="expand",
    )
    df = pd.concat([df, results], axis=1)
    return df
