# Feature: nj-inventory-allocation-v4, Property 5: MIDDLE SKU Queue Sort Ordering

"""
Property-based tests for logic.fc_to_sfc.build_fc_to_sfc_queue().

**Validates: Requirements 4.1**
"""

import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from logic.fc_to_sfc import build_fc_to_sfc_queue


class TestMiddleQueueSortOrdering:
    """Property 5: MIDDLE SKU Queue Sort Ordering.

    **Validates: Requirement 4.1**

    For any list of MIDDLE SKUs: no SKU with lower ``excess_vol`` appears before
    one with higher ``excess_vol``.  Ties on ``excess_vol`` are broken by
    ``avg_daily_orders`` DESC.

    - Req 4.1: MIDDLE queue primary sort is ``excess_vol`` DESC; secondary sort
      is ``avg_daily_orders`` DESC.
    """

    @given(
        st.lists(
            st.fixed_dictionaries({
                "item_number": st.text(min_size=1, max_size=8),
                "excess_vol": st.floats(min_value=0, max_value=1000.0, allow_nan=False),
                "avg_daily_orders": st.floats(min_value=0, max_value=100.0, allow_nan=False),
                "excess_qty": st.integers(min_value=1, max_value=500),
                "fc_qty": st.integers(min_value=1, max_value=1000),
                "trigger": st.just("MIDDLE_EVICT"),
                "seg": st.just("MIDDLE"),
            }),
            min_size=1,
            max_size=50,
        )
    )
    @settings(max_examples=100)
    def test_middle_queue_sort_ordering(self, skus):
        """MIDDLE queue is sorted excess_vol DESC, avg_daily_orders DESC.

        Validates: Requirement 4.1
        - Primary key: excess_vol DESC — no row with a lower excess_vol may
          appear before a row with a higher excess_vol.
        - Secondary key (tie-break): avg_daily_orders DESC — when two rows
          share the same excess_vol, the one with higher avg_daily_orders must
          appear first.
        """
        df_middle = pd.DataFrame(skus)
        df_tail = pd.DataFrame()  # empty — only testing MIDDLE ordering

        queue = build_fc_to_sfc_queue(df_middle, df_tail)

        middle_rows = queue[queue["queue_type"] == "MIDDLE_EVICT"].reset_index(drop=True)

        # INVARIANT: no SKU with lower excess_vol appears before one with higher excess_vol
        for i in range(len(middle_rows) - 1):
            vol_i = middle_rows.loc[i, "excess_vol"]
            vol_j = middle_rows.loc[i + 1, "excess_vol"]
            adl_i = middle_rows.loc[i, "avg_daily_orders"]
            adl_j = middle_rows.loc[i + 1, "avg_daily_orders"]

            if vol_i < vol_j:
                assert False, (
                    f"Row {i} (excess_vol={vol_i}) appears before row {i + 1} "
                    f"(excess_vol={vol_j}): violates DESC sort (Req 4.1)"
                )

            if vol_i == vol_j:
                # Tie: resolve by avg_daily_orders DESC
                assert adl_i >= adl_j, (
                    f"Tie on excess_vol={vol_i}: row {i} adl={adl_i} < "
                    f"row {i + 1} adl={adl_j}: violates secondary sort (Req 4.1)"
                )


# Feature: nj-inventory-allocation-v4, Property 4: FC→SFC TAIL Full-Clear Invariant

"""
Property-based test for logic.fc_to_sfc.select_tail_fc_locations().

**Validates: Requirements 2.2, 3.5**

For any TAIL SKU with fc_qty > 0:
  sum(qty_to_transfer across all selected locations) == fc_qty
"""


@given(
    st.lists(
        st.fixed_dictionaries({
            "location_no": st.text(min_size=1, max_size=8),
            "qty": st.integers(min_value=1, max_value=500),
            "loc_type": st.sampled_from(["Bin", "Stock"]),
        }),
        min_size=1,
        max_size=20,
    )
)
@settings(max_examples=100)
def test_tail_full_clear_invariant(lots):
    """TAIL SKU full-clear: sum(qty_to_transfer) == fc_qty.

    Validates: Requirements 2.2, 3.5
    - Req 2.2: TAIL SKUs trigger a full-clear transfer (fc_qty > 0).
    - Req 3.5: select_tail_fc_locations returns ALL FC lots for the SKU;
      qty_to_transfer == qty at every location.
    """
    from hypothesis import assume

    fc_qty = sum(l["qty"] for l in lots)
    assume(fc_qty > 0)

    df_lots = pd.DataFrame(lots)
    df_lots["item_number"] = "TAIL_SKU"
    df_lots["exp_date"] = pd.NaT
    df_lots["need_minimal_exp"] = 0

    from logic.fc_to_sfc import select_tail_fc_locations

    df_transfer = select_tail_fc_locations(df_lots, "TAIL_SKU")

    # INVARIANT: total transferred must equal total FC qty (full clear)
    assert not df_transfer.empty
    total_transferred = df_transfer["qty_to_transfer"].sum()
    assert total_transferred == fc_qty, (
        f"Expected full clear: {fc_qty}, got {total_transferred}"
    )
