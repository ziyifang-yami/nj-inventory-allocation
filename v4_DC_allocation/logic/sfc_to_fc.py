"""
v4_DC_allocation/logic/sfc_to_fc.py

SFC → FC transfer plan engine.

Four task lists per design.md §3:

  T1  紧急补货  — TOP fc_d2s<3 (top_emergency)
                   MIDDLE fc_d2s<3 (mid_emergency)
                   MIDDLE SFC Bin, EXP trigger, sfc worse (mid_zone_fallback)

  T2  常规补货  — TOP fc_d2s≥3, no bad EXP (top_routine)
                   MIDDLE SFC Stock, fc_d2s<14, no bad EXP (mid_routine)

  T3  Bin→Stock — MIDDLE SFC Bin: no EXP trigger OR sfc exp >= fc min (mid_bin_to_stock)

  T4  DateSwitch — TOP SFC, EXP trigger, sfc worse (top_stock_to_fc)
                   MIDDLE SFC Stock, EXP trigger, sfc worse (mid_stock_to_fc)

EXP comparison (§3.2):
  sfc_lot_exp_date  vs  MIN(fc_exp_date)  per SKU
  sfc < fc_min → SFC is worse (older / closer to expiry)

Transfer qty rules (§3.7):
  TOP        : full sfc_qty
  MIDDLE T1/T2: ceil to case_qty toward mid_target_d2s; if sfc < needed → all
  MIDDLE T3/T4: full lot qty
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd


# ────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ────────────────────────────────────────────────────────────────────────────

def _ceil_to_case(qty: float, case_qty: int) -> int:
    """Round qty up to the nearest multiple of case_qty (min 1 case)."""
    case_qty = max(int(case_qty), 1)
    return max(case_qty, math.ceil(qty / case_qty) * case_qty)


def _is_sfc_worse(sfc_exp, fc_min_exp) -> bool:
    """
    True if sfc_exp < fc_min_exp (SFC lot expires sooner = worse quality).
    Returns False if either date is NaT/None.
    """
    if pd.isna(sfc_exp) or pd.isna(fc_min_exp):
        return False
    return pd.Timestamp(sfc_exp) < pd.Timestamp(fc_min_exp)


# ────────────────────────────────────────────────────────────────────────────
# Public helpers (also called directly by app.py)
# ────────────────────────────────────────────────────────────────────────────

def compute_top_sfc_to_fc(df_skus: pd.DataFrame) -> pd.DataFrame:
    """Return TOP SKUs that have sfc_qty > 0 (all get transferred)."""
    return df_skus[(df_skus["seg"] == "TOP") & (df_skus["sfc_qty"] > 0)].copy()


def build_top_queue(df_top: pd.DataFrame) -> pd.DataFrame:
    """Sort TOP SFC→FC queue: OL DESC → ADL DESC."""
    if df_top.empty:
        return df_top
    return df_top.sort_values(
        ["order_lines_30d", "avg_daily_orders"],
        ascending=[False, False],
    ).reset_index(drop=True)


def estimate_pending_counts(df_skus: pd.DataFrame, cfg: dict) -> dict:
    """
    Fast SKU-level estimate of pending SKU counts per task list.
    Does NOT need lot data — uses only fc_d2s, need_minimal_exp, seg, sfc_qty.

    Returns dict:
      {
        "T1": {"skus": int, "est_qty": float},
        "T2": {"skus": int, "est_qty": float},
        "T3": {"skus": int, "est_qty": float},  # Bin SKUs, rough estimate
        "T4": {"skus": int, "est_qty": float},  # EXP trigger SKUs
        "keep": {"skus": int, "est_qty": float},
      }

    Rules (conservative estimate, no lot data):
      TOP, fc_d2s < 3           → T1
      TOP, fc_d2s >= 3, no EXP  → T2
      TOP, EXP trigger           → could be T2 or T4 → count as T2 (conservative)
      MIDDLE, fc_d2s < 3         → T1
      MIDDLE, fc_d2s >= 3 and < 14, no EXP → T2
      MIDDLE, EXP trigger        → T1/T3/T4 (estimate as T1 if d2s<3 else T4)
      MIDDLE, fc_d2s >= 14, no EXP → keep
    """
    mid_emergency_d2s = cfg.get("mid_emergency_d2s", 3)
    mid_replen_d2s    = cfg.get("mid_replen_d2s", 14)

    counts = {k: {"skus": 0, "est_qty": 0.0} for k in ["T1", "T2", "T3", "T4", "keep"]}

    active = df_skus[df_skus["sfc_qty"] > 0].copy()
    d2s    = active.get("fc_d2s", pd.Series(float("nan"), index=active.index))
    d2s    = pd.to_numeric(d2s, errors="coerce").fillna(999.0)
    need_exp = active.get("need_minimal_exp", pd.Series(0, index=active.index)).fillna(0).astype(int)
    seg    = active["seg"]
    sfc_q  = active["sfc_qty"]
    adl    = active.get("avg_daily_orders", pd.Series(0.0, index=active.index)).fillna(0.0)
    target = cfg.get("mid_target_d2s", 28)

    # TOP
    top = active[seg == "TOP"]
    top_d2s     = d2s[seg == "TOP"]
    top_exp     = need_exp[seg == "TOP"]
    top_sfc_q   = sfc_q[seg == "TOP"]

    top_t1 = top[top_d2s < mid_emergency_d2s]
    top_t2 = top[(top_d2s >= mid_emergency_d2s) & (top_exp == 0)]
    top_t4 = top[(top_d2s >= mid_emergency_d2s) & (top_exp > 0)]

    counts["T1"]["skus"]    += len(top_t1)
    counts["T1"]["est_qty"] += float(top_sfc_q[top_d2s < mid_emergency_d2s].sum())
    counts["T2"]["skus"]    += len(top_t2)
    counts["T2"]["est_qty"] += float(top_sfc_q[(top_d2s >= mid_emergency_d2s) & (top_exp == 0)].sum())
    counts["T4"]["skus"]    += len(top_t4)
    counts["T4"]["est_qty"] += float(top_sfc_q[(top_d2s >= mid_emergency_d2s) & (top_exp > 0)].sum())

    # MIDDLE
    mid      = active[seg == "MIDDLE"]
    mid_d2s  = d2s[seg == "MIDDLE"]
    mid_exp  = need_exp[seg == "MIDDLE"]
    mid_sfc  = sfc_q[seg == "MIDDLE"]
    mid_adl  = adl[seg == "MIDDLE"]
    mid_fc   = active.loc[seg == "MIDDLE", "fc_qty"] if "fc_qty" in active.columns else pd.Series(0.0, index=mid.index)

    m_t1      = mid[mid_d2s < mid_emergency_d2s]
    m_t2      = mid[(mid_d2s >= mid_emergency_d2s) & (mid_d2s < mid_replen_d2s) & (mid_exp == 0)]
    m_t4_exp  = mid[(mid_d2s >= mid_emergency_d2s) & (mid_exp > 0)]
    m_keep    = mid[(mid_d2s >= mid_replen_d2s) & (mid_exp == 0)]

    # estimate transfer qty for MIDDLE T1/T2 (cap at sfc_qty)
    needed_t1 = (mid_adl[mid_d2s < mid_emergency_d2s] * target
                 - mid_fc.loc[mid_d2s < mid_emergency_d2s]).clip(lower=0)
    est_t1    = mid_sfc[mid_d2s < mid_emergency_d2s].clip(upper=needed_t1)

    mask_t2   = (mid_d2s >= mid_emergency_d2s) & (mid_d2s < mid_replen_d2s) & (mid_exp == 0)
    needed_t2 = (mid_adl[mask_t2] * target - mid_fc.loc[mask_t2]).clip(lower=0)
    est_t2    = mid_sfc[mask_t2].clip(upper=needed_t2)

    counts["T1"]["skus"]    += len(m_t1)
    counts["T1"]["est_qty"] += float(est_t1.sum())
    counts["T2"]["skus"]    += len(m_t2)
    counts["T2"]["est_qty"] += float(est_t2.sum())
    counts["T4"]["skus"]    += len(m_t4_exp)  # rough: EXP SKUs may go T1/T3/T4
    counts["T4"]["est_qty"] += float(mid_sfc[(mid_d2s >= mid_emergency_d2s) & (mid_exp > 0)].sum())
    counts["keep"]["skus"]  += len(m_keep)
    counts["keep"]["est_qty"] += 0.0

    return counts


def route_middle_sku(
    sku_row: pd.Series,
    sfc_locs: pd.DataFrame,   # already filtered to this SKU's lots
    fc_min_exp,
) -> dict[str, Any]:
    """
    Apply MIDDLE routing decision tree for one SKU.
    sfc_locs must already be pre-filtered to this SKU (passed from build_middle_lists).
    Returns dict: {lots, source_type, list_id}
    list_id ∈ {"T1", "T2", "T3", "T4", "keep"}
    """
    fc_d2s   = sku_row.get("fc_d2s", float("nan"))
    need_exp = int(sku_row.get("need_minimal_exp", 0) or 0)

    # sfc_locs is already this SKU's data — no further filter needed
    locs = sfc_locs
    if locs.empty:
        return {"lots": pd.DataFrame(), "source_type": "keep", "list_id": "keep"}

    bin_locs   = locs[locs["loc_type"] == "Bin"]
    stock_locs = locs[locs["loc_type"] == "Stock"]

    fc_d2s_val = fc_d2s if not pd.isna(fc_d2s) else 999.0

    # ── Condition 1: emergency (any loc_type) ────────────────
    if fc_d2s_val < 3:
        return {"lots": locs, "source_type": "mid_emergency", "list_id": "T1"}

    # ── Condition 2: EXP trigger, SFC Bin worse ─────────────
    if need_exp and not bin_locs.empty:
        worse_bins = bin_locs[bin_locs["exp_date"].apply(
            lambda e: _is_sfc_worse(e, fc_min_exp)
        )]
        if not worse_bins.empty:
            return {"lots": worse_bins, "source_type": "mid_zone_fallback", "list_id": "T1"}

    # ── Condition 3: SFC Bin, EXP OK or no EXP → T3 ────────
    if not bin_locs.empty:
        # Bins where EXP is fine (not worse) or no EXP management
        ok_bins = bin_locs[~bin_locs["exp_date"].apply(
            lambda e: _is_sfc_worse(e, fc_min_exp)
        )] if need_exp else bin_locs
        if not ok_bins.empty:
            return {"lots": ok_bins, "source_type": "mid_bin_to_stock", "list_id": "T3"}

    # ── Condition 4: SFC Stock, EXP trigger, worse → T4 ────
    if need_exp and not stock_locs.empty:
        worse_stock = stock_locs[stock_locs["exp_date"].apply(
            lambda e: _is_sfc_worse(e, fc_min_exp)
        )]
        if not worse_stock.empty:
            return {"lots": worse_stock, "source_type": "mid_stock_to_fc", "list_id": "T4"}

    # ── Condition 5: SFC Stock, fc_d2s < 14, EXP OK → T2 ──
    if not stock_locs.empty and fc_d2s_val < 14:
        ok_stock = stock_locs[~stock_locs["exp_date"].apply(
            lambda e: _is_sfc_worse(e, fc_min_exp)
        )] if need_exp else stock_locs
        if not ok_stock.empty:
            return {"lots": ok_stock, "source_type": "mid_routine", "list_id": "T2"}

    # ── Condition 6: keep ────────────────────────────────────
    return {"lots": pd.DataFrame(), "source_type": "keep", "list_id": "keep"}


def select_middle_sfc_locations(
    lots: pd.DataFrame,
    sku_row: pd.Series,
    source_type: str,
    cfg: dict,
) -> pd.DataFrame:
    """
    Compute qty_to_transfer for each SFC lot row.

    T1 mid_emergency / T2 mid_routine → case-round toward mid_target_d2s
    T3 mid_bin_to_stock / T4 mid_stock_to_fc / T1 mid_zone_fallback → full qty
    """
    if lots.empty:
        return pd.DataFrame()

    lots = lots.copy()

    if source_type in ("mid_bin_to_stock", "mid_stock_to_fc", "mid_zone_fallback"):
        lots["qty_to_transfer"] = lots["qty"]
        lots["rule"] = source_type
        # FEFO sort for EXP SKUs — earliest expiry first, then qty DESC
        need_exp = int(sku_row.get("need_minimal_exp", 0) or 0)
        if need_exp and "exp_date" in lots.columns:
            lots = lots.sort_values(
                ["exp_date", "qty"], ascending=[True, False], na_position="last"
            ).reset_index(drop=True)
        return lots

    # mid_emergency or mid_routine: case-rounding toward target
    fc_qty      = float(sku_row.get("fc_qty", 0) or 0)
    adl         = float(sku_row.get("avg_daily_orders", 0) or 0)
    # T1 emergency uses t1_mid_target_d2s if set, otherwise mid_target_d2s
    if source_type == "mid_emergency":
        target_d2s = cfg.get("t1_mid_target_d2s", cfg.get("mid_target_d2s", 28))
    else:
        target_d2s = cfg.get("mid_target_d2s", 28)
    needed      = max(0.0, math.ceil(adl * target_d2s) - fc_qty) if adl > 0 else 0.0

    sfc_total   = float(lots["qty"].sum())
    if sfc_total <= 0:
        return pd.DataFrame()

    if sfc_total <= needed:
        # Not enough SFC — transfer everything
        lots["qty_to_transfer"] = lots["qty"]
        lots["rule"] = source_type + "_all"
        return lots

    # Sort lots for greedy selection:
    # When need_minimal_exp=1 → FEFO first (exp_date ASC), then loc_type, then qty DESC
    # Otherwise              → loc_type first, then qty DESC
    need_exp = int(sku_row.get("need_minimal_exp", 0) or 0)

    if source_type == "mid_emergency":
        lots["_sort_key"] = lots["loc_type"].map({"Bin": 0, "Stock": 1})
    else:
        lots["_sort_key"] = 1  # stock only

    if need_exp and "exp_date" in lots.columns:
        # FEFO: earliest expiry first, then loc_type, then qty DESC
        lots = lots.sort_values(
            ["exp_date", "_sort_key", "qty"],
            ascending=[True, True, False],
            na_position="last",   # NaT (no exp date) goes last
        ).drop(columns=["_sort_key"]).reset_index(drop=True)
    else:
        lots = lots.sort_values(
            ["_sort_key", "qty"], ascending=[True, False]
        ).drop(columns=["_sort_key"]).reset_index(drop=True)

    rows_out = []
    cum = 0.0
    for _, lot in lots.iterrows():
        if cum >= needed:
            break
        remaining = needed - cum
        case_qty   = max(int(lot.get("case_qty", 1) or 1), 1)
        lot_qty    = float(lot["qty"])

        if lot_qty <= case_qty:
            # Smaller than one case → take whole lot
            xfer = lot_qty
        elif cum + lot_qty <= needed:
            # Full lot fits within needed
            xfer = lot_qty
        else:
            # Last lot: ceil to case_qty
            xfer = _ceil_to_case(remaining, case_qty)
            xfer = min(xfer, lot_qty)  # can't exceed lot qty

        row = lot.to_dict()
        row["qty_to_transfer"] = xfer
        row["rule"] = source_type
        rows_out.append(row)
        cum += xfer

    return pd.DataFrame(rows_out)


def build_middle_lists(
    df_sfc_lots: pd.DataFrame,
    df_skus: pd.DataFrame,
    cfg: dict,
    lots_by_item: dict | None = None,
    sku_offset: int = 0,
    sku_limit: int | None = None,
    target_lists: list | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Route all MIDDLE SKUs and return rows grouped by list_id.
    target_lists: if set, only route SKUs that would go to those lists.
    """
    _build = set(target_lists) if target_lists else {"T1", "T2", "T3", "T4"}
    lists: dict[str, list] = {"T1": [], "T2": [], "T3": [], "T4": []}

    mid_skus_all = df_skus[
        (df_skus["seg"] == "MIDDLE") & (df_skus["sfc_qty"] > 0)
    ]
    # Sort by priority before slicing: emergency first (d2s ASC), then OL DESC
    if "fc_d2s" in mid_skus_all.columns:
        mid_skus_all = mid_skus_all.sort_values(
            ["fc_d2s", "order_lines_30d"], ascending=[True, False]
        )
    if sku_limit is not None:
        mid_skus = mid_skus_all.iloc[sku_offset : sku_offset + sku_limit]
    else:
        mid_skus = mid_skus_all

    # Build index if not provided
    if lots_by_item is None:
        lots_by_item = {
            item: grp.reset_index(drop=True)
            for item, grp in df_sfc_lots.groupby("item_number", sort=False)
        }

    for _, sku_row in mid_skus.iterrows():
        item = str(sku_row["item_number"])
        fc_min_exp = sku_row.get("fc_min_exp_date", pd.NaT)

        # O(1) dict lookup instead of full-table boolean filter
        sku_lots = lots_by_item.get(item)
        if sku_lots is None or sku_lots.empty:
            continue

        route = route_middle_sku(sku_row, sku_lots, fc_min_exp)
        list_id     = route["list_id"]
        source_type = route["source_type"]
        lots        = route["lots"]

        if list_id == "keep" or lots.empty:
            continue

        if list_id not in _build:
            continue

        lot_rows = select_middle_sfc_locations(lots, sku_row, source_type, cfg)
        if lot_rows.empty:
            continue

        lot_rows["segment"]          = "MIDDLE"
        lot_rows["source_type"]      = source_type
        lot_rows["fc_d2s"]           = sku_row.get("fc_d2s")
        lot_rows["avg_daily_orders"] = sku_row.get("avg_daily_orders")
        lot_rows["need_minimal_exp"] = sku_row.get("need_minimal_exp", 0)

        lists[list_id].append(lot_rows)

    return {k: pd.concat(v, ignore_index=True) if v else pd.DataFrame()
            for k, v in lists.items()}


