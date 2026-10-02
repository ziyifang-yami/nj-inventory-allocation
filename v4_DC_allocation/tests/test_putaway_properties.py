# Feature: nj-inventory-allocation-v4, Property 11: Putaway Decision Tree Exhaustiveness

"""
Property-based tests for the Putaway Decision Engine.

**Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8**
"""

import sys
import os

# Ensure the v4 package root is on the path so `from logic.putaway import …` works
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


@given(
    st.sampled_from(["TOP", "MIDDLE", "TAIL"]),
    st.floats(min_value=0, max_value=60, allow_nan=False),   # fc_d2s
    st.floats(min_value=0, max_value=500, allow_nan=False),  # sfc_qty
    st.integers(min_value=0, max_value=1),                   # need_minimal_exp
    st.one_of(st.none(), st.dates()),                         # new_exp_date (optional)
)
@settings(max_examples=100)
def test_putaway_exhaustiveness(seg, fc_d2s, sfc_qty, need_exp, new_exp_date):
    """
    Property 11: Putaway Decision Tree Exhaustiveness

    For any combination of segment, fc_d2s, sfc_qty, need_minimal_exp, and
    optional new_exp_date, decide_putaway() must:
    - return exactly one of {"FC", "SFC"} in putaway_zone
    - always populate putaway_rule with a non-empty string
    - always populate putaway_reason with a non-empty string

    **Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8**
    """
    from datetime import date
    sku_row = {
        "seg": seg,
        "fc_d2s": fc_d2s,
        "sfc_qty": sfc_qty,
        "need_minimal_exp": need_exp,
        "new_exp_date": new_exp_date,
        "sfc_exp_date": date(2025, 6, 1) if new_exp_date else None,
    }
    from logic.putaway import decide_putaway
    from config import P

    result = decide_putaway(sku_row, P)

    # The zone must be exactly one of the two valid values
    assert result["putaway_zone"] in {"FC", "SFC"}, (
        f"Expected FC or SFC, got {result['putaway_zone']!r} for input {sku_row}"
    )

    # Rule must be a non-empty string
    assert result["putaway_rule"] and isinstance(result["putaway_rule"], str), (
        f"putaway_rule must be a non-empty string, got {result['putaway_rule']!r}"
    )

    # Reason must be a non-empty string
    assert result["putaway_reason"] and isinstance(result["putaway_reason"], str), (
        f"putaway_reason must be a non-empty string, got {result['putaway_reason']!r}"
    )
