"""
tests/test_sfc_to_fc_units.py — Unit tests for SFC→FC routing branches

Requirements covered: 5.2, 6.2, 6.3, 6.4, 6.5, 6.6, 7.4
"""

import sys
import os

import pandas as pd
import pytest
from datetime import datetime

# Make the v4_DC_allocation package importable (conftest.py does this too,
# but explicit is safer when running the file directly)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.sfc_to_fc import (
    compute_top_sfc_to_fc,
    build_top_queue,
    route_middle_sku,
    build_middle_lists,
    select_middle_sfc_locations,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _top_sku(item="SKU001", seg="TOP", fc_qty=50, sfc_qty=10,
             adl=1.0, fc_d2s=5.0,
             sfc_exp_date=None, fc_exp_date=None,
             sfc_inv_age=None, fc_inv_age=None):
    """Build a minimal single-row TOP-SKU DataFrame."""
    return {
        "item_number": item,
        "seg": seg,
        "fc_qty": fc_qty,
        "sfc_qty": sfc_qty,
        "avg_daily_orders": adl,
        "fc_d2s": fc_d2s,
        "sfc_exp_date": sfc_exp_date,
        "fc_exp_date": fc_exp_date,
        "sfc_inv_age": sfc_inv_age,
        "fc_inv_age": fc_inv_age,
    }


def _mid_row(item="SKU001", fc_d2s=10.0, sfc_exp_date=None, fc_exp_date=None,
             sfc_loc_type="Stock", mid_emergency_d2s=None,
             sfc_qty=0.0, unit_volume_cbm=0.0, avg_daily_orders=1.0):
    """Build a minimal MIDDLE SKU dict for route_middle_sku / build_middle_lists.

    sfc_qty, unit_volume_cbm, avg_daily_orders are required by build_middle_lists
    (used for batch_volume / sorting), so we include them with safe defaults.
    """
    row = {
        "item_number": item,
        "fc_d2s": fc_d2s,
        "sfc_exp_date": sfc_exp_date,
        "fc_exp_date": fc_exp_date,
        "sfc_loc_type": sfc_loc_type,
        "sfc_qty": sfc_qty,
        "unit_volume_cbm": unit_volume_cbm,
        "avg_daily_orders": avg_daily_orders,
    }
    if mid_emergency_d2s is not None:
        row["mid_emergency_d2s"] = mid_emergency_d2s
    return row


