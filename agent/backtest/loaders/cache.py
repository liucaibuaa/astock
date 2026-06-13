"""Local disk cache for OHLCV data with incremental updates.

The cache is transparent to callers: a ``CachedLoader`` wraps any existing
DataLoader and intercepts ``fetch()`` calls.  On first access it persists the
full response; on later accesses it computes date gaps and fetches only the
missing bars, then merges and re-persists.

Cache layout::

    ~/.vibe-trading/cache/symbols/<source>/<interval>/<symbol-normalized>.csv

Cache key is ``(source, symbol, interval)``.  Different data sources never
share a cache file, which avoids mixing inconsistent prices or columns.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Cache path helpers                                                          #
# --------------------------------------------------------------------------- #


def _cache_root() -> Path:
    """Return the user-level cache directory for symbol data."""
    return Path.home() / ".vibe-trading" / "cache" / "symbols"


def _normalize_symbol_for_path(symbol: str) -> str:
    """Make a symbol safe for use as a filesystem name.

    Replaces path separators and colons, which appear in some market symbology
    (e.g. ``EUR/USD``, ``BTC-USDT`` stays readable).
    """
    return symbol.replace("/", "-").replace("\\", "-").replace(":", "-")


def _cache_path(source: str, symbol: str, interval: str) -> Path:
    """Return the CSV path for a single symbol's cached data."""
    root = _cache_root() / source / interval
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{_normalize_symbol_for_path(symbol)}.csv"


# --------------------------------------------------------------------------- #
# Read / write                                                                #
# --------------------------------------------------------------------------- #


def load_cached(
    source: str,
    symbol: str,
    interval: str,
    *,
    fields: list[str] | None = None,
    required_columns: tuple[str, ...] = ("open", "high", "low", "close", "volume"),
) -> pd.DataFrame | None:
    """Load cached OHLCV data for one symbol if it exists and looks valid.

    Args:
        source: Data source name (e.g. ``tushare``).
        symbol: Normalized symbol as passed to the loader.
        interval: Bar interval (e.g. ``1D``).
        fields: Optional extra columns that must also be present in the cache.
        required_columns: Minimum columns that must be present.

    Returns:
        Cached DataFrame with DatetimeIndex named ``trade_date``, or ``None``
        if no valid cache exists.
    """
    path = _cache_path(source, symbol, interval)
    if not path.exists():
        return None

    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read cache for %s/%s/%s: %s", source, symbol, interval, exc)
        return None

    if df.index.name != "trade_date":
        # Tolerate caches where the index name was not persisted correctly.
        df.index.name = "trade_date"

    if df.empty:
        logger.warning("Empty cache file for %s/%s/%s; ignoring", source, symbol, interval)
        return None

    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        logger.warning(
            "Cache for %s/%s/%s missing columns %s; treating as miss",
            source,
            symbol,
            interval,
            missing,
        )
        return None

    if fields:
        missing_extra = [c for c in fields if c not in df.columns]
        if missing_extra:
            logger.info(
                "Cache for %s/%s/%s missing requested fields %s; re-fetching",
                source,
                symbol,
                interval,
                missing_extra,
            )
            return None

    # Coerce numeric columns; non-numeric values become NaN and will be dropped
    # by downstream normalization, but we keep the frame itself.
    for col in df.columns:
        if col in required_columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def save_cached(source: str, symbol: str, interval: str, df: pd.DataFrame) -> None:
    """Persist OHLCV data for one symbol, overwriting any previous cache.

    Args:
        source: Data source name.
        symbol: Normalized symbol.
        interval: Bar interval.
        df: DataFrame with DatetimeIndex.  Expected index name ``trade_date``.
    """
    if df is None or df.empty:
        return

    path = _cache_path(source, symbol, interval)
    path.parent.mkdir(parents=True, exist_ok=True)

    out = df.copy()
    if out.index.name is None:
        out.index.name = "trade_date"

    try:
        out.to_csv(path, index=True)
        logger.debug("Wrote cache %s (%d rows)", path, len(out))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write cache %s: %s", path, exc)


# --------------------------------------------------------------------------- #
# Incremental fetch logic                                                     #
# --------------------------------------------------------------------------- #


def _parse_date(value: str) -> pd.Timestamp:
    """Parse a YYYY-MM-DD string into a Timestamp."""
    return pd.Timestamp(str(value))


