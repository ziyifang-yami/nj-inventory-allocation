"""
planning/schedule.py — 30-Day Rolling Inventory Drawdown Schedule
==================================================================
Simulates 30 days of inventory movement starting from the ideal state
produced by ``run_ideal()``.  Each day, demand is applied to FC stock,
replenishment/eviction rules are checked, and the resulting quantities
are recorded.

This is a **simplified simulation** — no actual warehouse location
selection occurs.  Transfer direction is captured in ``triggered_transfer``
and the quantity moved in ``transfer_qty``.

Usage
-----
    from planning.schedule import build_schedule
    from config import P

    df_sched = build_schedule(df_ideal, config=P, days=30)

Output columns
--------------
    item_number, seg, day, fc_qty, sfc_qty, fc_d2s,
    triggered_transfer, transfer_qty
"""

import math
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_schedule(df_ideal: pd.DataFrame, config: dict, days: int = 30) -> pd.DataFrame:
    """Simulate *days* days of rolling inventory drawdown from ideal state.

    Parameters
    ----------
    df_ideal : pd.DataFrame
        Output of ``run_ideal()``.  Required columns:
        ``item_number``, ``seg``, ``fc_qty``, ``sfc_qty``,
        ``avg_daily_orders``, ``fc_d2s``
    config : dict
        Parameter dict (e.g. ``config.P``).  Keys used:
        ``top_trigger_d2s``, ``mid_routine_d2s``, ``mid_evict_d2s``
    days : int, optional
        Number of simulation days.  Default 30.

    Returns
    -------
    pd.DataFrame
        One row per (SKU × day) with columns:
        ``item_number``, ``seg``, ``day``, ``fc_qty``, ``sfc_qty``,
        ``fc_d2s``, ``triggered_transfer``, ``transfer_qty``
    """
    # --- Validate required columns ----------------------------------------
    required_cols = {"item_number", "seg", "fc_qty", "sfc_qty",
                     "avg_daily_orders", "fc_d2s"}
    missing = required_cols - set(df_ideal.columns)
    if missing:
        raise ValueError(f"df_ideal is missing required columns: {missing}")

    # --- Extract config parameters ----------------------------------------
    top_trigger_d2s: float  = float(config.get("top_trigger_d2s", 28))
    mid_routine_d2s: float  = float(config.get("mid_routine_d2s", 28))
    mid_evict_d2s:   float  = float(config.get("mid_evict_d2s",   30))

    # --- Initialise mutable state per SKU ----------------------------------
    # Work with numpy arrays for speed; one entry per SKU.
    item_numbers = df_ideal["item_number"].values
    segs         = df_ideal["seg"].values
    adls         = pd.to_numeric(df_ideal["avg_daily_orders"], errors="coerce").fillna(0.0).values
    fc_qtys      = pd.to_numeric(df_ideal["fc_qty"], errors="coerce").fillna(0.0).astype(float).values.copy()
    sfc_qtys     = pd.to_numeric(df_ideal["sfc_qty"], errors="coerce").fillna(0.0).astype(float).values.copy()

    n_skus = len(item_numbers)

    # --- Pre-allocate output list ------------------------------------------
    # Each day appends n_skus records; total rows = days × n_skus.
    records = []

    # --- Daily simulation loop --------------------------------------------
    for day in range(1, days + 1):
        for i in range(n_skus):
            seg = segs[i]
            adl = adls[i]
            fc  = fc_qtys[i]
            sfc = sfc_qtys[i]

            # Step 1 — Apply daily demand to FC (clip at 0)
            fc = max(0.0, fc - adl)

            # Step 2 — Recompute D2S
            if adl > 0:
                fc_d2s = fc / adl
            else:
                fc_d2s = float("nan")

            # Step 3 — Replenishment / eviction checks
            transfer_direction = ""
            transfer_qty       = 0.0

            d2s_val = fc_d2s if not math.isnan(fc_d2s) else 0.0  # treat nan→0 for trigger checks

            if seg == "TOP":
                # TOP: transfer ALL SFC → FC when fc_d2s drops below top_trigger_d2s
                if d2s_val < top_trigger_d2s and sfc > 0:
                    transfer_qty       = sfc
                    transfer_direction = "SFC→FC"

            elif seg == "MIDDLE":
                # MIDDLE SFC→FC: replenish up to mid_routine_d2s worth when below threshold
                if d2s_val < mid_routine_d2s and sfc > 0:
                    # How much FC needs to reach the target D2S?
                    if adl > 0:
                        needed = math.ceil(adl * mid_routine_d2s) - fc
                    else:
                        needed = 0.0
                    if needed > 0:
                        qty = min(needed, sfc)
                        transfer_qty       = qty
                        transfer_direction = "SFC→FC"

                # MIDDLE FC→SFC: evict excess when FC is too deep
                elif d2s_val > mid_evict_d2s:
                    if adl > 0:
                        keep_qty = math.ceil(adl * mid_evict_d2s)
                        excess   = fc - keep_qty
                    else:
                        # adl == 0 but fc_d2s would be nan; this branch only
                        # reached when adl==0 and d2s_val was defaulted to 0.
                        # Guard: if adl==0 and d2s check somehow fires, skip.
                        excess = 0.0
                    if excess > 0:
                        transfer_qty       = excess
                        transfer_direction = "FC→SFC"

            # TAIL — no active replenishment logic in the daily simulation;
            # ideal state has already cleared TAIL FC to SFC.

            # Step 4 — Apply transfers to quantities
            if transfer_direction == "SFC→FC":
                fc  += transfer_qty
                sfc -= transfer_qty
                sfc  = max(0.0, sfc)   # guard against floating-point underrun
            elif transfer_direction == "FC→SFC":
                fc  -= transfer_qty
                fc   = max(0.0, fc)    # guard
                sfc += transfer_qty

            # Step 5 — Recompute D2S post-transfer (what we record)
            if adl > 0:
                fc_d2s_post = fc / adl
            else:
                fc_d2s_post = float("nan")

            # Write back state
            fc_qtys[i]  = fc
            sfc_qtys[i] = sfc

            # Record this day's state for this SKU
            records.append({
                "item_number":        item_numbers[i],
                "seg":                seg,
                "day":                day,
                "fc_qty":             fc,
                "sfc_qty":            sfc,
                "fc_d2s":             fc_d2s_post,
                "triggered_transfer": transfer_direction,
                "transfer_qty":       transfer_qty,
            })

    # --- Build output DataFrame -------------------------------------------
    df_out = pd.DataFrame(records, columns=[
        "item_number", "seg", "day",
        "fc_qty", "sfc_qty", "fc_d2s",
        "triggered_transfer", "transfer_qty",
    ])

    return df_out