# ────────────────────────────────────────────────────────────────────────────
# Task list sort helpers  (§3.6)
# ────────────────────────────────────────────────────────────────────────────

def _fefo_aware_sort(df: pd.DataFrame, sort_cols: list, sort_asc: list) -> pd.DataFrame:
    """
    Apply sort_cols/sort_asc, but for rows where need_minimal_exp=1,
    prepend exp_date ASC before all other sort keys (FEFO first).
    Rows with need_minimal_exp=0 use sort_cols/sort_asc unchanged.
    Both groups are concatenated: EXP rows first, then non-EXP rows.
    """
    if df.empty:
        return df
    has_exp_col  = "need_minimal_exp" in df.columns
    has_date_col = "exp_date" in df.columns

    if not has_exp_col or not has_date_col:
        # No EXP data available — fall back to standard sort
        return df.sort_values(sort_cols, ascending=sort_asc).reset_index(drop=True) if sort_cols else df

    exp_mask = df["need_minimal_exp"].fillna(0).astype(int) == 1
    df_exp     = df[exp_mask].copy()
    df_no_exp  = df[~exp_mask].copy()

    if not df_exp.empty:
        # FEFO first, then the standard sort keys
        df_exp = df_exp.sort_values(
            ["exp_date"] + sort_cols,
            ascending=[True] + sort_asc,
            na_position="last",
        ).reset_index(drop=True)

    if not df_no_exp.empty and sort_cols:
        df_no_exp = df_no_exp.sort_values(
            sort_cols, ascending=sort_asc
        ).reset_index(drop=True)

    parts = [d for d in [df_exp, df_no_exp] if not d.empty]
    return pd.concat(parts, ignore_index=True) if parts else df


