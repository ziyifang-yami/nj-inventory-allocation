"""
logic/fc_to_sfc.py — FC → SFC Transfer Engine for NJ Inventory Allocation v4

Three responsibilities:
  1. Trigger + quantity calculation  (compute_fc_to_sfc)
  2. Location selection              (select_middle_fc_locations, select_tail_fc_locations)
  3. SKU queue building + pickup     (build_fc_to_sfc_queue, reorder_by_location)

Requirements covered: 2.1, 2.2, 2.3, 2.4, 3.1–3.5, 4.1, 4.2, 4.3
"""

from __future__ import annotations

import math

import pandas as pd


# ── Constants ─────────────────────────────────────────────────────────────────

# Columns returned by location-selection functions
# Order matches task spec (5.2): item_number, location_no, loc_type, qty_to_transfer, qty, exp_date, rule
_LOC_COLS = [
    "item_number",
    "location_no",
    "loc_type",
    "qty_to_transfer",
    "qty",
    "exp_date",
    "rule",
]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Trigger + quantity calculation (Task 5.1)
# ─────────────────────────────────────────────────────────────────────────────

def compute_fc_to_sfc(df_skus: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Identify SKUs that should transfer stock from FC to SFC and compute
    the excess / transfer quantities.

    Parameters
    ----------
    df_skus:
        One row per SKU.  Required columns:
          seg, fc_qty, fc_d2s, avg_daily_orders (adl alias)
        Optional columns: item_number, unit_volume_cbm
    config:
        Must contain key ``mid_evict_d2s`` (default 30).

    Returns
    -------
    DataFrame with one row per triggered SKU, adding columns:
      trigger       — "MIDDLE_EVICT" or "TAIL_CLEAR"
      excess_qty    — units to remove from FC
      keep_qty      — units to keep in FC (0 for TAIL)
      excess_vol    — excess_qty × unit_volume_cbm (0 if vol unavailable)

    Requirements: 2.1, 2.2, 2.3, 2.4
    """
    mid_evict_d2s = config.get("mid_evict_d2s", 30)

    df = df_skus.copy()

    # Normalise column names
    if "avg_daily_orders" not in df.columns and "adl" in df.columns:
        df["avg_daily_orders"] = df["adl"]

    rows = []
    for _, row in df.iterrows():
        seg    = str(row.get("seg", "")).upper()
        fc_qty = float(row.get("fc_qty", 0) or 0)
        fc_d2s = float(row.get("fc_d2s", 0) or 0)
        adl    = float(row.get("avg_daily_orders", 0) or 0)
        vol    = float(row.get("unit_volume_cbm", 0) or 0)

        if seg == "MIDDLE":
            # Req 2.1: trigger when fc_d2s > mid_evict_d2s
            if fc_d2s <= mid_evict_d2s:
                continue  # Req 2.3: no task
            keep_qty   = math.ceil(adl * 30)
            excess_qty = max(0, fc_qty - keep_qty)
            if excess_qty == 0:
                continue
            r = row.to_dict()
            r["trigger"]   = "MIDDLE_EVICT"
            r["excess_qty"] = excess_qty
            r["keep_qty"]   = keep_qty
            r["excess_vol"] = excess_qty * vol
            rows.append(r)

        elif seg == "TAIL":
            # Req 2.2: trigger when fc_qty > 0
            if fc_qty <= 0:
                continue  # Req 2.4: no task
            r = row.to_dict()
            r["trigger"]   = "TAIL_CLEAR"
            r["excess_qty"] = fc_qty
            r["keep_qty"]   = 0
            r["excess_vol"] = fc_qty * vol
            rows.append(r)
        # TOP SKUs are not handled here

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# 2a. MIDDLE location selection (Task 5.2)
# ─────────────────────────────────────────────────────────────────────────────

def select_middle_fc_locations(
    df_lot_fc: pd.DataFrame,
    sku_row,
) -> pd.DataFrame:
    """
    Select which FC lot locations to transfer to SFC for a MIDDLE SKU.

    Parameters
    ----------
    df_lot_fc:
        DataFrame with FC lot data for **one SKU** (pre-filtered by caller).
        Also accepts the full FC lot table — the function will filter to the
        SKU identified by ``sku_row["item_number"]``.
        Required columns: item_number, location_no, loc_type, qty, exp_date,
                          need_minimal_exp
    sku_row:
        Row from ``compute_fc_to_sfc()`` output (or any Series/dict).
        Must contain: item_number, avg_daily_orders (or adl), keep_qty
        (keep_qty = ceil(adl × 30); computed from adl when 0).

    Returns
    -------
    DataFrame of locations to transfer to SFC.
    Columns: item_number, location_no, loc_type, qty_to_transfer, qty, exp_date, rule

    Returns empty DataFrame when:
      - SKU has exactly one FC location  (Req 3.3)
      - No lots found for SKU

    Rule labels
    -----------
    - ``"MIDDLE_EXP_KeepNewest"``  — EXP path transfers (need_minimal_exp = 1)
    - ``"MIDDLE_NOEXP_KeepBin"``   — No-EXP path transfers (need_minimal_exp = 0)

    Requirements: 3.1, 3.2, 3.3, 3.4
    """
    sku = str(
        sku_row.get("item_number") if hasattr(sku_row, "get")
        else getattr(sku_row, "item_number", "")
    )
    df_sku = df_lot_fc[df_lot_fc["item_number"].astype(str) == sku].copy()

    if df_sku.empty:
        return pd.DataFrame(columns=_LOC_COLS)

    # Req 3.3: single location → no transfer task
    if len(df_sku) == 1:
        return pd.DataFrame(columns=_LOC_COLS)

    adl = float(
        (sku_row.get("avg_daily_orders") if hasattr(sku_row, "get")
         else getattr(sku_row, "avg_daily_orders", 0)) or 0
    )
    if adl == 0:
        adl = float(
            (sku_row.get("adl") if hasattr(sku_row, "get")
             else getattr(sku_row, "adl", 0)) or 0
        )

    keep_target = float(
        (sku_row.get("keep_qty") if hasattr(sku_row, "get")
         else getattr(sku_row, "keep_qty", None)) or 0
    )
    if keep_target == 0:
        keep_target = math.ceil(adl * 30)

    need_exp = int(df_sku["need_minimal_exp"].iloc[0]) if "need_minimal_exp" in df_sku.columns else 0

    # ── Sort order (Req 3.1 / 3.2) ───────────────────────────
    if need_exp:
        # EXP path: sort by exp_date ASC — oldest expiry first (keep these)
        # Req 3.1: accumulate until >= keep_qty; remaining → transfer to SFC
        df_sorted = df_sku.sort_values("exp_date", ascending=True, na_position="last").reset_index(drop=True)
        rule_label = "MIDDLE_EXP_KeepNewest"
    else:
        # No-EXP path: Bin before Stock, within type by qty DESC (Req 3.2)
        df_sku["_type_order"] = df_sku["loc_type"].map({"Bin": 0, "Stock": 1}).fillna(2)
        df_sorted = df_sku.sort_values(
            ["_type_order", "qty"], ascending=[True, False]
        ).drop(columns=["_type_order"]).reset_index(drop=True)
        rule_label = "MIDDLE_NOEXP_KeepBin"

    # ── Accumulate "keep in FC" until >= keep_target (Req 3.4) ───
    # The last location that causes cumulative to meet/exceed keep_target is
    # always kept whole — no partial splits (ceiling rule).
    cumulative = 0.0
    keep_indices = []
    for idx, loc_row in df_sorted.iterrows():
        if cumulative >= keep_target:
            break
        keep_indices.append(idx)
        cumulative += float(loc_row["qty"])

    # Transfer = all rows NOT in keep set
    transfer_mask = ~df_sorted.index.isin(keep_indices)
    df_transfer = df_sorted[transfer_mask].copy()

    if df_transfer.empty:
        return pd.DataFrame(columns=_LOC_COLS)

    # Req 5 (task spec): qty_to_transfer = full lot qty (whole locations only)
    df_transfer["qty_to_transfer"] = df_transfer["qty"]
    df_transfer["rule"] = rule_label

    return df_transfer[_LOC_COLS].reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# 2b. TAIL location selection (Task 5.3)
# ─────────────────────────────────────────────────────────────────────────────

def select_tail_fc_locations(
    df_lot_fc: pd.DataFrame,
    sku: str,
) -> pd.DataFrame:
    """
    Select all FC lot locations for a TAIL SKU (full clear).

    All FC lots for the given SKU are included, sorted by ``location_no`` ASC.
    ``qty_to_transfer`` equals the full lot quantity at every location.

    Parameters
    ----------
    df_lot_fc:
        FC lot DataFrame (from ``load_fc_lot_data()``).
        Required columns: item_number, location_no, loc_type, qty, exp_date
    sku:
        The SKU (item_number) to process.

    Returns
    -------
    DataFrame with columns: item_number, location_no, loc_type, qty,
                             qty_to_transfer, exp_date, rule
    Returns empty DataFrame when no FC lots are found for the SKU.

    Requirements: 3.5 (all locations, sorted location_no ASC, full clear)
    """
    df_sku = df_lot_fc[df_lot_fc["item_number"].astype(str) == str(sku)].copy()

    if df_sku.empty:
        return pd.DataFrame(columns=_LOC_COLS)

    # Req 3.5: sort by location_no ASC
    df_sku = df_sku.sort_values("location_no", ascending=True).reset_index(drop=True)

    # Full clear: qty_to_transfer = qty
    df_sku["qty_to_transfer"] = df_sku["qty"]
    df_sku["rule"] = "TAIL_FullClear"

    return df_sku[_LOC_COLS].reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# 3. SKU queue builder (Task 5.3)
# ─────────────────────────────────────────────────────────────────────────────

def build_fc_to_sfc_queue(
    df_middle: pd.DataFrame,
    df_tail: pd.DataFrame,
    df_stats: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Combine MIDDLE and TAIL triggered SKUs into a priority-sorted task queue.

    MIDDLE queue (Req 4.1):
      - Primary sort:   ``excess_vol`` DESC
      - Secondary sort: ``avg_daily_orders`` DESC
      If ``excess_vol`` is missing, it is computed as
      ``excess_qty × unit_volume_cbm`` (falling back to ``excess_qty`` when
      ``unit_volume_cbm`` is unavailable).

    TAIL queue (Req 4.2):
      - Sorted by ``fc_qty`` DESC

    Each row gets a ``queue_type`` column: "MIDDLE_EVICT" or "TAIL_CLEAR".

    Parameters
    ----------
    df_middle:
        MIDDLE triggered SKUs from ``compute_fc_to_sfc()``.
        Expected columns include ``excess_vol``, ``avg_daily_orders``.
    df_tail:
        TAIL triggered SKUs from ``compute_fc_to_sfc()``.
        Expected column: ``fc_qty``.
    df_stats:
        Optional SKU stats from ``load_sku_loc_stats()`` with columns
        ``item_number``, ``total_vol``, ``loc_count``.
        Not used in sorting directly but preserved for downstream use.

    Returns
    -------
    Combined DataFrame with ``queue_type`` column.
    MIDDLE rows appear first (sorted by excess_vol DESC, avg_daily_orders DESC),
    followed by TAIL rows (sorted by fc_qty DESC).

    Requirements: 4.1, 4.2
    """
    parts = []

    # ── MIDDLE queue ─────────────────────────────────────────
    if df_middle is not None and not df_middle.empty:
        dm = df_middle.copy()

        # Ensure excess_vol exists (Req 4.1 note on volume)
        if "excess_vol" not in dm.columns or dm["excess_vol"].isna().all():
            if "excess_qty" in dm.columns and "unit_volume_cbm" in dm.columns:
                dm["excess_vol"] = dm["excess_qty"] * dm["unit_volume_cbm"].fillna(0)
            elif "excess_qty" in dm.columns:
                dm["excess_vol"] = dm["excess_qty"]  # fallback: no volume
            else:
                dm["excess_vol"] = 0.0
        else:
            dm["excess_vol"] = dm["excess_vol"].fillna(0)

        if "avg_daily_orders" not in dm.columns:
            dm["avg_daily_orders"] = 0.0
        else:
            dm["avg_daily_orders"] = dm["avg_daily_orders"].fillna(0)

        # Merge optional stats if provided
        if df_stats is not None and not df_stats.empty and "item_number" in dm.columns:
            dm = dm.merge(
                df_stats[["item_number", "total_vol", "loc_count"]].drop_duplicates("item_number"),
                on="item_number",
                how="left",
                suffixes=("", "_stats"),
            )

        # Sort: excess_vol DESC, avg_daily_orders DESC (Req 4.1)
        dm = dm.sort_values(
            ["excess_vol", "avg_daily_orders"],
            ascending=[False, False],
        ).reset_index(drop=True)

        dm["queue_type"] = "MIDDLE_EVICT"
        parts.append(dm)

    # ── TAIL queue ───────────────────────────────────────────
    if df_tail is not None and not df_tail.empty:
        dt = df_tail.copy()

        if "fc_qty" not in dt.columns:
            dt["fc_qty"] = 0.0
        else:
            dt["fc_qty"] = dt["fc_qty"].fillna(0)

        # Sort: fc_qty DESC (Req 4.2)
        dt = dt.sort_values("fc_qty", ascending=False).reset_index(drop=True)

        dt["queue_type"] = "TAIL_CLEAR"
        parts.append(dt)

    if not parts:
        return pd.DataFrame()

    return pd.concat(parts, ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Task pickup location re-sort (Task 5.3)
# ─────────────────────────────────────────────────────────────────────────────

def reorder_by_location(df_tasks: pd.DataFrame) -> pd.DataFrame:
    """
    Re-sort a task DataFrame by ``location_no`` ASC for sequential field execution.

    Called during Task_Pickup after the operator selects the top-N SKUs from
    the priority queue.  The re-sort collapses the queue ordering so that
    warehouse workers can walk the aisles in location order.

    Parameters
    ----------
    df_tasks:
        Task DataFrame (already filtered to top-N SKUs for pickup).
        Must contain a ``location_no`` column.

    Returns
    -------
    DataFrame sorted by ``location_no`` ASC.

    Requirements: 4.3
    """
    if df_tasks.empty or "location_no" not in df_tasks.columns:
        return df_tasks

    return df_tasks.sort_values("location_no", ascending=True).reset_index(drop=True)