def _date_gaps(
    requested_start: str,
    requested_end: str,
    cached_df: pd.DataFrame,
) -> list[tuple[str, str]]:
    """Compute date ranges that need to be fetched to cover the request.

    The cache is assumed to be contiguous between its min and max dates.
    Gaps can appear at the head (request starts before cache) or tail (request
    ends after cache).

    Args:
        requested_start: Start date of the new request (YYYY-MM-DD).
        requested_end: End date of the new request (YYYY-MM-DD).
        cached_df: Existing cached data.

    Returns:
        List of ``(gap_start, gap_end)`` tuples in YYYY-MM-DD format.
    """
    req_start = _parse_date(requested_start)
    req_end = _parse_date(requested_end)

    if cached_df is None or cached_df.empty:
        return [(requested_start, requested_end)]

    cached_min = cached_df.index.min()
    cached_max = cached_df.index.max()

    gaps: list[tuple[str, str]] = []

    # Head gap: requested period starts before cached data.
    if req_start < cached_min:
        head_end = min(req_end, cached_min - pd.Timedelta(days=1))
        if head_end >= req_start:
            gaps.append((req_start.strftime("%Y-%m-%d"), head_end.strftime("%Y-%m-%d")))

    # Tail gap: requested period ends after cached data.
    if req_end > cached_max:
        tail_start = max(req_start, cached_max + pd.Timedelta(days=1))
        if tail_start <= req_end:
            # Clamp to today: never ask a loader for future dates.
            today = pd.Timestamp.now().normalize()
            if tail_start > today:
                # Entire tail is in the future; nothing to fetch.
                pass
            else:
                tail_end = min(req_end, today)
                gaps.append((tail_start.strftime("%Y-%m-%d"), tail_end.strftime("%Y-%m-%d")))

    return gaps


def _merge_cached_and_fetched(
    cached_df: pd.DataFrame | None,
    fetched_frames: list[pd.DataFrame],
) -> pd.DataFrame:
    """Concatenate cached and freshly fetched frames, removing duplicates.

    When duplicate index values exist, fetched data wins so that corrections
    from the upstream source are reflected.
    """
    parts: list[pd.DataFrame] = []
    if cached_df is not None and not cached_df.empty:
        parts.append(cached_df)
    parts.extend(fetched_frames)

    if not parts:
        return pd.DataFrame()

    merged = pd.concat(parts, axis=0)
    merged = merged[~merged.index.duplicated(keep="last")]
    return merged.sort_index()


def fetch_with_cache(
    loader: Any,
    codes: list[str],
    start_date: str,
    end_date: str,
    *,
    interval: str = "1D",
    fields: list[str] | None = None,
    refresh_cache: bool = False,
) -> dict[str, pd.DataFrame]:
    """Fetch data through ``loader.fetch`` with transparent local caching.

    Args:
        loader: A DataLoader instance with ``name`` and ``fetch()``.
        codes: List of normalized symbol codes.
        start_date: Request start date (YYYY-MM-DD).
        end_date: Request end date (YYYY-MM-DD).
        interval: Bar interval.
        fields: Optional extra fields (passed to loader).
        refresh_cache: If True, ignore cache and re-fetch full range.

    Returns:
        Mapping ``code -> DataFrame`` for all codes.  Missing data yields a
        ``None`` or absent entry, matching existing loader conventions.
    """
    source = getattr(loader, "name", "unknown")
    result: dict[str, pd.DataFrame] = {}

    for code in codes:
        cached_df = None if refresh_cache else load_cached(source, code, interval, fields=fields)

        if cached_df is None:
            # Full fetch on cache miss or refresh.
            fetched = loader.fetch(
                [code],
                start_date,
                end_date,
                interval=interval,
                fields=fields,
            )
            df = fetched.get(code) if fetched else None
            if df is not None and not df.empty:
                save_cached(source, code, interval, df)
            if df is not None:
                result[code] = df
            continue

        # Cache hit: slice or fill gaps.
        gaps = _date_gaps(start_date, end_date, cached_df)
        if not gaps:
            result[code] = cached_df.loc[start_date:end_date].copy()
            logger.debug("Cache hit for %s/%s/%s", source, code, interval)
            continue

        logger.info(
            "Incremental fetch for %s/%s/%s: gaps %s",
            source,
            code,
            interval,
            gaps,
        )
        fetched_frames: list[pd.DataFrame] = []
        for gap_start, gap_end in gaps:
            fetched = loader.fetch(
                [code],
                gap_start,
                gap_end,
                interval=interval,
                fields=fields,
            )
            gap_df = fetched.get(code) if fetched else None
            if gap_df is not None and not gap_df.empty:
                fetched_frames.append(gap_df)

        merged = _merge_cached_and_fetched(cached_df, fetched_frames)
        if not merged.empty:
            save_cached(source, code, interval, merged)
            result[code] = merged.loc[start_date:end_date].copy()

    return result


# --------------------------------------------------------------------------- #
# Wrapper class                                                               #
# --------------------------------------------------------------------------- #


class CachedLoader:
    """Transparent caching wrapper around any DataLoader.

    Implements the same ``fetch()`` surface so it can be dropped in anywhere
    a loader instance is used.
    """

    def __init__(self, loader: Any, refresh_cache: bool = False):
        self._loader = loader
        self._refresh_cache = refresh_cache

    @property
    def name(self) -> str:
        return getattr(self._loader, "name", "unknown")

    def is_available(self) -> bool:
        return bool(getattr(self._loader, "is_available", lambda: True)())

    def fetch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch data through the wrapped loader with local cache support."""
        return fetch_with_cache(
            self._loader,
            codes,
            start_date,
            end_date,
            interval=interval,
            fields=fields,
            refresh_cache=self._refresh_cache,
        )