def _sort_t1(df: pd.DataFrame) -> pd.DataFrame:
    """T1: [EXP→FEFO first] OL DESC → ADL DESC → location_no ASC"""
    cols, asc = [], []
    for c in ["order_lines_30d", "avg_daily_orders"]:
        if c in df.columns:
            cols.append(c); asc.append(False)
    if "location_no" in df.columns:
        cols.append("location_no"); asc.append(True)
    return _fefo_aware_sort(df, cols, asc)


def _sort_t2_top(df: pd.DataFrame) -> pd.DataFrame:
    """T2 TOP: [EXP→FEFO first] OL DESC → ADL DESC → location_no ASC"""
    return _sort_t1(df)


def _sort_t2_middle(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """T2 MIDDLE: [EXP→FEFO first] fc_d2s ASC → batch_volume DESC → ADL DESC → location_no ASC"""
    df = df.copy()
    if "qty_to_transfer" in df.columns and "unit_volume_cbm" in df.columns:
        df["_batch_vol"] = df["qty_to_transfer"] * df["unit_volume_cbm"].fillna(0)
    else:
        df["_batch_vol"] = 0

    cols, asc = [], []
    if "fc_d2s" in df.columns:           cols.append("fc_d2s");          asc.append(True)
    if "_batch_vol" in df.columns:       cols.append("_batch_vol");       asc.append(False)
    if "avg_daily_orders" in df.columns: cols.append("avg_daily_orders"); asc.append(False)
    if "location_no" in df.columns:      cols.append("location_no");      asc.append(True)

    result = _fefo_aware_sort(df, cols, asc)
    return result.drop(columns=["_batch_vol"], errors="ignore").reset_index(drop=True)


def _sort_t34(df: pd.DataFrame) -> pd.DataFrame:
    """T3/T4: exp_date ASC → location_no ASC (already FEFO by nature)"""
    cols, asc = [], []
    if "exp_date" in df.columns:    cols.append("exp_date");     asc.append(True)
    if "location_no" in df.columns: cols.append("location_no");  asc.append(True)
    return df.sort_values(cols, ascending=asc).reset_index(drop=True) if cols else df


# ────────────────────────────────────────────────────────────────────────────
# Main entry point
# ────────────────────────────────────────────────────────────────────────────

def build_sfc_to_fc_lists(
    df_sfc_lots: pd.DataFrame,
    df_skus: pd.DataFrame,
    cfg: dict,
    sku_offset: int = 0,
    sku_limit: int | None = None,
    target_lists: list | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Build T1/T2/T3/T4 task lists.

    target_lists: if provided (e.g. ["T1"]), only build those lists.
                  None = build all 4.
    """
    _build = set(target_lists) if target_lists else {"T1", "T2", "T3", "T4"}
    result: dict[str, pd.DataFrame] = {"T1": pd.DataFrame(), "T2": pd.DataFrame(),
                                        "T3": pd.DataFrame(), "T4": pd.DataFrame()}

    if df_sfc_lots is None or df_sfc_lots.empty:
        return result

    # ── Pre-index lots ONCE — shared by TOP loop and build_middle_lists ──
    lots_by_item: dict[str, pd.DataFrame] = {
        item: grp.reset_index(drop=True)
        for item, grp in df_sfc_lots.groupby("item_number", sort=False)
    }

    # ── Attach need_minimal_exp to SFC lots from SKU metadata ─
    if "need_minimal_exp" not in df_sfc_lots.columns:
        need_exp_map = (
            df_skus[["item_number", "need_minimal_exp"]].drop_duplicates("item_number")
            if "need_minimal_exp" in df_skus.columns
            else pd.DataFrame(columns=["item_number", "need_minimal_exp"])
        )
        df_sfc_lots = df_sfc_lots.merge(need_exp_map, on="item_number", how="left")
        df_sfc_lots["need_minimal_exp"] = (
            df_sfc_lots["need_minimal_exp"].fillna(0).astype(int)
        )

    # ── TOP: build T1 (emergency) and T2 (routine) ───────────
    top_skus_all = df_skus[
        (df_skus["seg"] == "TOP") & (df_skus["sfc_qty"] > 0)
    ].copy()
    # Sort TOP by priority: emergency first (d2s ASC), then OL DESC
    if "fc_d2s" in top_skus_all.columns:
        top_skus_all = top_skus_all.sort_values(
            ["fc_d2s", "order_lines_30d"], ascending=[True, False]
        )
    if sku_limit is not None:
        top_skus = top_skus_all.iloc[sku_offset : sku_offset + sku_limit]
    else:
        top_skus = top_skus_all

    top_t1_rows: list[pd.DataFrame] = []
    top_t2_rows: list[pd.DataFrame] = []
    top_t4_rows: list[pd.DataFrame] = []

    for _, sku in top_skus.iterrows():
        item       = str(sku["item_number"])
        fc_d2s     = sku.get("fc_d2s", float("nan"))
        fc_min_exp = sku.get("fc_min_exp_date", pd.NaT)
        need_exp   = int(sku.get("need_minimal_exp", 0) or 0)

        locs = lots_by_item.get(item)       # reuse shared index
        if locs is None or locs.empty:
            continue

        fc_d2s_val = fc_d2s if not pd.isna(fc_d2s) else 999.0

        # T4: EXP trigger, SFC worse (any loc_type)
        if need_exp:
            worse = locs[locs["exp_date"].apply(lambda e: _is_sfc_worse(e, fc_min_exp))]
            ok    = locs[~locs["exp_date"].apply(lambda e: _is_sfc_worse(e, fc_min_exp))]
        else:
            worse = pd.DataFrame()
            ok    = locs

        if not worse.empty:
            w = worse.copy()
            # FEFO sort within worse lots — earliest exp first
            if "exp_date" in w.columns:
                w = w.sort_values("exp_date", ascending=True, na_position="last")
            w["qty_to_transfer"]  = w["qty"]
            w["segment"]          = "TOP"
            w["source_type"]      = "top_stock_to_fc"
            w["fc_d2s"]           = fc_d2s
            w["avg_daily_orders"] = sku.get("avg_daily_orders")
            w["order_lines_30d"]  = sku.get("order_lines_30d")
            w["rule"]             = "top_stock_to_fc"
            top_t4_rows.append(w)

        if ok.empty:
            continue

        ok = ok.copy()
        # FEFO sort within ok lots when EXP SKU — earliest exp first, then qty DESC
        if need_exp and "exp_date" in ok.columns:
            ok = ok.sort_values(
                ["exp_date", "qty"], ascending=[True, False], na_position="last"
            )

        # For T1 (fc_d2s < 3): cap qty at t1_top_target_d2s, case-rounded
        # For T2 (fc_d2s >= 3): transfer full SFC qty (no cap)
        adl_top    = float(sku.get("avg_daily_orders", 0) or 0)
        fc_qty_top = float(sku.get("fc_qty", 0) or 0)

        if fc_d2s_val < 3:
            _t1_target = cfg.get("t1_top_target_d2s")
            if _t1_target and adl_top > 0:
                needed_top = max(0.0, adl_top * _t1_target - fc_qty_top)
                total_sfc  = float(ok["qty"].sum())
                if 0 < needed_top < total_sfc:
                    # Greedy fill to needed_top, case-rounded per lot
                    rows_top, cum = [], 0.0
                    for _, lot in ok.iterrows():
                        if cum >= needed_top:
                            break
                        case_qty  = max(int(lot.get("case_qty", 1) or 1), 1)
                        lot_qty   = float(lot["qty"])
                        remaining = needed_top - cum
                        xfer = lot_qty if cum + lot_qty <= needed_top else min(_ceil_to_case(remaining, case_qty), lot_qty)
                        r = lot.to_dict()
                        r["qty_to_transfer"] = xfer
                        rows_top.append(r)
                        cum += xfer
                    ok = pd.DataFrame(rows_top)
                else:
                    ok["qty_to_transfer"] = ok["qty"]
            else:
                ok["qty_to_transfer"] = ok["qty"]
        else:
            ok["qty_to_transfer"] = ok["qty"]

        ok["avg_daily_orders"] = sku.get("avg_daily_orders")
        ok["order_lines_30d"]  = sku.get("order_lines_30d")
        ok["fc_d2s"]           = fc_d2s

        if fc_d2s_val < 3:
            if "T1" not in _build:
                continue
            ok["segment"]     = "TOP"
            ok["source_type"] = "top_emergency"
            ok["rule"]        = "top_emergency"
            top_t1_rows.append(ok)
        else:
            if "T2" not in _build:
                continue
            ok["segment"]     = "TOP"
            ok["source_type"] = "top_routine"
            ok["rule"]        = "top_routine"
            top_t2_rows.append(ok)

    # ── MIDDLE: build T1/T2/T3/T4 ────────────────────────────
    mid_lists = build_middle_lists(
        df_sfc_lots, df_skus, cfg,
        lots_by_item=lots_by_item,
        sku_offset=sku_offset,
        sku_limit=sku_limit,
        target_lists=list(_build),
    )

    # Merge unit_volume_cbm for T2 MIDDLE sort
    if "unit_volume_cbm" in df_skus.columns:
        vol_map = df_skus[["item_number", "unit_volume_cbm"]].drop_duplicates("item_number")
        for k in ("T1", "T2", "T3", "T4"):
            if not mid_lists[k].empty:
                mid_lists[k] = mid_lists[k].merge(vol_map, on="item_number", how="left")

    # ── Assemble and sort ─────────────────────────────────────
    def _concat(*dfs):
        parts = [d for d in dfs if d is not None and not d.empty]
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    t1_raw = _concat(
        pd.concat(top_t1_rows, ignore_index=True) if top_t1_rows else None,
        mid_lists["T1"],
    )
    t2_raw = _concat(
        pd.concat(top_t2_rows, ignore_index=True) if top_t2_rows else None,
        mid_lists["T2"],
    )
    t3_raw = mid_lists["T3"]
    t4_raw = _concat(
        pd.concat(top_t4_rows, ignore_index=True) if top_t4_rows else None,
        mid_lists["T4"],
    )

    result["T1"] = _sort_t1(t1_raw)       if not t1_raw.empty else pd.DataFrame()
    result["T3"] = _sort_t34(t3_raw)      if not t3_raw.empty else pd.DataFrame()
    result["T4"] = _sort_t34(t4_raw)      if not t4_raw.empty else pd.DataFrame()

    # T2: TOP portion sorted by OL/ADL, MIDDLE portion by D2S/vol
    if not t2_raw.empty:
        t2_top = t2_raw[t2_raw.get("segment", pd.Series()) == "TOP"] if "segment" in t2_raw.columns else pd.DataFrame()
        t2_mid = t2_raw[t2_raw.get("segment", pd.Series()) == "MIDDLE"] if "segment" in t2_raw.columns else t2_raw
        result["T2"] = _concat(
            _sort_t2_top(t2_top) if not t2_top.empty else None,
            _sort_t2_middle(t2_mid, cfg) if not t2_mid.empty else None,
        )

    return result
