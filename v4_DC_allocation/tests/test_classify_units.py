"""
Unit tests for exact boundary values of the v4 SKU classifier.

Validates: Requirements 1.2, 1.3, 1.4

Default thresholds: aod_top=21, ol_top=30, ol_tail=4
"""

import pandas as pd
import pytest

from v4_DC_allocation.logic.classify import classify, classify_df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def row(aod: float, ol: float) -> dict:
    """Build a minimal dict that classify() accepts."""
    return {"aod": aod, "ol": ol}


# ---------------------------------------------------------------------------
# Boundary edge tests — single-row classify()
# ---------------------------------------------------------------------------

class TestClassifyBoundaryEdges:
    """Exact boundary cases using default thresholds (aod_top=21, ol_top=30, ol_tail=4)."""

    def test_ol_below_tail_threshold_returns_tail(self):
        """ol=3 < ol_tail=4 → TAIL regardless of aod. (Req 1.3)"""
        assert classify(row(aod=0,  ol=3)) == "TAIL"
        assert classify(row(aod=21, ol=3)) == "TAIL"
        assert classify(row(aod=50, ol=3)) == "TAIL"

    def test_ol_at_tail_threshold_with_aod_at_top_returns_middle(self):
        """ol=4 >= ol_tail=4, aod=21 >= aod_top=21, but ol=4 < ol_top=30 → MIDDLE. (Req 1.4)"""
        assert classify(row(aod=21, ol=4)) == "MIDDLE"

    def test_ol_just_below_top_threshold_returns_middle(self):
        """ol=29 < ol_top=30 → cannot be TOP even with aod=21 → MIDDLE. (Req 1.4)"""
        assert classify(row(aod=21, ol=29)) == "MIDDLE"

    def test_both_thresholds_met_returns_top(self):
        """ol=30 >= ol_top=30 AND aod=21 >= aod_top=21 → TOP. (Req 1.2)"""
        assert classify(row(aod=21, ol=30)) == "TOP"

    def test_ol_meets_top_but_aod_below_returns_middle(self):
        """ol=30 >= ol_top but aod=20 < aod_top=21 → MIDDLE. (Req 1.4)"""
        assert classify(row(aod=20, ol=30)) == "MIDDLE"

    def test_zero_aod_zero_ol_returns_tail(self):
        """ol=0 < ol_tail=4 → TAIL. (Req 1.3)"""
        assert classify(row(aod=0, ol=0)) == "TAIL"

    def test_high_values_return_top(self):
        """aod=100, ol=100 — clearly above all thresholds → TOP. (Req 1.2)"""
        assert classify(row(aod=100, ol=100)) == "TOP"

    def test_custom_ol_tail_threshold(self):
        """ol=4 with custom ol_tail=5 → ol < ol_tail → TAIL. (Req 1.6)"""
        assert classify(row(aod=21, ol=4), ol_tail=5) == "TAIL"


# ---------------------------------------------------------------------------
# classify_df() produces same results as classify()
# ---------------------------------------------------------------------------

class TestClassifyDfConsistency:
    """classify_df() must produce identical labels to calling classify() row-by-row."""

    # All eight boundary cases bundled into a single DataFrame test.
    _CASES = [
        # (aod,   ol,   kwargs,            expected)
        (0,    3,    {},                  "TAIL"),    # ol < 4 → TAIL
        (21,   3,    {},                  "TAIL"),    # ol < 4 → TAIL
        (21,   4,    {},                  "MIDDLE"),  # ol=4 >= tail, aod ok but ol < ol_top
        (21,   29,   {},                  "MIDDLE"),  # ol < ol_top
        (21,   30,   {},                  "TOP"),     # both thresholds met
        (20,   30,   {},                  "MIDDLE"),  # aod < aod_top
        (0,    0,    {},                  "TAIL"),    # both zero → ol < 4
        (100,  100,  {},                  "TOP"),     # clearly above
        (21,   4,    {"ol_tail": 5},      "TAIL"),    # custom threshold
    ]

    def test_classify_df_matches_classify_row_by_row(self):
        """Each row classified by classify_df() must equal the classify() result."""
        records = [{"aod": aod, "ol": ol} for aod, ol, _, _ in self._CASES]
        expected = [exp for _, _, _, exp in self._CASES]

        # Build one DataFrame per unique threshold set and verify independently.
        # Default-threshold rows
        default_records = [r for r, (_, _, kw, _) in zip(records, self._CASES) if not kw]
        default_expected = [e for (_, _, kw, e) in self._CASES if not kw]
        df_default = pd.DataFrame(default_records)
        result_default = classify_df(df_default).tolist()
        assert result_default == default_expected

        # Custom-threshold row (ol_tail=5)
        custom_records = [r for r, (_, _, kw, _) in zip(records, self._CASES) if kw]
        custom_expected = [e for (_, _, kw, e) in self._CASES if kw]
        df_custom = pd.DataFrame(custom_records)
        result_custom = classify_df(df_custom, ol_tail=5).tolist()
        assert result_custom == custom_expected

    def test_classify_df_output_is_series(self):
        """classify_df() must return a pandas Series."""
        df = pd.DataFrame([{"aod": 21, "ol": 30}])
        result = classify_df(df)
        assert isinstance(result, pd.Series)

    def test_classify_df_index_aligned_with_input(self):
        """Output Series index must align with input DataFrame index."""
        df = pd.DataFrame([{"aod": 21, "ol": 30}, {"aod": 0, "ol": 0}], index=[10, 20])
        result = classify_df(df)
        assert list(result.index) == [10, 20]

    def test_classify_df_empty_dataframe(self):
        """classify_df() on an empty DataFrame must return an empty Series."""
        df = pd.DataFrame(columns=["aod", "ol"])
        result = classify_df(df)
        assert isinstance(result, pd.Series)
        assert len(result) == 0
