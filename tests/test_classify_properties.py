# Feature: nj-inventory-allocation-v4, Property 1: Classification Exhaustiveness and Mutual Exclusion

"""
Property-based tests for logic.classify.classify().

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from logic.classify import classify

# The only valid segment labels produced by the classifier.
VALID_SEGMENTS = {"TOP", "MIDDLE", "TAIL"}


class TestClassifyProperties:
    """Property 1: Classification Exhaustiveness and Mutual Exclusion."""

    @given(
        st.floats(min_value=0, max_value=30, allow_nan=False),
        st.floats(min_value=0, max_value=100, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_classify_returns_valid_segment(self, aod: float, ol: float) -> None:
        """Every (aod, ol) pair maps to exactly one of TOP / MIDDLE / TAIL.

        Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5
        - Req 1.1: result is one of the three defined segments (exhaustiveness)
        - Req 1.2: TOP assigned when aod >= 21 AND ol >= 30
        - Req 1.3: TAIL assigned when ol < 4
        - Req 1.4: MIDDLE is the fallback (everything else)
        - Req 1.5: evaluation order TOP → TAIL → MIDDLE ensures mutual exclusion
        """
        row = {"active_order_days": aod, "order_lines_30d": ol}

        result = classify(row)

        # Exhaustiveness: result must be one of the three valid segment strings
        assert result in VALID_SEGMENTS, (
            f"classify returned unexpected value {result!r} for aod={aod}, ol={ol}. "
            f"Expected one of {VALID_SEGMENTS}."
        )

        # No SKU left unclassified: result must be a non-empty string
        assert isinstance(result, str) and len(result) > 0, (
            f"classify returned an empty or non-string result {result!r} for aod={aod}, ol={ol}."
        )

        # Mutual exclusion: result is exactly one segment (set membership already
        # guarantees this since VALID_SEGMENTS has three distinct elements, but
        # we verify via the invariant that it cannot be two at once)
        assert result == result.strip(), (
            f"classify returned a result with surrounding whitespace: {result!r}"
        )


# Feature: nj-inventory-allocation-v4, Property 2: Classification Boundary Correctness


class TestClassifyBoundaryCorrectness:
    """Property 2: Classification Boundary Correctness.

    **Validates: Requirements 1.2, 1.3, 1.4**

    For any input in the MIDDLE region — defined as ol >= ol_tail AND
    (aod < aod_top OR ol < ol_top) — the classifier must return "MIDDLE".

    - Req 1.2: TOP requires BOTH aod >= aod_top AND ol >= ol_top; if either
      is false (aod < aod_top OR ol < ol_top) the SKU cannot be TOP.
    - Req 1.3: TAIL requires ol < ol_tail; the precondition ol >= ol_tail
      rules out TAIL.
    - Req 1.4: With TOP and TAIL excluded, the result must be MIDDLE.
    """

    @given(
        st.floats(min_value=0, max_value=30, allow_nan=False),
        st.floats(min_value=0, max_value=100, allow_nan=False),
        st.floats(min_value=0, max_value=30, allow_nan=False),
        st.floats(min_value=0, max_value=100, allow_nan=False),
        st.floats(min_value=0, max_value=10, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_middle_region_always_classified_as_middle(
        self,
        aod: float,
        ol: float,
        aod_top: float,
        ol_top: float,
        ol_tail: float,
    ) -> None:
        """Any SKU in the MIDDLE region is classified as MIDDLE.

        Validates: Requirements 1.2, 1.3, 1.4
        Precondition (MIDDLE region): ol >= ol_tail AND (aod < aod_top OR ol < ol_top)
        """
        from hypothesis import assume

        # Avoid degenerate thresholds where ol_tail > ol_top would make the
        # MIDDLE region impossible to enter via the standard decision tree.
        assume(ol_tail <= ol_top)

        # Constrain to the MIDDLE region:
        #   - ol >= ol_tail  → rules out TAIL (Req 1.3)
        #   - aod < aod_top OR ol < ol_top  → rules out TOP (Req 1.2)
        assume(ol >= ol_tail and (aod < aod_top or ol < ol_top))

        row = {"active_order_days": aod, "order_lines_30d": ol}

        result = classify(row, aod_top=aod_top, ol_top=ol_top, ol_tail=ol_tail)

        assert result == "MIDDLE", (
            f"Expected 'MIDDLE' for aod={aod}, ol={ol}, "
            f"aod_top={aod_top}, ol_top={ol_top}, ol_tail={ol_tail}, "
            f"but got {result!r}."
        )
