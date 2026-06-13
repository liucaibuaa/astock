"""Tests for backtest/loaders/cache.py."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from backtest.loaders.cache import (
    CachedLoader,
    _cache_path,
    _date_gaps,
    _normalize_symbol_for_path,
    fetch_with_cache,
    load_cached,
    save_cached,
)


@pytest.fixture
def tmp_cache_root(monkeypatch, tmp_path):
    """Redirect the cache root to a temporary directory."""
    from backtest import loaders

    def _root():
        return tmp_path / "symbols"

    monkeypatch.setattr("backtest.loaders.cache._cache_root", _root)
    return _root()


class FakeLoader:
    """Stub loader that records fetch calls and returns configurable data."""

    name = "fake"

    def __init__(self, data: dict[str, pd.DataFrame]) -> None:
        self.data = data
        self.calls: list[tuple[str, str, str]] = []

    def fetch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        for code in codes:
            self.calls.append((code, start_date, end_date))
        return {code: self.data[code].loc[start_date:end_date].copy() for code in codes if code in self.data}


def _make_df(start: str, end: str) -> pd.DataFrame:
    """Build a trivial OHLCV DataFrame for one symbol."""
    dates = pd.date_range(start, end, freq="B")  # business days
    n = len(dates)
    base = pd.Series(range(100, 100 + n), index=dates, dtype=float)
    return pd.DataFrame(
        {
            "open": base,
            "high": base + 1,
            "low": base - 1,
            "close": base + 0.5,
            "volume": base * 100,
        },
        index=pd.DatetimeIndex(dates, name="trade_date"),
    )


def test_normalize_symbol_for_path():
    assert _normalize_symbol_for_path("600519.SH") == "600519.SH"
    assert _normalize_symbol_for_path("EUR/USD") == "EUR-USD"
    assert _normalize_symbol_for_path("BTC\\USDT") == "BTC-USDT"


def test_save_and_load_cached(tmp_cache_root):
    df = _make_df("2024-01-01", "2024-01-10")
    save_cached("fake", "600519.SH", "1D", df)

    path = _cache_path("fake", "600519.SH", "1D")
    assert path.exists()

    loaded = load_cached("fake", "600519.SH", "1D")
    assert loaded is not None
    assert len(loaded) == len(df)
    assert list(loaded.columns) == list(df.columns)
    pd.testing.assert_index_equal(loaded.index, df.index)


def test_load_cached_missing_returns_none(tmp_cache_root):
    assert load_cached("fake", "NONE", "1D") is None


def test_date_gaps_no_cache():
    gaps = _date_gaps("2024-01-01", "2024-01-31", None)
    assert gaps == [("2024-01-01", "2024-01-31")]


def test_date_gaps_fully_covered():
    cached = _make_df("2024-01-01", "2024-01-31")
    assert _date_gaps("2024-01-05", "2024-01-25", cached) == []


def test_date_gaps_tail_only():
    cached = _make_df("2024-01-01", "2024-01-15")
    gaps = _date_gaps("2024-01-01", "2024-01-31", cached)
    assert len(gaps) == 1
    assert gaps[0] == ("2024-01-16", "2024-01-31")


def test_date_gaps_head_only():
    cached = _make_df("2024-01-15", "2024-01-31")
    gaps = _date_gaps("2024-01-01", "2024-01-31", cached)
    assert len(gaps) == 1
    assert gaps[0] == ("2024-01-01", "2024-01-14")


def test_date_gaps_both_sides():
    cached = _make_df("2024-01-10", "2024-01-20")
    gaps = _date_gaps("2024-01-01", "2024-01-31", cached)
    assert len(gaps) == 2
    assert gaps[0] == ("2024-01-01", "2024-01-09")
    # 2024-01-20 is a Saturday; cached business-day data ends on 2024-01-19.
    assert gaps[1] == ("2024-01-20", "2024-01-31")


def test_fetch_with_cache_first_persists(tmp_cache_root):
    df = _make_df("2024-01-01", "2024-01-10")
    loader = FakeLoader({"600519.SH": df})

    result = fetch_with_cache(
        loader,
        ["600519.SH"],
        "2024-01-01",
        "2024-01-10",
        interval="1D",
    )

    assert len(loader.calls) == 1
    assert "600519.SH" in result
    assert len(result["600519.SH"]) == len(df)
    assert _cache_path("fake", "600519.SH", "1D").exists()


def test_fetch_with_cache_second_uses_cache(tmp_cache_root):
    df = _make_df("2024-01-01", "2024-01-10")
    loader = FakeLoader({"600519.SH": df})

    fetch_with_cache(loader, ["600519.SH"], "2024-01-01", "2024-01-10", interval="1D")
    loader.calls.clear()

    result = fetch_with_cache(loader, ["600519.SH"], "2024-01-01", "2024-01-10", interval="1D")

    assert len(loader.calls) == 0
    assert len(result["600519.SH"]) == len(df)


def test_fetch_with_cache_incremental_tail(tmp_cache_root):
    df = _make_df("2024-01-01", "2024-01-31")
    loader = FakeLoader({"600519.SH": df})

    # First fetch: cache 01-01 to 01-10.
    fetch_with_cache(loader, ["600519.SH"], "2024-01-01", "2024-01-10", interval="1D")
    loader.calls.clear()

    # Second fetch: extend to 01-31. Should only fetch the tail gap.
    result = fetch_with_cache(loader, ["600519.SH"], "2024-01-01", "2024-01-31", interval="1D")

    assert len(loader.calls) == 1
    assert loader.calls[0] == ("600519.SH", "2024-01-11", "2024-01-31")
    assert len(result["600519.SH"]) == len(df)


def test_fetch_with_cache_refresh(tmp_cache_root):
    df = _make_df("2024-01-01", "2024-01-10")
    loader = FakeLoader({"600519.SH": df})

    fetch_with_cache(loader, ["600519.SH"], "2024-01-01", "2024-01-10", interval="1D")
    loader.calls.clear()

    result = fetch_with_cache(
        loader,
        ["600519.SH"],
        "2024-01-01",
        "2024-01-10",
        interval="1D",
        refresh_cache=True,
    )

    assert len(loader.calls) == 1
    assert len(result["600519.SH"]) == len(df)


def test_cached_loader_wrapper(tmp_cache_root):
    df = _make_df("2024-01-01", "2024-01-10")
    loader = FakeLoader({"600519.SH": df})
    cached = CachedLoader(loader)

    result1 = cached.fetch(["600519.SH"], "2024-01-01", "2024-01-10", interval="1D")
    result2 = cached.fetch(["600519.SH"], "2024-01-01", "2024-01-10", interval="1D")

    assert len(loader.calls) == 1
    assert len(result1["600519.SH"]) == len(result2["600519.SH"])


def test_cached_loader_passes_name_and_availability():
    class Dummy:
        name = "dummy"

        def is_available(self):
            return True

        def fetch(self, *args, **kwargs):
            return {}

    wrapped = CachedLoader(Dummy())
    assert wrapped.name == "dummy"
    assert wrapped.is_available() is True
