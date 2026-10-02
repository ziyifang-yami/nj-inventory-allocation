"""
tests/test_fc_to_sfc_units.py — Unit tests for FC→SFC edge cases

Requirements covered: 2.3, 2.4, 3.3
"""

import math
import sys
import os

import pandas as pd
import pytest

# Make the v4_DC_allocation package importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.fc_to_sfc import (
    compute_fc_to_sfc,
    select_middle_fc_locations,
    select_tail_fc_locations,
)
from config import P


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sku(seg, fc_qty, fc_d2s, adl=1.0, item="SKU001", vol=0.0):
    """Build a minimal single-row SKU DataFrame."""
    return pd.DataFrame([{
        "item_number": item,
        "seg": seg,
        "fc_qty": fc_qty,
        "fc_d2s": fc_d2s,
        "avg_daily_orders": adl,
        "unit_volume_cbm": vol,
    }])


def _lot(item, location_no, qty, loc_type="Stock", exp_date=None, need_minimal_exp=0):
    """Build a single lot row dict."""
    return {
        "item_number": item,
        "location_no": location_no,
        "loc_type": loc_type,
        "qty": qty,
        "exp_date": exp_date,
        "need_minimal_exp": need_minimal_exp,
    }


# ── Req 3.3: single-location MIDDLE → select_middle_fc_locations returns empty ──

class TestMiddleSingleLocation:
    """Req 3.3: A MIDDLE SKU with exactly one FC location must NOT generate a transfer task."""

    def test_single_location_returns_empty_dataframe(self):
        df_lot = pd.DataFrame([_lot("SKU001", "A-01-01", qty=50)])
        sku_row = {
            "item_number": "SKU001",
            "avg_daily_orders": 2.0,
            "keep_qty": 60,
        }
        result = select_middle_fc_locations(df_lot, sku_row)
        assert result.empty, (
            "Req 3.3: single-location MIDDLE SKU must produce an empty DataFrame"
        )

    def test_single_location_columns_present(self):
        """Even when empty, the DataFrame should carry the expected columns."""
        df_lot = pd.DataFrame([_lot("SKU001", "A-01-01", qty=50)])
        sku_row = {"item_number": "SKU001", "avg_daily_orders": 1.0, "keep_qty": 30}
        result = select_middle_fc_locations(df_lot, sku_row)
        assert result.empty
        assert "location_no" in result.columns or len(result) == 0


# ── Req 2.3: MIDDLE SKU with fc_d2s <= 30 → compute_fc_to_sfc produces no task ──

class TestMiddleD2SThreshold:
    """Req 2.3: MIDDLE SKU does NOT trigger when fc_d2s <= mid_evict_d2s (30)."""

    @pytest.mark.parametrize("d2s", [0, 1, 15, 29, 30])
    def test_no_task_when_d2s_at_or_below_threshold(self, d2s):
        df = _sku(seg="MIDDLE", fc_qty=200, fc_d2s=d2s, adl=2.0)
        result = compute_fc_to_sfc(df, P)
        assert result.empty, (
            f"Req 2.3: MIDDLE SKU with fc_d2s={d2s} (≤ {P['mid_evict_d2s']}) "
            "must NOT produce a transfer task"
        )

    def test_threshold_uses_config_value(self):
        """Threshold comes from config, not a hard-coded constant."""
        threshold = P["mid_evict_d2s"]
        df = _sku(seg="MIDDLE", fc_qty=500, fc_d2s=threshold, adl=3.0)
        result = compute_fc_to_sfc(df, P)
        assert result.empty, "fc_d2s exactly equal to threshold must NOT trigger"


# ── Req 2.4: TAIL SKU with fc_qty = 0 → compute_fc_to_sfc produces no task ──

class TestTailZeroQty:
    """Req 2.4: TAIL SKU with fc_qty = 0 must NOT trigger a transfer task."""

    def test_tail_zero_qty_no_task(self):
        df = _sku(seg="TAIL", fc_qty=0, fc_d2s=0, adl=0.5)
        result = compute_fc_to_sfc(df, P)
        assert result.empty, (
            "Req 2.4: TAIL SKU with fc_qty=0 must produce an empty DataFrame"
        )

    def test_tail_negative_qty_no_task(self):
        """Negative quantity (data anomaly) must also not trigger."""
        df = _sku(seg="TAIL", fc_qty=-1, fc_d2s=0, adl=0.5)
        result = compute_fc_to_sfc(df, P)
        assert result.empty, "TAIL SKU with fc_qty < 0 must produce no task"


# ── Additional edge case 4: MIDDLE with fc_d2s = 31 (just above threshold) ──

