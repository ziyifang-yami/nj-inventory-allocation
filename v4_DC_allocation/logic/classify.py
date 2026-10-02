"""
logic/classify.py — SKU Segmentation Classifier for NJ Inventory Allocation v4

Assigns each SKU to exactly one of three segments: TOP, MIDDLE, or TAIL.

Evaluation order (Requirements 1.5):
  1. TOP   — aod >= aod_top  AND  ol >= ol_top
  2. TAIL  — ol < ol_tail
  3. MIDDLE — everything else

Default thresholds (Requirements 1.2, 1.3):
  aod_top  = 21   (Active Order Days minimum for TOP)
  ol_top   = 30   (Order Lines 30d minimum for TOP)
  ol_tail  = 8    (Order Lines 30d below which → TAIL; default 8, tunable)

Column name support (Requirements 1.1, 1.4):
  Accepts both v3-style long names  (active_order_days, order_lines_30d)
  and short aliases                 (aod, ol).
"""

from __future__ import annotations

import pandas as pd


def classify(
    row,
    aod_top: float = 21,
    ol_top: float = 30,
    ol_tail: float = 8,
) -> str:
    """Classify a single SKU row into 'TOP', 'MIDDLE', or 'TAIL'.

    Parameters
    ----------
    row:
        A pandas Series (or any dict-like) representing one SKU.
        Must contain either ``active_order_days`` or ``aod`` (Active Order Days),
        and either ``order_lines_30d`` or ``ol`` (30-day order lines).
    aod_top:
        Minimum active order days for the TOP segment (default 21).
    ol_top:
        Minimum 30-day order lines for the TOP segment (default 30).
    ol_tail:
        30-day order lines strictly below which the SKU is TAIL (default 4).

    Returns
    -------
    str
        One of ``'TOP'``, ``'TAIL'``, or ``'MIDDLE'``.

    Notes
    -----
    Evaluation order (Req 1.5):
      1. TOP  — if aod >= aod_top **and** ol >= ol_top
      2. TAIL — elif ol < ol_tail
      3. MIDDLE — else
    """
    # Support both long column names (v3-style) and short aliases
    aod = row.get("active_order_days") if hasattr(row, "get") else getattr(row, "active_order_days", None)
    if aod is None:
        aod = row.get("aod") if hasattr(row, "get") else getattr(row, "aod", 0)
    aod = float(aod or 0)

    ol = row.get("order_lines_30d") if hasattr(row, "get") else getattr(row, "order_lines_30d", None)
    if ol is None:
        ol = row.get("ol") if hasattr(row, "get") else getattr(row, "ol", 0)
    ol = float(ol or 0)

    # Priority 1: TOP (Req 1.2)
    if aod >= aod_top and ol >= ol_top:
        return "TOP"

    # Priority 2: TAIL (Req 1.3)
    if ol < ol_tail:
        return "TAIL"

    # Priority 3: MIDDLE (Req 1.4)
    return "MIDDLE"


def classify_df(
    df: pd.DataFrame,
    aod_top: float = 21,
    ol_top: float = 30,
    ol_tail: float = 8,
) -> pd.Series:
    """Apply :func:`classify` to every row of *df* and return a Series of labels.

    Convenience wrapper so callers can write::

        df["seg"] = classify_df(df, **threshold_kwargs)

    instead of::

        df["seg"] = df.apply(classify, axis=1, **threshold_kwargs)

    Parameters
    ----------
    df:
        DataFrame with SKU rows.  Must contain at least one of the supported
        column-name pairs (``active_order_days``/``aod`` and
        ``order_lines_30d``/``ol``).
    aod_top, ol_top, ol_tail:
        Threshold overrides; defaults match v4 configuration (Req 1.6).

    Returns
    -------
    pd.Series
        String Series of ``'TOP'`` / ``'MIDDLE'`` / ``'TAIL'`` aligned to *df*.
    """
    threshold_kwargs = {"aod_top": aod_top, "ol_top": ol_top, "ol_tail": ol_tail}
    return df.apply(classify, axis=1, **threshold_kwargs)
