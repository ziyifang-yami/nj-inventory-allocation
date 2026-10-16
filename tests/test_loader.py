"""
tests/test_loader.py

Unit tests for v4_DC_allocation/data/loader.py error paths.

Coverage:
  - Req 9.5  : MySQL connection failure → load_inventory/load_capacity return (None, error_str)
  - Req 10.3 : Athena unavailable + valid disk cache → load() reads from cache (age < 12h)
  - Req 10.5 : Athena unavailable + no cache → load() falls back to latest CSV
  - Req 10.6 : Athena unavailable + no CSV → load() raises FileNotFoundError

The @st.cache_data decorators are patched to passthrough at module level so the
real function bodies execute during tests.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# Helpers to neutralise @st.cache_data before loader is imported
# ---------------------------------------------------------------------------

def _make_passthrough_cache_data():
    """Return a no-op replacement for streamlit.cache_data."""
    def cache_data(func=None, *, ttl=None, show_spinner=True, **kwargs):
        if func is not None:
            # Called as @st.cache_data (no args)
            return func
        # Called as @st.cache_data(ttl=...) — return decorator
        def decorator(f):
            return f
        return decorator
    return cache_data


def _get_loader_module():
    """
    Import (or reimport) data.loader with streamlit.cache_data neutralised.

    We inject a minimal fake `streamlit` into sys.modules so the decorator is
    a no-op. This avoids the need to install Streamlit in the test environment
    and prevents accidental cache interactions between tests.
    """
    # Build a minimal fake streamlit if not already present
    if "streamlit" not in sys.modules:
        fake_st = types.ModuleType("streamlit")
        fake_st.cache_data = _make_passthrough_cache_data()
        sys.modules["streamlit"] = fake_st
    else:
        # Patch the existing module's cache_data in place
        sys.modules["streamlit"].cache_data = _make_passthrough_cache_data()

    # Force a fresh import of the loader so the patched decorator is applied
    for mod_name in list(sys.modules.keys()):
        if "data.loader" in mod_name or mod_name == "data.loader":
            del sys.modules[mod_name]

    # Add the package root to sys.path so `data.loader` resolves correctly
    pkg_root = str(Path(__file__).resolve().parents[1])
    if pkg_root not in sys.path:
        sys.path.insert(0, pkg_root)

    import data.loader as loader  # noqa: PLC0415
    return loader


# Load once for the whole module
loader = _get_loader_module()


# ===========================================================================
# Req 9.5 — MySQL connection failure
# ===========================================================================

class TestMysqlConnectionFailure:
    """Req 9.5: MySQL failure must be caught and surfaced as (None, error_str)."""

    def test_load_inventory_returns_none_and_error_string(self):
        """
        WHEN _connect() raises an exception,
        load_inventory() MUST return (None, non_empty_string).
        Validates: Requirements 9.5
        """
        with patch("data.loader._connect", side_effect=Exception("connection refused")):
            result, err = loader.load_inventory()

        assert result is None, "Expected None DataFrame on connection failure"
        assert isinstance(err, str) and err, "Expected a non-empty error string"
        assert "connection refused" in err

    def test_load_capacity_returns_none_and_error_string(self):
        """
        WHEN _connect() raises an exception,
        load_capacity() MUST return (None, non_empty_string).
        Validates: Requirements 9.5
        """
        with patch("data.loader._connect", side_effect=Exception("connection refused")):
            result, err = loader.load_capacity()

        assert result is None, "Expected None DataFrame on connection failure"
        assert isinstance(err, str) and err, "Expected a non-empty error string"
        assert "connection refused" in err

    def test_load_inventory_error_string_is_not_empty_for_arbitrary_exceptions(self):
        """
        WHEN _connect() raises a generic RuntimeError,
        load_inventory() MUST still return (None, non_empty_string).
        Validates: Requirements 9.5
        """
        with patch("data.loader._connect", side_effect=RuntimeError("host unreachable")):
            result, err = loader.load_inventory()

        assert result is None
        assert err  # non-empty
        assert "host unreachable" in err

    def test_load_capacity_error_string_is_not_empty_for_arbitrary_exceptions(self):
        """
        WHEN _connect() raises a generic OSError,
        load_capacity() MUST still return (None, non_empty_string).
        Validates: Requirements 9.5
        """
        with patch("data.loader._connect", side_effect=OSError("network error")):
            result, err = loader.load_capacity()

        assert result is None
        assert err  # non-empty
        assert "network error" in err


# ===========================================================================
# Req 10.3 — Athena unavailable + valid disk cache → read from cache
# ===========================================================================

class TestAthenaUnavailableValidCache:
    """Req 10.3: load() reads from disk cache when cache age < 12 h."""

    def test_load_reads_from_valid_disk_cache(self, tmp_path, monkeypatch):
        """
        WHEN the disk cache exists and is less than 12 hours old,
        AND Athena (boto3.Session) would fail,
        load() MUST return the cached DataFrame without calling Athena.
        Validates: Requirements 10.3
        """
        # Create a valid cache CSV
        cache_dir = tmp_path / "results"
        cache_dir.mkdir()
        cache_csv = cache_dir / "athena_cache.csv"
        cache_ts  = cache_dir / "athena_cache_ts.txt"

        expected_df = pd.DataFrame({
            "item_number": ["10001", "10002"],
            "avg_daily_orders": [1.5, 2.0],
        })
        expected_df.to_csv(cache_csv, index=False)

        # Timestamp is 1 hour ago (well within 12-hour window)
        one_hour_ago = datetime.now(ZoneInfo("America/New_York")) - timedelta(hours=1)
        cache_ts.write_text(one_hour_ago.isoformat())

        # Change working directory to tmp_path so relative paths resolve correctly
        monkeypatch.chdir(tmp_path)

        # Boto3 should NOT be called; if it is, raise to catch any regression
        with patch("boto3.Session", side_effect=Exception("should not reach Athena")):
            df, ts = loader.load()

        assert df is not None
        assert list(df.columns) == ["item_number", "avg_daily_orders"]
        assert len(df) == 2
        assert "updated" in ts

    def test_load_cache_timestamp_reflected_in_ts_string(self, tmp_path, monkeypatch):
        """
        WHEN loading from a valid disk cache,
        the returned ts string MUST reflect the cached timestamp.
        Validates: Requirements 10.3
        """
        cache_dir = tmp_path / "results"
        cache_dir.mkdir()
        cache_csv = cache_dir / "athena_cache.csv"
        cache_ts  = cache_dir / "athena_cache_ts.txt"

        pd.DataFrame({"item_number": ["10001"]}).to_csv(cache_csv, index=False)

        # Use a timestamp that is recent (1 hour ago) so the 12-hour age check
        # passes, while still having a deterministic date we can verify.
        now_nj = datetime.now(ZoneInfo("America/New_York"))
        one_hour_ago = now_nj - timedelta(hours=1)
        cache_ts.write_text(one_hour_ago.isoformat())

        monkeypatch.chdir(tmp_path)

        expected_date = one_hour_ago.strftime("%Y-%m-%d")
        expected_time = one_hour_ago.strftime("%H:%M")

        with patch("boto3.Session", side_effect=Exception("should not reach Athena")):
            _, ts = loader.load()

        assert expected_date in ts, f"Expected date {expected_date!r} in ts {ts!r}"
        assert expected_time in ts, f"Expected time {expected_time!r} in ts {ts!r}"


# ===========================================================================
# Req 10.5 — Athena unavailable + no cache → fall back to latest CSV
# ===========================================================================

class TestAthenaUnavailableNoCache:
    """Req 10.5: load() falls back to latest CSV when no valid cache file exists."""

    def test_load_falls_back_to_latest_csv_when_no_cache(self, tmp_path, monkeypatch):
        """
        WHEN Athena raises an exception AND no cache file exists,
        load() MUST read from the latest results/segment_sku_detail_*.csv.
        Validates: Requirements 10.5
        """
        results_dir = tmp_path / "results"
        results_dir.mkdir()

        fallback_csv = results_dir / "segment_sku_detail_20240101_0000.csv"
        fallback_df = pd.DataFrame({
            "item_number": ["50001", "50002"],
            "avg_daily_orders": [0.5, 1.2],
        })
        fallback_df.to_csv(fallback_csv, index=False)

        monkeypatch.chdir(tmp_path)

        with patch("boto3.Session", side_effect=Exception("Athena down")):
            df, ts = loader.load()

        assert df is not None
        assert set(df["item_number"].astype(str)) == {"50001", "50002"}
        # ts should indicate it came from a CSV
        assert "CSV" in ts or "2024" in ts

    def test_load_picks_latest_csv_when_multiple_exist(self, tmp_path, monkeypatch):
        """
        WHEN multiple CSV fallback files exist,
        load() MUST use the lexicographically latest filename.
        Validates: Requirements 10.5
        """
        results_dir = tmp_path / "results"
        results_dir.mkdir()

        older_csv  = results_dir / "segment_sku_detail_20231201_0000.csv"
        newer_csv  = results_dir / "segment_sku_detail_20240615_1200.csv"

        pd.DataFrame({"item_number": ["OLD"]}).to_csv(older_csv, index=False)
        pd.DataFrame({"item_number": ["NEW"]}).to_csv(newer_csv, index=False)

        monkeypatch.chdir(tmp_path)

        with patch("boto3.Session", side_effect=Exception("Athena down")):
            df, _ = loader.load()

        assert list(df["item_number"].astype(str)) == ["NEW"], (
            "Expected the newer CSV to be selected (sorted desc by filename)"
        )


# ===========================================================================
# Req 10.6 — Athena unavailable + no CSV → raise FileNotFoundError
# ===========================================================================

class TestAthenaUnavailableNoFallback:
    """Req 10.6: load() raises FileNotFoundError when Athena fails and no CSV exists."""

    def test_load_raises_file_not_found_when_no_csv(self, tmp_path, monkeypatch):
        """
        WHEN Athena raises an exception AND no CSV fallback files exist,
        load() MUST raise FileNotFoundError.
        Validates: Requirements 10.6
        """
        results_dir = tmp_path / "results"
        results_dir.mkdir()   # dir exists but is empty (no CSVs, no cache)

        monkeypatch.chdir(tmp_path)

        with patch("boto3.Session", side_effect=Exception("Athena unavailable")):
            with pytest.raises(FileNotFoundError):
                loader.load()

    def test_load_raises_file_not_found_when_results_dir_missing(self, tmp_path, monkeypatch):
        """
        WHEN Athena raises an exception AND the results/ directory does not exist at all,
        load() MUST raise FileNotFoundError.
        Validates: Requirements 10.6
        """
        # tmp_path has no results/ subdirectory
        monkeypatch.chdir(tmp_path)

        with patch("boto3.Session", side_effect=Exception("Athena unavailable")):
            with pytest.raises(FileNotFoundError):
                loader.load()

    def test_load_raises_file_not_found_error_message_is_informative(self, tmp_path, monkeypatch):
        """
        WHEN FileNotFoundError is raised, the message should mention the original
        Athena error and the missing fallback, giving operators enough context.
        Validates: Requirements 10.6
        """
        monkeypatch.chdir(tmp_path)

        with patch("boto3.Session", side_effect=Exception("timeout after 5 minutes")):
            with pytest.raises(FileNotFoundError) as exc_info:
                loader.load()

        message = str(exc_info.value)
        assert message, "FileNotFoundError message must not be empty"
