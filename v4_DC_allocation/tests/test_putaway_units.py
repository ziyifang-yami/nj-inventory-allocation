"""
Unit tests for the Putaway Decision Engine — EXP comparison branches and core rules.

Requirements: 8.1 – 8.9
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import date
from logic.putaway import decide_putaway
from config import P


# ── Helper ─────────────────────────────────────────────────────────────────────

def make_row(
    seg="MIDDLE",
    fc_d2s=10.0,
    sfc_qty=100.0,
    need_exp=0,
    new_exp_date=None,
    sfc_exp_date=None,
):
    return {
        "seg": seg,
        "fc_d2s": fc_d2s,
        "sfc_qty": sfc_qty,
        "need_minimal_exp": need_exp,
        "new_exp_date": new_exp_date,
        "sfc_exp_date": sfc_exp_date,
    }


# ── EXP comparison branches ────────────────────────────────────────────────────

class TestExpBranches:
    """Tests for expiration-date comparison logic (Req 8.7, 8.8)."""

    def test_new_item_fresher_goes_to_sfc(self):
        """
        Req 8.7: new_exp_date > sfc_exp_date → putaway_zone = SFC
        Fresher inbound should go to SFC so older SFC stock exits first (FIFO).
        """
        row = make_row(
            seg="MIDDLE",
            fc_d2s=10.0,
            sfc_qty=50.0,
            need_exp=1,
            new_exp_date=date(2025, 12, 31),   # fresher
            sfc_exp_date=date(2025, 6, 1),     # older
        )
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "SFC"
        assert result["putaway_rule"] == "MID_EXP_FRESHER"

    def test_new_item_older_goes_to_fc(self):
        """
        Req 8.8: new_exp_date < sfc_exp_date → putaway_zone = FC
        Older inbound must exit before existing SFC stock (FIFO).
        """
        row = make_row(
            seg="MIDDLE",
            fc_d2s=10.0,
            sfc_qty=50.0,
            need_exp=1,
            new_exp_date=date(2025, 3, 1),     # older
            sfc_exp_date=date(2025, 9, 1),     # fresher
        )
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "FC"
        assert result["putaway_rule"] == "MID_EXP_OLDER"

    def test_new_item_same_exp_goes_to_fc(self):
        """
        Req 8.8: new_exp_date == sfc_exp_date → putaway_zone = FC (same freshness).
        """
        same_date = date(2025, 6, 1)
        row = make_row(
            seg="MIDDLE",
            fc_d2s=10.0,
            sfc_qty=50.0,
            need_exp=1,
            new_exp_date=same_date,
            sfc_exp_date=same_date,
        )
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "FC"
        assert result["putaway_rule"] == "MID_EXP_OLDER"


# ── SFC empty branch (Req 8.5) ─────────────────────────────────────────────────

class TestSfcEmpty:
    """Req 8.5: sfc_qty == 0 → FC regardless of EXP state."""

    def test_sfc_empty_goes_to_fc_no_exp(self):
        row = make_row(seg="MIDDLE", fc_d2s=10.0, sfc_qty=0, need_exp=0)
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "FC"
        assert result["putaway_rule"] == "MID_SFC_EMPTY"

    def test_sfc_empty_goes_to_fc_with_exp_flag(self):
        """Even with need_exp=1, if SFC is empty it goes to FC."""
        row = make_row(
            seg="MIDDLE",
            fc_d2s=10.0,
            sfc_qty=0,
            need_exp=1,
            new_exp_date=date(2025, 12, 31),
            sfc_exp_date=date(2025, 1, 1),
        )
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "FC"
        assert result["putaway_rule"] == "MID_SFC_EMPTY"


# ── No-EXP branch (Req 8.6) ────────────────────────────────────────────────────

class TestNoExp:
    """Req 8.6: fc_d2s <= 28, sfc_qty > 0, need_exp == 0 → FC."""

    def test_no_exp_flag_sfc_not_empty_goes_to_fc(self):
        row = make_row(seg="MIDDLE", fc_d2s=15.0, sfc_qty=200.0, need_exp=0)
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "FC"
        assert result["putaway_rule"] == "MID_NO_EXP"

    def test_no_exp_flag_at_boundary_d2s(self):
        """At exactly putaway_d2s=28, still qualifies for no-EXP path."""
        row = make_row(seg="MIDDLE", fc_d2s=28.0, sfc_qty=10.0, need_exp=0)
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "FC"
        assert result["putaway_rule"] == "MID_NO_EXP"


# ── TOP always FC (Req 8.2) ────────────────────────────────────────────────────

class TestTopSegment:
    """Req 8.2: TOP SKUs always go to FC."""

    def test_top_sku_goes_to_fc(self):
        row = make_row(seg="TOP", fc_d2s=5.0, sfc_qty=0)
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "FC"
        assert result["putaway_rule"] == "TOP_FC"

    def test_top_sku_goes_to_fc_even_when_oversupplied(self):
        """TOP always FC even if fc_d2s is very high."""
        row = make_row(seg="TOP", fc_d2s=100.0, sfc_qty=500.0)
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "FC"
        assert result["putaway_rule"] == "TOP_FC"


# ── TAIL always SFC (Req 8.3) ─────────────────────────────────────────────────

class TestTailSegment:
    """Req 8.3: TAIL SKUs always go to SFC."""

    def test_tail_sku_goes_to_sfc(self):
        row = make_row(seg="TAIL", fc_d2s=5.0, sfc_qty=0)
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "SFC"
        assert result["putaway_rule"] == "TAIL_SFC"

    def test_tail_sku_goes_to_sfc_with_low_d2s(self):
        """TAIL always SFC even when FC has very little stock."""
        row = make_row(seg="TAIL", fc_d2s=0.5, sfc_qty=0)
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "SFC"
        assert result["putaway_rule"] == "TAIL_SFC"


# ── FC D2S too high for MIDDLE (Req 8.4) ──────────────────────────────────────

class TestMiddleD2sHigh:
    """Req 8.4: MIDDLE with fc_d2s > 28 → SFC."""

    def test_middle_fc_oversupplied_goes_to_sfc(self):
        row = make_row(seg="MIDDLE", fc_d2s=35.0, sfc_qty=0)
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "SFC"
        assert result["putaway_rule"] == "MID_FC_D2S_HIGH"

    def test_middle_fc_exactly_at_threshold_does_not_trigger(self):
        """fc_d2s == 28 is NOT oversupplied (condition is strictly >28)."""
        row = make_row(seg="MIDDLE", fc_d2s=28.0, sfc_qty=0)
        result = decide_putaway(row, P)
        # sfc_qty=0 → MID_SFC_EMPTY wins, not MID_FC_D2S_HIGH
        assert result["putaway_zone"] == "FC"
        assert result["putaway_rule"] == "MID_SFC_EMPTY"

    def test_middle_fc_just_over_threshold_goes_to_sfc(self):
        row = make_row(seg="MIDDLE", fc_d2s=28.01, sfc_qty=100.0)
        result = decide_putaway(row, P)
        assert result["putaway_zone"] == "SFC"
        assert result["putaway_rule"] == "MID_FC_D2S_HIGH"


# ── Output always populated (Req 8.9) ─────────────────────────────────────────

class TestOutputFields:
    """Req 8.9: putaway_rule and putaway_reason are always non-empty strings."""

    @pytest.mark.parametrize("seg", ["TOP", "MIDDLE", "TAIL"])
    def test_rule_and_reason_always_populated(self, seg):
        row = make_row(seg=seg, fc_d2s=5.0, sfc_qty=0)
        result = decide_putaway(row, P)
        assert isinstance(result["putaway_rule"], str) and result["putaway_rule"]
        assert isinstance(result["putaway_reason"], str) and result["putaway_reason"]
