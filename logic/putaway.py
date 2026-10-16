"""
logic/putaway.py — Putaway Decision Engine
NJ Inventory Allocation v4

Assigns each inbound receipt to FC or SFC based on segment,
FC D2S, SFC stock state, and expiration date comparison.

Decision tree (updated 2026-10-14):

    TBD  → No Need To Change Zone (new product, hold at staging)

    TOP  → FC  (putaway_rule="TOP_FC")

    TAIL → SFC (putaway_rule="TAIL_SFC")

    MIDDLE:
      1. need_minimal_exp=1 AND new_exp < fc_min_exp
         → FC  (MID_EXP_EARLIER_FC) — new goods expire earlier than FC stock (FEFO)

      2. fc_d2s > putaway_d2s (default 30)
         → SFC (MID_FC_D2S_HIGH) — FC has sufficient stock

      3. sfc_qty > 0:
         a. need_minimal_exp=1 AND new_exp <= sfc_min_exp
            → FC  (MID_SFC_EXP_EARLIER_FC) — new goods expire earlier/equal to SFC stock (FEFO)
         b. otherwise
            → SFC (MID_SFC_HAS_STOCK) — SFC has stock, wait for T2 replenishment

      4. sfc_qty == 0
         → FC  (MID_SFC_EMPTY) — both FC and SFC low, put in FC directly

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

    seg        = _get(sku_row, "seg", "")
    fc_d2s     = _safe_float(_get(sku_row, "fc_d2s", 0))
    sfc_qty    = _safe_float(_get(sku_row, "sfc_qty", 0))
    need_exp   = int(_get(sku_row, "need_minimal_exp", 0) or 0)
    new_exp    = _get(sku_row, "new_exp_date", None)
    fc_min_exp = _get(sku_row, "fc_min_exp_date", None)  # oldest FC lot exp date

    # ── Branch 1: TBD — new products, hold at receiving zone ──────────────────
    if seg == "TBD":
        return {
            "putaway_zone": "No Need To Change Zone",
            "putaway_rule": "TBD_keep",
            "putaway_reason": "New product (<30d in stock) — hold at receiving zone until classified",
        }

    # ── Branch 2: TOP ──────────────────────────────────────────────────────────
    if seg == "TOP":
        return {
            "putaway_zone": "FC",
            "putaway_rule": "TOP_FC",
            "putaway_reason": "TOP SKU — always stock in FC",
        }

    # ── Branch 3: BOTTOM / TAIL ────────────────────────────────────────────────
    if seg in ("TAIL", "BOTTOM"):
        return {
            "putaway_zone": "SFC",
            "putaway_rule": "TAIL_SFC",
            "putaway_reason": "TAIL/BOTTOM SKU — always stock in SFC",
        }

    # ── Branch 4: MIDDLE ───────────────────────────────────────────────────────

    # Step 1 — EXP check
    exp_trigger = (
        need_exp == 1
        and new_exp is not None
        and fc_min_exp is not None
        and pd.Timestamp(new_exp) < pd.Timestamp(fc_min_exp)
    )
    if exp_trigger:
        return {
            "putaway_zone": "FC",
            "putaway_rule": "MID_EXP_EARLIER_FC",
            "putaway_reason": "New goods expire earlier than FC stock — put in FC first (FEFO)",
        }

    # Step 2 — D2S check (no EXP trigger)
    if fc_d2s > putaway_d2s:
        return {
            "putaway_zone": "SFC",
            "putaway_rule": "MID_FC_D2S_HIGH",
            "putaway_reason": f"FC has sufficient stock (D2S>{putaway_d2s}) — put new goods in SFC",
        }

    if sfc_qty > 0:
        # Exception: new goods expire earlier/equal to SFC's oldest lot → FC (FEFO)
        sfc_min_exp = _get(sku_row, "sfc_min_exp_date", None)
        if (need_exp == 1
                and new_exp is not None
                and sfc_min_exp is not None
                and pd.Timestamp(new_exp) <= pd.Timestamp(sfc_min_exp)):
            return {
                "putaway_zone": "FC",
                "putaway_rule": "MID_SFC_EXP_EARLIER_FC",
                "putaway_reason": "New goods expire earlier than SFC stock — put in FC first (FEFO)",
            }
        return {
            "putaway_zone": "SFC",
            "putaway_rule": "MID_SFC_HAS_STOCK",
            "putaway_reason": "FC needs replenishment but SFC has stock — put in SFC, wait for T2",
        }

    return {
        "putaway_zone": "FC",
        "putaway_rule": "MID_SFC_EMPTY",
        "putaway_reason": "FC needs replenishment and SFC is empty — put directly in FC",
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
        df_inventory[["item_number", "sfc_qty", "fc_min_exp_date", "sfc_min_exp_date"]],
        on="item_number",
        how="left",
    )

    # Fill missing inventory fields with safe defaults
    df["sfc_qty"] = df["sfc_qty"].fillna(0)
    df["fc_min_exp_date"]  = df["fc_min_exp_date"].where(df["fc_min_exp_date"].notna(), None)
    df["sfc_min_exp_date"] = df["sfc_min_exp_date"].where(df["sfc_min_exp_date"].notna(), None)

    results = df.apply(
        lambda row: decide_putaway(row, config),
        axis=1,
        result_type="expand",
    )
    df = pd.concat([df, results], axis=1)
    return df
