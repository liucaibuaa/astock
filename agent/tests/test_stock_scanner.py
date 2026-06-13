"""Tests for src/tools/stock_scanner_tool.py."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

# Ensure the agent source root is on sys.path.
_AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_AGENT_ROOT))


@pytest.fixture
def sample_ohlcv_df():
    """Build a 60-bar OHLCV DataFrame for testing."""
    dates = pd.date_range("2026-01-01", periods=60, freq="B")
    n = len(dates)
    base = pd.Series(range(100, 100 + n), index=dates, dtype=float)
    return pd.DataFrame(
        {
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base + 0.3,
            "volume": base * 50,
        },
        index=pd.DatetimeIndex(dates, name="trade_date"),
    )


# --------------------------------------------------------------------------- #
# Strategy resolution                                                         #
# --------------------------------------------------------------------------- #


def test_strategy_list_complete():
    from src.tools.stock_scanner_tool import _SCANNER_STRATEGIES

    expected = {"elliott-wave", "chanlun", "smc", "technical-basic", "ichimoku", "candlestick"}
    assert set(_SCANNER_STRATEGIES) == expected
    for key, rel_path in _SCANNER_STRATEGIES.items():
        full = _AGENT_ROOT / rel_path
        assert full.is_file(), f"missing {full}"


def test_scan_stocks_unknown_strategy():
    from src.tools.stock_scanner_tool import scan_stocks

    result = scan_stocks("nonexistent", "2026-06-13")
    assert result["status"] == "error"
    assert "nonexistent" in result["error"]


def test_scan_stocks_invalid_date():
    from src.tools.stock_scanner_tool import scan_stocks

    result = scan_stocks("ichimoku", "not-a-date")
    assert result["status"] == "error"


def test_scan_stocks_empty_codes():
    from src.tools.stock_scanner_tool import scan_stocks

    result = scan_stocks("ichimoku", "2026-06-13", codes=[])
    assert result["status"] == "error"


# --------------------------------------------------------------------------- #
# SignalEngine loading                                                         #
# --------------------------------------------------------------------------- #


def test_load_signal_engine_all_strategies():
    """Every listed strategy should have a loadable SignalEngine (best-effort).

    Strategies that require external packages (czsc for chanlun,
    smartmoneyconcepts for smc) are skipped gracefully when those
    packages are not installed.
    """
    from src.tools.stock_scanner_tool import _SCANNER_STRATEGIES, _load_signal_engine

    external_deps = {
        "chanlun": "czsc",
        "smc": "smartmoneyconcepts",
    }

    for key in _SCANNER_STRATEGIES:
        cls = _load_signal_engine(key)
        if cls is None:
            pkg = external_deps.get(key)
            if pkg:
                # These strategies need external Python packages that may not
                # be available in every environment — treat as skipped.
                import importlib

                try:
                    importlib.import_module(pkg)
                except ImportError:
                    import pytest as _pt

                    _pt.skip(f"{pkg} not installed — {key} engine unavailable")
            raise AssertionError(f"Failed to load SignalEngine for {key}")
        assert hasattr(cls, "generate"), f"SignalEngine for {key} missing generate()"


def test_load_signal_engine_missing():
    from src.tools.stock_scanner_tool import _load_signal_engine

    assert _load_signal_engine("nonexistent") is None


# --------------------------------------------------------------------------- #
# Signal check on a single stock                                               #
# --------------------------------------------------------------------------- #


def test_check_signal_long(sample_ohlcv_df):
    """_check_signal should return a valid result for a non-zero signal."""
    from src.tools.stock_scanner_tool import _check_signal

    class FakeEngine:
        def generate(self, data_map):
            sig = pd.Series(0.0, index=sample_ohlcv_df.index)
            # Set a long signal on the last day
            sig.iloc[-1] = 1.0
            return {"600519.SH": sig}

    target = sample_ohlcv_df.index[-1].strftime("%Y-%m-%d")
    res = _check_signal(FakeEngine(), "600519.SH", sample_ohlcv_df, target)
    assert res is not None
    assert res["code"] == "600519.SH"
    assert res["signal"] == 1
    assert res["signal_label"] == "long"


def test_check_signal_short(sample_ohlcv_df):
    from src.tools.stock_scanner_tool import _check_signal

    class FakeEngine:
        def generate(self, data_map):
            sig = pd.Series(0.0, index=sample_ohlcv_df.index)
            sig.iloc[-1] = -1.0
            return {"000858.SZ": sig}

    target = sample_ohlcv_df.index[-1].strftime("%Y-%m-%d")
    res = _check_signal(FakeEngine(), "000858.SZ", sample_ohlcv_df, target)
    assert res is not None
    assert res["signal"] == -1
    assert res["signal_label"] == "short"


def test_check_signal_flat(sample_ohlcv_df):
    from src.tools.stock_scanner_tool import _check_signal

    class FakeEngine:
        def generate(self, data_map):
            sig = pd.Series(0.0, index=sample_ohlcv_df.index)
            return {"600519.SH": sig}

    target = sample_ohlcv_df.index[-1].strftime("%Y-%m-%d")
    res = _check_signal(FakeEngine(), "600519.SH", sample_ohlcv_df, target)
    assert res is not None
    assert res["signal"] == 0
    assert res["signal_label"] == "flat"


def test_check_signal_date_not_in_index(sample_ohlcv_df):
    from src.tools.stock_scanner_tool import _check_signal

    class FakeEngine:
        def generate(self, data_map):
            sig = pd.Series(0.0, index=sample_ohlcv_df.index)
            return {"600519.SH": sig}

    # Date BEFORE the first bar — should return None (no previous bar to fall back to)
    res = _check_signal(FakeEngine(), "600519.SH", sample_ohlcv_df, "2010-01-01")
    assert res is None


def test_check_signal_weekend_fallback(sample_ohlcv_df):
    """When target is a weekend, fall back to the nearest previous trading day."""
    from src.tools.stock_scanner_tool import _check_signal

    class FakeEngine:
        def generate(self, data_map):
            sig = pd.Series(0.0, index=sample_ohlcv_df.index)
            sig.iloc[-1] = 1.0
            return {"600519.SH": sig}

    # Pick the first Saturday after the last data bar.
    last_friday = sample_ohlcv_df.index[-1]
    saturday = last_friday + pd.Timedelta(days=1)
    if saturday.day_name() != "Saturday":
        saturday += pd.Timedelta(days=(5 - saturday.dayofweek) % 7)
    target = saturday.strftime("%Y-%m-%d")

    res = _check_signal(FakeEngine(), "600519.SH", sample_ohlcv_df, target)
    assert res is not None
    assert res["signal"] == 1
    assert res["signal_label"] == "long"
    assert "effective_date" in res


# --------------------------------------------------------------------------- #
# Fallback code list                                                          #
# --------------------------------------------------------------------------- #


def test_csi300_fallback_has_30_codes():
    from src.tools.stock_scanner_tool import _CSI300_FALLBACK_CODES

    assert len(_CSI300_FALLBACK_CODES) == 30
    assert all(c.endswith((".SH", ".SZ")) for c in _CSI300_FALLBACK_CODES)


def test_get_csi300_codes_fallback_without_token(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    from src.tools.stock_scanner_tool import _get_csi300_codes

    codes = _get_csi300_codes()
    # Without TUSHARE_TOKEN, the scanner tries akshare first.  If akshare is
    # reachable it will return ~300 codes; if not, it degrades to the 30-name
    # fallback.  Both outcomes are valid.
    assert len(codes) >= 30


# --------------------------------------------------------------------------- #
# Name lookup                                                                  #
# --------------------------------------------------------------------------- #


def test_name_lookup_empty_without_token(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    from src.tools.stock_scanner_tool import _name_lookup

    assert _name_lookup() == {}


# --------------------------------------------------------------------------- #
# SignalLabel mapping                                                         #
# --------------------------------------------------------------------------- #


def test_signal_labels():
    from src.tools.stock_scanner_tool import _SIGNAL_LABELS

    assert _SIGNAL_LABELS[1] == "long"
    assert _SIGNAL_LABELS[-1] == "short"
    assert _SIGNAL_LABELS[0] == "flat"


# --------------------------------------------------------------------------- #
# MCP Tool                                                                     #
# --------------------------------------------------------------------------- #


def test_stock_scanner_tool_schema():
    from src.tools.stock_scanner_tool import StockScannerTool

    tool = StockScannerTool()
    schema = tool.to_openai_schema()
    assert schema["function"]["name"] == "scan_stocks"
    assert "required" in schema["function"]["parameters"]
    assert "strategy" in schema["function"]["parameters"]["required"]


def test_stock_scanner_tool_execute_unknown_strategy():
    from src.tools.stock_scanner_tool import StockScannerTool

    tool = StockScannerTool()
    result = tool.execute(strategy="nonexistent", target_date="2026-06-13")
    import json

    d = json.loads(result)
    assert d["status"] == "error"
