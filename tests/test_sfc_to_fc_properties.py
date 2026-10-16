# Feature: nj-inventory-allocation-v4, Property 6: TOP SFC→FC Priority Ordering (P0 Before P1–P3)

"""
Property-based tests for SFC→FC engine (logic/sfc_to_fc.py).

Validates: Requirements 5.4, 5.5
"""

import pandas as pd
import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ─────────────────────────────────────────────────────────────────────────────
# Property 6: TOP SFC→FC Priority Ordering (P0 Before P1–P3)
# Validates: Requirements 5.4, 5.5
# ─────────────────────────────────────────────────────────────────────────────

@given(
    st.lists(
        st.fixed_dictionaries({
            "item_number": st.text(min_size=1, max_size=8),
            "sfc_qty": st.floats(min_value=1, max_value=500, allow_nan=False),
            "fc_qty": st.floats(min_value=0, max_value=1000, allow_nan=False),
            "avg_daily_orders": st.floats(min_value=0.1, max_value=50, allow_nan=False),
            "fc_d2s": st.floats(min_value=0, max_value=60, allow_nan=False),
            "seg": st.just("TOP"),
        }),
        min_size=2,
        max_size=30,
    )
)
@settings(max_examples=100)
def test_top_p0_before_p1_p3(skus):
    """
    **Validates: Requirements 5.4, 5.5**

    For any DataFrame of TOP SKUs with mixed fc_d2s values (some < 3, some >= 3):
    All P0 SKUs (fc_d2s < 3) must appear before all non-P0 SKUs in the output
    of build_top_queue.
    """
    # Ensure there's at least one P0 and one non-P0 to make the test meaningful
    has_p0 = any(s["fc_d2s"] < 3 for s in skus)
    has_non_p0 = any(s["fc_d2s"] >= 3 for s in skus)
    assume(has_p0 and has_non_p0)

    df = pd.DataFrame(skus)
    df["trigger"] = "TOP_REPLEN"
    df["transfer_qty"] = df["sfc_qty"]

    from logic.sfc_to_fc import build_top_queue
    result = build_top_queue(df)

    # Find the last P0 row and the first non-P0 row
    p0_indices = result[result["fc_d2s"] < 3].index.tolist()
    non_p0_indices = result[result["fc_d2s"] >= 3].index.tolist()

    if p0_indices and non_p0_indices:
        last_p0_pos = max(p0_indices)
        first_non_p0_pos = min(non_p0_indices)
        assert last_p0_pos < first_non_p0_pos, (
            f"P0 row at position {last_p0_pos} appears after non-P0 row at {first_non_p0_pos}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Feature: nj-inventory-allocation-v4, Property 10: Stock Location Case-Rounding Invariant
# Validates: Requirements 7.5
# ─────────────────────────────────────────────────────────────────────────────

@given(
    st.lists(
        st.fixed_dictionaries({
            "location_no": st.text(min_size=1, max_size=8),
            "qty": st.integers(min_value=1, max_value=300),
            "loc_type": st.just("Stock"),  # Only Stock locations
            "case_qty": st.integers(min_value=1, max_value=24),
            "need_minimal_exp": st.just(0),
        }),
        min_size=1, max_size=8,
    ),
    st.floats(min_value=1.0, max_value=20.0, allow_nan=False),  # adl
    st.floats(min_value=0.0, max_value=100.0, allow_nan=False),  # fc_qty
)
@settings(max_examples=100)
def test_stock_case_rounding_invariant(lots, adl, fc_qty):
    """
    **Validates: Requirements 7.5**

    For every Stock location selected by select_middle_sfc_locations:
      qty_to_transfer % case_qty == 0  and  qty_to_transfer > 0
    """
    import math
    from hypothesis import assume

    sfc_qty = sum(l["qty"] for l in lots)
    needed = math.ceil(adl * 28) - fc_qty
    assume(needed > 0)
    assume(sfc_qty >= needed)  # Focus on normal path, not universal rule

    df_lots = pd.DataFrame(lots)
    df_lots["item_number"] = "ROUND_SKU"
    df_lots["exp_date"] = pd.NaT
    df_lots["inv_age_days"] = 0

    sku_row = {
        "item_number": "ROUND_SKU",
        "avg_daily_orders": adl,
        "fc_qty": fc_qty,
        "sfc_qty": sfc_qty,
    }

    from logic.sfc_to_fc import select_middle_sfc_locations
    result = select_middle_sfc_locations(df_lots, sku_row)

    # Filter to only Stock rows
    stock_rows = result[result["loc_type"] == "Stock"] if not result.empty else result

    for _, row in stock_rows.iterrows():
        case_q = int(row["case_qty"])
        xfer = row["qty_to_transfer"]
        assert xfer > 0, f"qty_to_transfer must be > 0, got {xfer}"
        assert xfer % case_q == 0, (
            f"Stock loc case-rounding violated: {xfer} % {case_q} = {xfer % case_q}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Feature: nj-inventory-allocation-v4, Property 7: MIDDLE Router Exhaustiveness
# Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
# ─────────────────────────────────────────────────────────────────────────────

@given(
    st.floats(min_value=0, max_value=100, allow_nan=False),  # fc_d2s
    st.booleans(),  # exp_trigger: True means sfc_exp < fc_exp
    st.sampled_from(["Bin", "Stock", "Unknown", ""]),  # sfc_loc_type
    st.floats(min_value=0, max_value=30, allow_nan=False),  # mid_emergency_d2s
)
@settings(max_examples=100)
def test_middle_router_exhaustiveness(fc_d2s, exp_trigger, sfc_loc_type, mid_emergency_d2s):
    """
    **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6**

    For any (fc_d2s, exp_flag, sfc_loc_type) combination:
    - route_middle_sku() always returns exactly one of {"List1", "List2", "List3", "Dateswitch", "Keep"}
    - No input raises an exception or returns None
    """
    from hypothesis import assume
    assume(mid_emergency_d2s > 0)

    # Build exp dates that trigger or not
    import pandas as pd
    from datetime import datetime, timedelta
    if exp_trigger:
        sfc_exp = datetime(2025, 1, 1)
        fc_exp = datetime(2025, 6, 1)  # fc_exp is newer → sfc < fc → EXP trigger
    else:
        sfc_exp = None
        fc_exp = None

    sku_row = {
        "fc_d2s": fc_d2s,
        "sfc_exp_date": sfc_exp,
        "fc_exp_date": fc_exp,
        "sfc_loc_type": sfc_loc_type,
        "mid_emergency_d2s": mid_emergency_d2s,
    }

    from logic.sfc_to_fc import route_middle_sku
    result = route_middle_sku(sku_row)

    VALID = {"List1", "List2", "List3", "Dateswitch", "Keep"}
    assert result in VALID, f"Unexpected result: {result!r}"
    assert result is not None


# ─────────────────────────────────────────────────────────────────────────────
# Feature: nj-inventory-allocation-v4, Property 8: MIDDLE SFC→FC Transfer Quantity Sufficiency
# Validates: Requirements 7.2, 7.3
# ─────────────────────────────────────────────────────────────────────────────

@given(
    st.lists(
        st.fixed_dictionaries({
            "location_no": st.text(min_size=1, max_size=8),
            "qty": st.integers(min_value=1, max_value=300),
            "loc_type": st.sampled_from(["Bin", "Stock"]),
            "case_qty": st.integers(min_value=1, max_value=12),
            "need_minimal_exp": st.just(0),
        }),
        min_size=1, max_size=10,
    ),
    st.floats(min_value=1.0, max_value=30.0, allow_nan=False),  # adl
    st.floats(min_value=0.0, max_value=100.0, allow_nan=False),  # fc_qty
)
@settings(max_examples=100)
def test_middle_sfc_transfer_quantity_sufficiency(lots, adl, fc_qty):
    """
    **Validates: Requirements 7.2, 7.3**

    When sfc_qty >= needed, the greedy location selection must produce a total
    qty_to_transfer >= needed for every generated case.

    Note: Stock locations apply case-rounding (Req 7.5).  When all lots are
    Stock-typed with qty < case_qty, no case-aligned pick is possible even though
    sfc_qty >= needed — that edge case is a spec gap, not covered here.  We assume
    the lot set can provide at least `needed` in case-aligned picks.
    """
    import math
    from hypothesis import assume
    sfc_qty = sum(l["qty"] for l in lots)
    needed = math.ceil(adl * 28) - fc_qty
    assume(needed > 0)
    assume(sfc_qty >= needed)

    # Compute the case-aligned capacity: Bin lots contribute full qty;
    # Stock lots contribute floor(qty / case_qty) * case_qty whole cases.
    # Skip Stock lots that can't fill even one case.
    case_aligned_capacity = 0.0
    for l in lots:
        if l["loc_type"] == "Bin":
            case_aligned_capacity += l["qty"]
        else:
            whole = (l["qty"] // l["case_qty"]) * l["case_qty"]
            case_aligned_capacity += whole
    assume(case_aligned_capacity >= needed)

    df_lots = pd.DataFrame(lots)
    df_lots["item_number"] = "SFC_SKU"
    df_lots["exp_date"] = pd.NaT
    df_lots["inv_age_days"] = 0

    sku_row = {"item_number": "SFC_SKU", "avg_daily_orders": adl, "fc_qty": fc_qty, "sfc_qty": sfc_qty}

    from logic.sfc_to_fc import select_middle_sfc_locations
    result = select_middle_sfc_locations(df_lots, sku_row)

    total = result["qty_to_transfer"].sum() if not result.empty else 0
    assert total >= needed


# ─────────────────────────────────────────────────────────────────────────────
# Feature: nj-inventory-allocation-v4, Property 9: Universal Rule — Transfer All When SFC Insufficient
# Validates: Requirements 5.3, 7.4
# ─────────────────────────────────────────────────────────────────────────────

@given(
    st.lists(
        st.fixed_dictionaries({
            "location_no": st.text(min_size=1, max_size=8),
            "qty": st.integers(min_value=1, max_value=100),
            "loc_type": st.sampled_from(["Bin", "Stock"]),
            "case_qty": st.integers(min_value=1, max_value=6),
            "need_minimal_exp": st.just(0),
        }),
        min_size=1, max_size=10,
    ),
    st.floats(min_value=1.0, max_value=30.0, allow_nan=False),
    st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
)
@settings(max_examples=100)
def test_universal_rule_transfer_all_when_insufficient(lots, adl, fc_qty):
    """
    **Validates: Requirements 5.3, 7.4**

    When sfc_qty < needed, the universal rule must fire:
    - sum(qty_to_transfer) == sfc_qty  (all SFC inventory moved)
    - every row carries rule == "MIDDLE_SFC_Universal"
    """
    import math
    from hypothesis import assume
    sfc_qty = sum(l["qty"] for l in lots)
    needed = math.ceil(adl * 28) - fc_qty
    assume(needed > 0)
    assume(sfc_qty < needed)  # insufficient — universal rule triggers

    df_lots = pd.DataFrame(lots)
    df_lots["item_number"] = "INSUF_SKU"
    df_lots["exp_date"] = pd.NaT
    df_lots["inv_age_days"] = 0

    sku_row = {"item_number": "INSUF_SKU", "avg_daily_orders": adl, "fc_qty": fc_qty, "sfc_qty": sfc_qty}

    from logic.sfc_to_fc import select_middle_sfc_locations
    result = select_middle_sfc_locations(df_lots, sku_row)

    total = result["qty_to_transfer"].sum() if not result.empty else 0
    assert total == sfc_qty
    assert (result["rule"] == "MIDDLE_SFC_Universal").all()


# ─────────────────────────────────────────────────────────────────────────────
# Feature: nj-inventory-allocation-v4, Property 12: Task Pickup Location-No Re-sort
# Validates: Requirements 4.3, 5.6, 6.8
# ─────────────────────────────────────────────────────────────────────────────

@given(
    st.lists(
        st.fixed_dictionaries({
            "item_number": st.text(min_size=1, max_size=6),
            "location_no": st.text(min_size=1, max_size=10),
            "qty_to_transfer": st.integers(min_value=1, max_value=100),
        }),
        min_size=0, max_size=30,
    )
)
@settings(max_examples=100)
def test_task_pickup_location_resorted_asc(tasks):
    """
    **Validates: Requirements 4.3, 5.6, 6.8**

    For any task DataFrame with location_no strings:
    - reorder_by_location() output is sorted by location_no ASC regardless of input order
    - The number of rows is unchanged (no rows dropped)
    """
    df = pd.DataFrame(tasks)

    # Can come from either engine (fc_to_sfc or sfc_to_fc)
    from logic.sfc_to_fc import reorder_by_location
    if df.empty:
        result = reorder_by_location(df)
        assert result.empty
        return

    result = reorder_by_location(df)

    # Same number of rows
    assert len(result) == len(df)

    # Sorted ASC by location_no
    locs = result["location_no"].tolist()
    assert locs == sorted(locs), f"Not sorted: {locs}"