def _lot_row(item, location_no, qty, loc_type="Stock",
             exp_date=None, case_qty=1, need_minimal_exp=0, inv_age_days=0):
    """Build a single SFC lot row dict."""
    return {
        "item_number": item,
        "location_no": location_no,
        "loc_type": loc_type,
        "qty": qty,
        "exp_date": exp_date,
        "case_qty": case_qty,
        "need_minimal_exp": need_minimal_exp,
        "inv_age_days": inv_age_days,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. compute_top_sfc_to_fc — sfc_qty=0 → no task  (Req 5.2)
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeTopSfcToFc:
    """Req 5.2: SKUs with sfc_qty = 0 must be excluded from output."""

    def test_sfc_qty_zero_returns_empty(self):
        df = pd.DataFrame([_top_sku(sfc_qty=0)])
        result = compute_top_sfc_to_fc(df)
        assert result.empty, (
            "Req 5.2: sfc_qty=0 must not produce a transfer task"
        )

    def test_sfc_qty_zero_multiple_skus_filtered(self):
        """All rows have sfc_qty=0 → result must be empty."""
        df = pd.DataFrame([
            _top_sku(item="A", sfc_qty=0),
            _top_sku(item="B", sfc_qty=0),
        ])
        result = compute_top_sfc_to_fc(df)
        assert result.empty, (
            "Req 5.2: all-zero sfc_qty rows must produce an empty DataFrame"
        )

    def test_sfc_qty_zero_mixed_with_positive(self):
        """Zero rows are excluded; positive rows are kept."""
        df = pd.DataFrame([
            _top_sku(item="A", sfc_qty=0),
            _top_sku(item="B", sfc_qty=5),
        ])
        result = compute_top_sfc_to_fc(df)
        assert len(result) == 1
        assert result.iloc[0]["item_number"] == "B"

    def test_sfc_qty_nan_treated_as_zero(self):
        """NaN sfc_qty must also be excluded (treated as 0)."""
        df = pd.DataFrame([_top_sku(sfc_qty=float("nan"))])
        result = compute_top_sfc_to_fc(df)
        assert result.empty, "NaN sfc_qty must be treated as 0 and excluded"

    def test_sfc_qty_positive_produces_task(self):
        """Positive sfc_qty produces a task with trigger=TOP_REPLEN."""
        df = pd.DataFrame([_top_sku(sfc_qty=10)])
        result = compute_top_sfc_to_fc(df)
        assert not result.empty
        assert result.iloc[0]["trigger"] == "TOP_REPLEN"
        assert result.iloc[0]["transfer_qty"] == 10


# ─────────────────────────────────────────────────────────────────────────────
# 2. route_middle_sku — all five routing branches  (Req 6.2 – 6.6)
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteMiddleSku:
    """Req 6.2-6.6: All five routing outcomes from route_middle_sku."""

    # ── Req 6.2: fc_d2s < 3 → "List1" ──────────────────────────────────────

    def test_fc_d2s_lt3_returns_list1(self):
        """Req 6.2: fc_d2s=2 (< 3) must route to List1 regardless of other fields."""
        row = _mid_row(fc_d2s=2)
        assert route_middle_sku(row) == "List1"

    def test_fc_d2s_zero_returns_list1(self):
        row = _mid_row(fc_d2s=0)
        assert route_middle_sku(row) == "List1"

    def test_fc_d2s_2_99_returns_list1(self):
        row = _mid_row(fc_d2s=2.99)
        assert route_middle_sku(row) == "List1"

    # ── Req 6.4: EXP trigger + Bin → "List3" ────────────────────────────────

    def test_exp_trigger_bin_returns_list3(self):
        """Req 6.4: fc_d2s=20, sfc_exp_date < fc_exp_date, loc_type=Bin → List3."""
        row = _mid_row(
            fc_d2s=20,
            sfc_exp_date=datetime(2025, 1, 1),
            fc_exp_date=datetime(2025, 6, 1),
            sfc_loc_type="Bin",
        )
        assert route_middle_sku(row) == "List3"

    def test_exp_trigger_bin_ignores_d2s_value(self):
        """EXP+Bin branch fires even if fc_d2s is well above emergency threshold."""
        row = _mid_row(
            fc_d2s=50,
            sfc_exp_date=datetime(2025, 3, 1),
            fc_exp_date=datetime(2025, 9, 1),
            sfc_loc_type="Bin",
        )
        assert route_middle_sku(row) == "List3"

    # ── Req 6.5: EXP trigger + Stock → "Dateswitch" ─────────────────────────

    def test_exp_trigger_stock_returns_dateswitch(self):
        """Req 6.5: fc_d2s=20, sfc_exp_date < fc_exp_date, loc_type=Stock → Dateswitch."""
        row = _mid_row(
            fc_d2s=20,
            sfc_exp_date=datetime(2025, 1, 1),
            fc_exp_date=datetime(2025, 6, 1),
            sfc_loc_type="Stock",
        )
        assert route_middle_sku(row) == "Dateswitch"

    def test_exp_trigger_non_bin_returns_dateswitch(self):
        """Any non-Bin loc_type with EXP trigger falls to Dateswitch."""
        row = _mid_row(
            fc_d2s=15,
            sfc_exp_date=datetime(2024, 12, 1),
            fc_exp_date=datetime(2025, 5, 1),
            sfc_loc_type="Stock",
        )
        assert route_middle_sku(row) == "Dateswitch"

    # ── Req 6.3: No EXP trigger + fc_d2s < mid_emergency_d2s(14) → "List2" ──

    def test_no_exp_trigger_d2s_lt_emergency_returns_list2(self):
        """Req 6.3: fc_d2s=10, no exp trigger, default mid_emergency_d2s=14 → List2."""
        row = _mid_row(fc_d2s=10, sfc_exp_date=None, fc_exp_date=None)
        assert route_middle_sku(row) == "List2"

    def test_no_exp_trigger_d2s_3_returns_list2(self):
        """fc_d2s=3 is NOT < 3, so List1 is skipped; no EXP trigger, 3 < 14 → List2."""
        row = _mid_row(fc_d2s=3, sfc_exp_date=None, fc_exp_date=None)
        assert route_middle_sku(row) == "List2"

    def test_list2_uses_default_mid_emergency_d2s_14(self):
        """When mid_emergency_d2s is absent, default is 14; d2s=13 → List2."""
        row = _mid_row(fc_d2s=13)
        assert route_middle_sku(row) == "List2"

    # ── Req 6.6: No EXP trigger + fc_d2s >= mid_emergency_d2s → "Keep" ──────

    def test_no_exp_trigger_d2s_ge_emergency_returns_keep(self):
        """Req 6.6: fc_d2s=20, no exp trigger, default mid_emergency_d2s=14 → Keep."""
        row = _mid_row(fc_d2s=20, sfc_exp_date=None, fc_exp_date=None)
        assert route_middle_sku(row) == "Keep"

    def test_no_exp_trigger_d2s_exact_14_returns_keep(self):
        """fc_d2s=14 is NOT < 14, so falls through to Keep."""
        row = _mid_row(fc_d2s=14)
        assert route_middle_sku(row) == "Keep"

    def test_no_exp_trigger_large_d2s_returns_keep(self):
        row = _mid_row(fc_d2s=999)
        assert route_middle_sku(row) == "Keep"

    # ── Boundary: EXP trigger only fires when sfc_exp_date < fc_exp_date ─────

    def test_no_exp_trigger_when_dates_equal(self):
        """Equal exp dates → no EXP trigger → falls through based on fc_d2s."""
        same_date = datetime(2025, 6, 1)
        row = _mid_row(
            fc_d2s=10,
            sfc_exp_date=same_date,
            fc_exp_date=same_date,
            sfc_loc_type="Bin",
        )
        # No EXP trigger, fc_d2s=10 < 14 → List2
        assert route_middle_sku(row) == "List2"

    def test_no_exp_trigger_when_sfc_exp_later(self):
        """sfc_exp_date > fc_exp_date → no trigger → falls to Keep (d2s=20)."""
        row = _mid_row(
            fc_d2s=20,
            sfc_exp_date=datetime(2025, 9, 1),
            fc_exp_date=datetime(2025, 1, 1),
            sfc_loc_type="Bin",
        )
        assert route_middle_sku(row) == "Keep"


# ─────────────────────────────────────────────────────────────────────────────
# 3. select_middle_sfc_locations — universal rule  (Req 7.4)
# ─────────────────────────────────────────────────────────────────────────────

class TestSelectMiddleSfcLocationsUniversal:
    """Req 7.4: when sfc_qty < needed, return ALL SFC locations with rule='MIDDLE_SFC_Universal'."""

    def _make_sku_row(self, item="SKU001", adl=1.0, fc_qty=0.0, sfc_qty=5.0):
        return {
            "item_number": item,
            "avg_daily_orders": adl,
            "fc_qty": fc_qty,
            "sfc_qty": sfc_qty,
        }

    def _make_lots(self, item="SKU001"):
        return pd.DataFrame([
            _lot_row(item, "A-01", 10),
            _lot_row(item, "A-02", 8),
            _lot_row(item, "B-01", 12),
        ])

    def test_universal_rule_when_sfc_qty_lt_needed(self):
        """Req 7.4: sfc_qty=5 < needed (ceil(2*28)-0=56) → universal rule fires."""
        sku_row = self._make_sku_row(adl=2.0, fc_qty=0.0, sfc_qty=5.0)
        df_lots = self._make_lots()
        result = select_middle_sfc_locations(df_lots, sku_row)
        assert not result.empty, "Universal rule must return all SFC locations"
        assert (result["rule"] == "MIDDLE_SFC_Universal").all(), (
            "Req 7.4: all rows must carry rule='MIDDLE_SFC_Universal'"
        )

    def test_universal_rule_returns_all_locations(self):
        """All 3 lot rows must appear in the result."""
        sku_row = self._make_sku_row(adl=2.0, fc_qty=0.0, sfc_qty=5.0)
        df_lots = self._make_lots()
        result = select_middle_sfc_locations(df_lots, sku_row)
        assert len(result) == 3

    def test_universal_rule_qty_to_transfer_equals_qty(self):
        """qty_to_transfer must equal qty for every row under universal rule."""
        sku_row = self._make_sku_row(adl=2.0, fc_qty=0.0, sfc_qty=5.0)
        df_lots = self._make_lots()
        result = select_middle_sfc_locations(df_lots, sku_row)
        for _, row in result.iterrows():
            assert row["qty_to_transfer"] == row["qty"], (
                f"Location {row['location_no']}: qty_to_transfer must equal qty"
            )

    def test_universal_rule_strict_sfc_lt_needed(self):
        """sfc_qty must be strictly less than needed to trigger universal rule."""
        # adl=1, fc_qty=0 → needed=ceil(1*28)=28
        # sfc_qty=27 < 28 → universal rule
        sku_row = self._make_sku_row(adl=1.0, fc_qty=0.0, sfc_qty=27.0)
        df_lots = self._make_lots()
        result = select_middle_sfc_locations(df_lots, sku_row)
        assert (result["rule"] == "MIDDLE_SFC_Universal").all()

    def test_non_universal_when_sfc_qty_gte_needed(self):
        """When sfc_qty >= needed, universal rule must NOT fire."""
        # adl=1, fc_qty=20 → needed=ceil(1*28)-20=8
        # sfc_qty=100 >= 8 → NOT universal
        sku_row = self._make_sku_row(adl=1.0, fc_qty=20.0, sfc_qty=100.0)
        df_lots = self._make_lots()
        result = select_middle_sfc_locations(df_lots, sku_row)
        if not result.empty:
            assert not (result["rule"] == "MIDDLE_SFC_Universal").any(), (
                "Universal rule must not fire when sfc_qty >= needed"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 4. build_middle_lists — returns all 5 keys  (Req 6.2 – 6.6)
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildMiddleLists:
    """build_middle_lists must always return exactly 5 keys, even for empty routes."""

    _EXPECTED_KEYS = {"List1", "List2", "List3", "Dateswitch", "Keep"}

    def _candidates(self):
        """One candidate per branch so each key gets at least one row."""
        return pd.DataFrame([
            # List1 — fc_d2s < 3
            _mid_row(item="SKU_L1", fc_d2s=1),
            # List3 — EXP + Bin
            _mid_row(item="SKU_L3", fc_d2s=20,
                     sfc_exp_date=datetime(2025, 1, 1),
                     fc_exp_date=datetime(2025, 6, 1),
                     sfc_loc_type="Bin"),
            # Dateswitch — EXP + Stock
            _mid_row(item="SKU_DS", fc_d2s=20,
                     sfc_exp_date=datetime(2025, 1, 1),
                     fc_exp_date=datetime(2025, 6, 1),
                     sfc_loc_type="Stock"),
            # List2 — no EXP, fc_d2s < 14
            _mid_row(item="SKU_L2", fc_d2s=10),
            # Keep — no EXP, fc_d2s >= 14
            _mid_row(item="SKU_KP", fc_d2s=20),
        ])

    def test_returns_all_five_keys(self):
        result = build_middle_lists(self._candidates())
        assert set(result.keys()) == self._expected_keys()

    def _expected_keys(self):
        return self._EXPECTED_KEYS

    def test_returns_five_keys_even_with_empty_input(self):
        result = build_middle_lists(pd.DataFrame())
        assert set(result.keys()) == self._EXPECTED_KEYS

    def test_empty_routes_are_dataframes(self):
        """Empty routes must be empty DataFrames, not None or missing."""
        # Only one candidate → four other keys must be empty DataFrames
        df = pd.DataFrame([_mid_row(fc_d2s=1)])  # → List1 only
        result = build_middle_lists(df)
        for key in self._EXPECTED_KEYS:
            assert isinstance(result[key], pd.DataFrame), (
                f"Key '{key}' must be a DataFrame, got {type(result[key])}"
            )

    def test_empty_routes_have_zero_rows(self):
        """Routes without candidates must have 0 rows."""
        df = pd.DataFrame([_mid_row(fc_d2s=1)])  # → List1 only
        result = build_middle_lists(df)
        assert result["List1"].shape[0] == 1
        assert result["List2"].empty
        assert result["List3"].empty
        assert result["Dateswitch"].empty
        assert result["Keep"].empty

    def test_each_row_lands_in_correct_bucket(self):
        result = build_middle_lists(self._candidates())
        assert len(result["List1"]) >= 1
        assert len(result["List2"]) >= 1
        assert len(result["List3"]) >= 1
        assert len(result["Dateswitch"]) >= 1
        assert len(result["Keep"]) >= 1

    def test_item_numbers_in_correct_bucket(self):
        result = build_middle_lists(self._candidates())
        assert "SKU_L1" in result["List1"]["item_number"].values
        assert "SKU_L2" in result["List2"]["item_number"].values
        assert "SKU_L3" in result["List3"]["item_number"].values
        assert "SKU_DS" in result["Dateswitch"]["item_number"].values
        assert "SKU_KP" in result["Keep"]["item_number"].values


# ─────────────────────────────────────────────────────────────────────────────
# 5. build_top_queue — P0 ordering  (Req 5.4)
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTopQueueP0Ordering:
    """Req 5.4: SKUs with fc_d2s < 3 (P0) must appear before fc_d2s >= 3 (P1-P3)."""

    def _make_triggered(self, rows):
        """Wrap raw dicts as triggered DataFrame (adds trigger + transfer_qty)."""
        df = pd.DataFrame(rows)
        df["trigger"] = "TOP_REPLEN"
        df["transfer_qty"] = df.get("sfc_qty", 5)
        return df

    def test_p0_before_p1(self):
        """fc_d2s=2 (P0) must precede fc_d2s=5 (P1) in the sorted queue."""
        df = self._make_triggered([
            _top_sku(item="P1_SKU", fc_d2s=5, sfc_qty=5),
            _top_sku(item="P0_SKU", fc_d2s=2, sfc_qty=5),
        ])
        result = build_top_queue(df)
        items = result["item_number"].tolist()
        assert items.index("P0_SKU") < items.index("P1_SKU"), (
            "Req 5.4: P0 SKU (fc_d2s=2) must appear before P1 SKU (fc_d2s=5)"
        )

    def test_multiple_p0_before_all_p1(self):
        """All P0 SKUs must precede all P1+ SKUs."""
        df = self._make_triggered([
            _top_sku(item="P1_A", fc_d2s=10, sfc_qty=5),
            _top_sku(item="P0_B", fc_d2s=1, sfc_qty=5),
            _top_sku(item="P1_C", fc_d2s=7, sfc_qty=5),
            _top_sku(item="P0_A", fc_d2s=2, sfc_qty=5),
        ])
        result = build_top_queue(df)
        p0_mask = result["fc_d2s"] < 3
        p1_mask = result["fc_d2s"] >= 3
        p0_indices = result.index[p0_mask].tolist()
        p1_indices = result.index[p1_mask].tolist()
        if p0_indices and p1_indices:
            assert max(p0_indices) < min(p1_indices), (
                "All P0 rows must come before all P1+ rows in sorted queue"
            )

    def test_p0_group_flag_set_correctly(self):
        """priority_group column: 0 for fc_d2s < 3, 1 otherwise."""
        df = self._make_triggered([
            _top_sku(item="A", fc_d2s=2, sfc_qty=5),
            _top_sku(item="B", fc_d2s=3, sfc_qty=5),
            _top_sku(item="C", fc_d2s=0, sfc_qty=5),
            _top_sku(item="D", fc_d2s=10, sfc_qty=5),
        ])
        result = build_top_queue(df)
        for _, row in result.iterrows():
            expected = 0 if row["fc_d2s"] < 3 else 1
            assert row["priority_group"] == expected, (
                f"item={row['item_number']}, fc_d2s={row['fc_d2s']}: "
                f"expected priority_group={expected}, got {row['priority_group']}"
            )

    def test_fc_d2s_3_is_not_p0(self):
        """fc_d2s=3 is NOT strictly less than 3 → P1, not P0."""
        df = self._make_triggered([
            _top_sku(item="BORDER", fc_d2s=3, sfc_qty=5),
        ])
        result = build_top_queue(df)
        assert result.iloc[0]["priority_group"] == 1, (
            "fc_d2s=3 must be P1 (priority_group=1), not P0"
        )

    def test_empty_input_returns_empty(self):
        df = pd.DataFrame()
        result = build_top_queue(df)
        assert result.empty

    def test_all_p0_preserved_in_output(self):
        """No P0 SKU may be dropped during queue build."""
        df = self._make_triggered([
            _top_sku(item=f"P0_{i}", fc_d2s=i, sfc_qty=5)
            for i in range(3)  # 0, 1, 2 → all P0
        ])
        result = build_top_queue(df)
        assert len(result) == 3
        assert (result["priority_group"] == 0).all()