class TestMiddleJustAboveThreshold:
    """fc_d2s one unit above mid_evict_d2s must trigger the MIDDLE_EVICT rule."""

    def test_d2s_31_triggers(self):
        threshold = P["mid_evict_d2s"]           # 30
        fc_d2s = threshold + 1                   # 31
        adl = 2.0
        fc_qty = math.ceil(adl * 30) + 10        # keep_qty + 10 excess
        df = _sku(seg="MIDDLE", fc_qty=fc_qty, fc_d2s=fc_d2s, adl=adl)
        result = compute_fc_to_sfc(df, P)
        assert not result.empty, (
            f"MIDDLE SKU with fc_d2s={fc_d2s} (> threshold {threshold}) must trigger"
        )
        assert result.iloc[0]["trigger"] == "MIDDLE_EVICT"

    def test_d2s_31_excess_qty_correct(self):
        threshold = P["mid_evict_d2s"]
        fc_d2s = threshold + 1
        adl = 2.0
        keep_qty = math.ceil(adl * 30)           # 60
        fc_qty = keep_qty + 25                   # 85
        df = _sku(seg="MIDDLE", fc_qty=fc_qty, fc_d2s=fc_d2s, adl=adl)
        result = compute_fc_to_sfc(df, P)
        assert not result.empty
        assert result.iloc[0]["excess_qty"] == 25
        assert result.iloc[0]["keep_qty"] == keep_qty


# ── Additional edge case 5: TAIL with fc_qty > 0 → qty_to_transfer = fc_qty ──

class TestTailPositiveQty:
    """TAIL SKU with fc_qty > 0 must trigger and excess_qty must equal fc_qty."""

    @pytest.mark.parametrize("fc_qty", [1, 10, 100, 999])
    def test_tail_triggers_with_positive_qty(self, fc_qty):
        df = _sku(seg="TAIL", fc_qty=fc_qty, fc_d2s=5, adl=0.5)
        result = compute_fc_to_sfc(df, P)
        assert not result.empty, f"TAIL SKU with fc_qty={fc_qty} must trigger"
        assert result.iloc[0]["trigger"] == "TAIL_CLEAR"
        assert result.iloc[0]["excess_qty"] == fc_qty, (
            "For TAIL, excess_qty must equal fc_qty (full clear)"
        )
        assert result.iloc[0]["keep_qty"] == 0


# ── Additional edge case 6: MIDDLE where all FC qty fits within keep_qty ──

class TestMiddleNoExcess:
    """When fc_qty <= keep_qty, excess_qty = 0 → no transfer task."""

    def test_fc_qty_equals_keep_qty_no_task(self):
        adl = 2.0
        keep_qty = math.ceil(adl * 30)           # 60
        fc_qty = keep_qty                        # exact match → excess = 0
        fc_d2s = P["mid_evict_d2s"] + 1         # trigger condition met
        df = _sku(seg="MIDDLE", fc_qty=fc_qty, fc_d2s=fc_d2s, adl=adl)
        result = compute_fc_to_sfc(df, P)
        assert result.empty, (
            "MIDDLE SKU with fc_qty == keep_qty must not produce a transfer task"
        )

    def test_fc_qty_below_keep_qty_no_task(self):
        adl = 3.0
        keep_qty = math.ceil(adl * 30)           # 90
        fc_qty = keep_qty - 10                   # 80 < 90
        fc_d2s = P["mid_evict_d2s"] + 5
        df = _sku(seg="MIDDLE", fc_qty=fc_qty, fc_d2s=fc_d2s, adl=adl)
        result = compute_fc_to_sfc(df, P)
        assert result.empty, (
            "MIDDLE SKU with fc_qty < keep_qty must not produce a transfer task"
        )


# ── Additional edge case 7: TAIL select_tail_fc_locations with 3 locations ──

class TestTailLocationSelection:
    """
    select_tail_fc_locations with multiple locations:
    - sorted by location_no ASC
    - qty_to_transfer == qty at every row
    """

    def _make_lots(self):
        return pd.DataFrame([
            _lot("SKU999", "C-03-01", qty=30),
            _lot("SKU999", "A-01-05", qty=15),
            _lot("SKU999", "B-02-10", qty=45),
        ])

    def test_returns_all_three_locations(self):
        result = select_tail_fc_locations(self._make_lots(), "SKU999")
        assert len(result) == 3

    def test_sorted_by_location_no_asc(self):
        result = select_tail_fc_locations(self._make_lots(), "SKU999")
        locs = result["location_no"].tolist()
        assert locs == sorted(locs), (
            "Req 3.5: locations must be sorted by location_no ASC"
        )

    def test_qty_to_transfer_equals_qty_for_each_row(self):
        result = select_tail_fc_locations(self._make_lots(), "SKU999")
        for _, row in result.iterrows():
            assert row["qty_to_transfer"] == row["qty"], (
                f"Location {row['location_no']}: qty_to_transfer must equal qty (full clear)"
            )

    def test_rule_label_is_tail_full_clear(self):
        result = select_tail_fc_locations(self._make_lots(), "SKU999")
        assert (result["rule"] == "TAIL_FullClear").all()

    def test_no_locations_returns_empty(self):
        df_lot = pd.DataFrame([_lot("OTHER_SKU", "A-01-01", qty=10)])
        result = select_tail_fc_locations(df_lot, "SKU999")
        assert result.empty
