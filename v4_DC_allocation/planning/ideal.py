"""
planning/ideal.py — Ideal State Allocation Planner for NJ Inventory Allocation v4
==================================================================================
Computes the "ideal state" allocation across all SKUs — what transfers would be
needed if we could execute everything today with no daily cap.

This module is a callable refactor of v3's run_allocation_ideal.py with three key
v4 changes:
  1. No daily cap (v3's daily_cap_out / daily_cap_in removed entirely)
  2. v4 classifier defaults (ol_tail=4 applied upstream; this module uses the
     pre-computed ``seg`` column from df_skus)
  3. Delegates FC→SFC and TOP SFC→FC decisions to v4 logic engines instead of
     inline loops

Requirements covered: 1.1, 2.1, 2.2, 5.1, 8.1
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

try:
    # When running from project root (e.g. pytest from NJ_Inventory_Allocation/)
    from v4_DC_allocation.logic.fc_to_sfc import compute_fc_to_sfc
    from v4_DC_allocation.logic.sfc_to_fc import compute_top_sfc_to_fc
except ImportError:
    # When running from inside v4_DC_allocation/ (e.g. streamlit run app.py)
    from logic.fc_to_sfc import compute_fc_to_sfc
    from logic.sfc_to_fc import compute_top_sfc_to_fc


# ── Public API ────────────────────────────────────────────────────────────────

def run_ideal(
    df_skus: pd.DataFrame,
    df_inventory: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """
    Compute the ideal-state allocation for all SKUs.

    Determines what FC↔SFC transfers would be needed to bring every SKU to its
    target D2S coverage if executed without any daily volume cap.

    Parameters
    ----------
    df_skus : pd.DataFrame
        One row per SKU.
        Required columns: ``item_number``, ``seg``, ``avg_daily_orders``,
        ``unit_volume_cbm``

    df_inventory : pd.DataFrame
        One row per SKU.
        Required columns: ``item_number``, ``fc_qty``, ``sfc_qty``

    config : dict
        Parameter dict (from ``config.P``).  Relevant keys:
          - ``mid_evict_d2s``      (default 30)  — MIDDLE FC→SFC trigger
          - ``top_trigger_d2s``   (default 28)  — TOP SFC→FC trigger
          - ``mid_routine_d2s``   (default 28)  — MIDDLE SFC→FC trigger
          - ``mid_target_d2s``    (default 28)  — MIDDLE SFC→FC replenishment target
          - ``min_loc_qty``       (default 5)   — minimum excess qty before issuing task

    Returns
    -------
    pd.DataFrame
        Columns: ``item_number``, ``seg``, ``fc_qty``, ``sfc_qty``, ``fc_d2s``,
        ``action``, ``direction``, ``transfer_qty``, ``transfer_vol``,
        ``priority_score``
        Sorted by ``seg`` then ``priority_score`` ASC.

    Notes
    -----
    - ``action`` is one of ``"DC_TO_FC"``, ``"FC_TO_DC"``, or ``"KEEP"``
    - ``direction`` is one of ``"SFC→FC"``, ``"FC→SFC"``, or ``""``
    - ``priority_score`` lower = higher priority (0–3 for TOP urgent cases,
      ~100 for TAIL, ~500+ for MIDDLE evict, 999 for KEEP)
    """
    # ── Config defaults ───────────────────────────────────────────────────────
    mid_evict_d2s    = config.get("mid_evict_d2s",    30)
    top_trigger_d2s  = config.get("top_trigger_d2s",  28)
    mid_routine_d2s  = config.get("mid_routine_d2s",  28)
    mid_target_d2s   = config.get("mid_target_d2s",   28)
    min_loc_qty      = config.get("min_loc_qty",       5)

    # ── Step 1: Merge SKU attributes with inventory ───────────────────────────
    df_skus = df_skus.copy()
    df_skus["item_number"] = df_skus["item_number"].astype(str)

    df_inventory = df_inventory.copy()
    df_inventory["item_number"] = df_inventory["item_number"].astype(str)

    df = df_skus.merge(
        df_inventory[["item_number", "fc_qty", "sfc_qty"]],
        on="item_number",
        how="left",
    )

    # ── Step 2: Fill missing inventory with 0 ────────────────────────────────
    df["fc_qty"]  = pd.to_numeric(df["fc_qty"],  errors="coerce").fillna(0)
    df["sfc_qty"] = pd.to_numeric(df["sfc_qty"], errors="coerce").fillna(0)
    df["avg_daily_orders"] = pd.to_numeric(
        df["avg_daily_orders"], errors="coerce"
    ).fillna(0)
    df["unit_volume_cbm"] = pd.to_numeric(
        df["unit_volume_cbm"], errors="coerce"
    ).fillna(0)

    # ── Step 3: Compute fc_d2s ────────────────────────────────────────────────
    # NaN when adl = 0 (undefined velocity)
    df["fc_d2s"] = np.where(
        df["avg_daily_orders"] > 0,
        (df["fc_qty"] / df["avg_daily_orders"]).round(1),
        np.nan,
    )

    # ── Step 4: Apply transfer rules per segment ──────────────────────────────
    records = []

    for _, row in df.iterrows():
        sku    = str(row["item_number"])
        seg    = str(row.get("seg", "TAIL")).upper()
        fc     = float(row["fc_qty"])
        sfc    = float(row["sfc_qty"])
        adl    = float(row["avg_daily_orders"])
        vol    = float(row["unit_volume_cbm"])
        fc_d2s = row["fc_d2s"]

        action       = "KEEP"
        direction    = ""
        transfer_qty = 0.0
        priority     = 999.0

        # ── TOP ───────────────────────────────────────────────────────────────
        # SFC→FC: trigger when sfc_qty > 0 AND fc_d2s < top_trigger_d2s
        # Uses compute_top_sfc_to_fc logic: transfer full sfc_qty, no cap
        if seg == "TOP":
            if sfc > 0 and (pd.isna(fc_d2s) or fc_d2s < top_trigger_d2s):
                action       = "DC_TO_FC"
                direction    = "SFC→FC"
                transfer_qty = sfc  # full SFC quantity (Req 5.1)

                # Priority scoring mirrors build_top_queue logic (Req 5.4, 5.5)
                d2s_val = fc_d2s if not pd.isna(fc_d2s) else 0.0
                if d2s_val < 3:
                    priority = 0.0  # P0 emergency group
                else:
                    # P1–P3: lower fc_d2s = higher urgency
                    priority = 3.0 + d2s_val / 100.0

        # ── MIDDLE ────────────────────────────────────────────────────────────
        elif seg == "MIDDLE":
            if adl > 0:
                d2s = fc_d2s if not pd.isna(fc_d2s) else 0.0

                # SFC→FC: replenish when fc_d2s < mid_routine_d2s and SFC has stock
                # (Req 5.1 for MIDDLE portion; mirrors v3 routine SFC→FC trigger)
                if d2s < mid_routine_d2s and sfc > 0:
                    target_qty = math.ceil(adl * mid_target_d2s)
                    needed     = max(0.0, target_qty - fc)
                    if needed > 0:
                        xfer         = min(needed, sfc)
                        action       = "DC_TO_FC"
                        direction    = "SFC→FC"
                        transfer_qty = float(round(xfer, 0))
                        # Lower d2s = more urgent; larger volume = higher value
                        priority = d2s * 1000.0 - needed * vol

                # FC→SFC: evict excess stock when fc_d2s > mid_evict_d2s (Req 2.1)
                elif d2s > mid_evict_d2s:
                    keep_qty = math.ceil(adl * mid_evict_d2s)
                    excess   = fc - keep_qty
                    if excess > min_loc_qty:
                        action       = "FC_TO_DC"
                        direction    = "FC→SFC"
                        transfer_qty = float(round(excess, 0))
                        priority     = 500.0 + d2s  # higher d2s = more excess

        # ── TAIL ──────────────────────────────────────────────────────────────
        # FC→SFC: full clear of FC stock (Req 2.2)
        elif seg == "TAIL":
            if fc > 0:
                action       = "FC_TO_DC"
                direction    = "FC→SFC"
                transfer_qty = fc
                priority     = 100.0

        records.append({
            "item_number":    sku,
            "seg":            seg,
            "fc_qty":         fc,
            "sfc_qty":        sfc,
            "fc_d2s":         round(fc_d2s, 1) if not pd.isna(fc_d2s) else None,
            "action":         action,
            "direction":      direction,
            "transfer_qty":   transfer_qty,
            "transfer_vol":   round(transfer_qty * vol, 4),
            "priority_score": round(priority, 3),
        })

    # ── Step 5: Build result DataFrame ───────────────────────────────────────
    if not records:
        return pd.DataFrame(columns=[
            "item_number", "seg", "fc_qty", "sfc_qty", "fc_d2s",
            "action", "direction", "transfer_qty", "transfer_vol", "priority_score",
        ])

    result = (
        pd.DataFrame(records)
        .sort_values(["seg", "priority_score"])
        .reset_index(drop=True)
    )

    return result
