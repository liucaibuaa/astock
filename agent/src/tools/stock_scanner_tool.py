"""Stock scanner: screen a universe with existing single-stock strategy engines.

Resolves a strategy skill (e.g. ``ichimoku``, ``elliott-wave``), loads its
``example_signal_engine.py``, fetches OHLCV data for every stock in the chosen
universe, runs the ``SignalEngine``, and returns which stocks triggered a
non-zero signal on the requested trading date.

Strategy → skill mapping::

    _SCANNER_STRATEGIES = {
        "elliott-wave":    "agent/src/skills/elliott-wave/example_signal_engine.py",
        "chanlun":         "agent/src/skills/chanlun/example_signal_engine.py",
        "smc":             "agent/src/skills/smc/example_signal_engine.py",
        "technical-basic": "agent/src/skills/technical-basic/example_signal_engine.py",
        "ichimoku":        "agent/src/skills/ichimoku/example_signal_engine.py",
        "candlestick":     "agent/src/skills/candlestick/example_signal_engine.py",
    }

Universe modes:
  * ``universe="csi300"`` — fetch CSI 300 constituents via Tushare (requires TUSHARE_TOKEN)
  * ``universe="sp500"`` — fetch S&P 500 constituents via Wikipedia + yfinance
  * ``codes=[...]`` — user-provided explicit list
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

from src.agent.tools import BaseTool

# Load .env so TUSHARE_TOKEN / other config is visible to the scanner.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Strategy → skill file resolver                                               #
# --------------------------------------------------------------------------- #

_AGENT_ROOT = Path(__file__).resolve().parents[2]

_SCANNER_STRATEGIES: dict[str, str] = {
    "elliott-wave": "src/skills/elliott-wave/example_signal_engine.py",
    "chanlun": "src/skills/chanlun/example_signal_engine.py",
    "smc": "src/skills/smc/example_signal_engine.py",
    "technical-basic": "src/skills/technical-basic/example_signal_engine.py",
    "ichimoku": "src/skills/ichimoku/example_signal_engine.py",
    "candlestick": "src/skills/candlestick/example_signal_engine.py",
}

# Number of concurrent data fetches.  Tushare free tier allows ~200 calls/min;
# 4 workers stays well under that cap for a 300-name list.
_MAX_FETCH_WORKERS = 4

# --------------------------------------------------------------------------- #
# CSI 300 fallback                                          #
# --------------------------------------------------------------------------- #

_CSI300_FALLBACK_CODES: list[str] = [
    "600519.SH", "000858.SZ", "601398.SH", "601939.SH", "601288.SH",
    "601318.SH", "600036.SH", "000001.SZ", "601166.SH", "600900.SH",
    "601012.SH", "000333.SZ", "002415.SZ", "600276.SH", "601888.SH",
    "600030.SH", "000651.SZ", "000725.SZ", "601668.SH", "600887.SH",
    "600809.SH", "600309.SH", "601899.SH", "002714.SZ", "601088.SH",
    "600585.SH", "601857.SH", "600031.SH", "601390.SH", "601766.SH",
]

_SIGNAL_LABELS: dict[int, str] = {1: "long", 0: "flat", -1: "short"}

# Module-level cache for Tushare stock_basic result, shared between
# _get_all_a_codes() and _name_lookup() so we only call the API once.
_stock_basic_cache: dict[str, str] | None = None

# --------------------------------------------------------------------------- #
# Strategy engine loader                                                        #
# --------------------------------------------------------------------------- #


def _load_signal_engine(strategy: str):
    """Load the SignalEngine class from a strategy skill's example file.

    Performs a light AST safety check (rejecting dangerous constructs like
    ``exec``/``eval``/``__import__`` at the top level) but allows benign
    top-level ``if __name__ == "__main__"`` blocks that are common in skill
    example files.

    Args:
        strategy: Strategy key (must be in ``_SCANNER_STRATEGIES``).

    Returns:
        SignalEngine class, or ``None`` on any failure.
    """
    import ast

    if strategy not in _SCANNER_STRATEGIES:
        return None

    rel_path = _SCANNER_STRATEGIES[strategy]
    file_path = _AGENT_ROOT / rel_path
    if not file_path.is_file():
        logger.warning("scanner: skill file not found: %s", file_path)
        return None

    # Light AST safety scan: reject dangerous top-level constructs but allow
    # `if __name__ == "__main__"` blocks (common in skill example files).
    source_text = file_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source_text, filename=str(file_path))
    except SyntaxError as exc:
        logger.warning("scanner: syntax error in %s: %s", strategy, exc)
        return None

    _DANGEROUS_CALLS = {"exec", "eval", "compile", "__import__", "open"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute) and node.func.attr in _DANGEROUS_CALLS:
                name = node.func.attr
            if name in _DANGEROUS_CALLS:
                logger.warning("scanner: dangerous call %r in %s", name, strategy)
                return None
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(p in (node.module or "") for p in ("subprocess", "socket", "ctypes", "multiprocessing")):
                    logger.warning("scanner: dangerous import %r in %s", node.module, strategy)
                    return None

    module_name = f"scanner_{strategy.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.warning("scanner: failed to load %s: %s", strategy, exc)
        return None

    engine_cls = getattr(module, "SignalEngine", None)
    if engine_cls is None:
        return None

    # Validate interface: must be callable with no required args and have generate()
    try:
        sig = inspect.signature(engine_cls.__init__)
        required = [
            p.name for p in sig.parameters.values()
            if p.name != "self" and p.default is inspect.Parameter.empty
            and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        ]
        if required:
            logger.warning("scanner: %s SignalEngine.__init__ has required args: %s", strategy, required)
            return None
        if not callable(getattr(engine_cls, "generate", None)):
            return None
    except Exception as exc:
        logger.warning("scanner: %s SignalEngine interface check failed: %s", strategy, exc)
        return None

    return engine_cls


# --------------------------------------------------------------------------- #
# Universe helpers                                                              #
# --------------------------------------------------------------------------- #


def _fetch_stock_basic() -> dict[str, str]:
    """Fetch Tushare stock_basic once and cache on success.

    Returns:
        Dict mapping ts_code → name.  Empty dict on failure (not cached,
        so later calls will retry).
    """
    global _stock_basic_cache
    if _stock_basic_cache is not None:
        return _stock_basic_cache

    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token or token == "your-tushare-token":
        return {}

    try:
        import tushare as ts
        pro = ts.pro_api(token)
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
        if df is None or df.empty:
            return {}
        _stock_basic_cache = dict(zip(df["ts_code"].astype(str), df["name"].astype(str)))
        logger.info("scanner: stock_basic = %d names from tushare", len(_stock_basic_cache))
        return _stock_basic_cache
    except Exception as exc:
        logger.warning("scanner: tushare stock_basic failed: %s", exc)
        return {}


def _name_lookup() -> dict[str, str]:
    """Build code → name mapping.  Uses the cached stock_basic result."""
    return _fetch_stock_basic()


def _get_csi300_codes() -> list[str]:
    """Fetch current CSI 300 constituent codes.

    Tries akshare (free, no auth), then the 30-name fallback.
    Tushare free tier does not include index_weight, so we use akshare
    for the list and Tushare for OHLCV data when a token is configured.
    """
    codes = _try_akshare_index("399300")
    if codes:
        return codes

    logger.info("scanner: using csi300 %d-name fallback", len(_CSI300_FALLBACK_CODES))
    return list(_CSI300_FALLBACK_CODES)


# --------------------------------------------------------------------------- #
# Per-stock fetch + signal check                                                #
# --------------------------------------------------------------------------- #


def _fetch_one_stock(
    code: str,
    target_date: str,
    source: str = "tushare",
    *,
    interval: str = "1D",
) -> dict[str, Any] | None:
    """Fetch OHLCV data for one stock with per-source fallback.

    Tries *source*, then mootdx, then akshare.  Wraps each loader in
    CachedLoader for local persistence.  Retries once on empty results.

    Args:
        code: Stock code, e.g. ``600519.SH``.
        target_date: YYYY-MM-DD fetch end date.
        source: Preferred data source name.
        interval: Bar interval.  ``1D`` (default), ``1H``, ``30m``, ``15m``, ``5m``.

    Returns:
        Dict with keys ``code``, ``df``, ``effective_source``, or ``None``.
    """
    import time as _time

    from backtest.loaders.cache import CachedLoader
    from backtest.loaders.registry import LOADER_REGISTRY

    # Lookback: 400 calendar days for daily (enough for all strategies),
    # 60 days for intraday (1 day of 30m bars = 16 bars, 60 days = 960).
    lookback_days = 400 if interval == "1D" else 60
    start_dt = pd.Timestamp(target_date) - pd.Timedelta(days=lookback_days)
    start_date = start_dt.strftime("%Y-%m-%d")

    # Per-source fallback chain.  Each entry is (source_name, loader_cls_or_None).
    # We build it dynamically so only available loaders are included.
    _fallback_chain: list[tuple[str, Any]] = []

    # 1. Preferred source
    try:
        from backtest.loaders.registry import get_loader_cls_with_fallback as _glc2
        _fallback_chain.append((source, _glc2(source)))
    except Exception:
        pass

    # 2. mootdx (free, no auth, TDX TCP protocol)
    if source != "mootdx" and "mootdx" in LOADER_REGISTRY:
        try:
            inst = LOADER_REGISTRY["mootdx"]()
            if inst.is_available():
                _fallback_chain.append(("mootdx", LOADER_REGISTRY["mootdx"]))
        except Exception:
            pass

    # 3. akshare (free HTTP API, broadest coverage)
    if source != "akshare" and "akshare" in LOADER_REGISTRY:
        try:
            inst = LOADER_REGISTRY["akshare"]()
            if inst.is_available():
                _fallback_chain.append(("akshare", LOADER_REGISTRY["akshare"]))
        except Exception:
            pass

    # 4. a_stock_data (TDX-backed via Baidu K-line)
    if source != "a_stock_data" and "a_stock_data" in LOADER_REGISTRY:
        try:
            inst = LOADER_REGISTRY["a_stock_data"]()
            if inst.is_available():
                _fallback_chain.append(("a_stock_data", LOADER_REGISTRY["a_stock_data"]))
        except Exception:
            pass

    for src_name, LoaderCls in _fallback_chain:
        try:
            loader = LoaderCls()
            cached = CachedLoader(loader)
            result = cached.fetch([code], start_date, target_date, interval=interval)
            df = result.get(code)
            if df is not None and not df.empty and len(df) >= 20:
                return {"code": code, "df": df, "effective_source": src_name}

            # One retry after a short delay.
            _time.sleep(2)
            result = cached.fetch([code], start_date, target_date, interval=interval)
            df = result.get(code)
            if df is not None and not df.empty and len(df) >= 20:
                return {"code": code, "df": df, "effective_source": src_name}

            logger.debug("scanner: %s returned empty/short for %s, trying next fallback", src_name, code)
        except Exception as exc:
            logger.debug("scanner: %s failed for %s: %s", src_name, code, exc)
            continue

    return None


def _check_signal(
    engine: Any,
    code: str,
    df: pd.DataFrame,
    target_date: str,
) -> dict[str, Any] | None:
    """Run SignalEngine on one stock and extract the signal on target_date.

    When ``target_date`` is not a trading day (weekend, holiday) it
    falls back to the nearest previous trading bar.

    Args:
        engine: Instantiated SignalEngine.
        code: Stock code.
        df: OHLCV DataFrame.
        target_date: YYYY-MM-DD date.

    Returns:
        Dict with ``code``, ``signal``, ``signal_label``, or ``None`` if no
        signal triggered or no usable date is found.
    """
    try:
        sig = engine.generate({code: df})
        series = sig.get(code)
        if series is None:
            return None

        # Normalize target_date to match the index type.
        target_dt = pd.Timestamp(target_date)

        # Direct hit: the requested date is itself a trading day.
        if target_dt in series.index:
            val = series.loc[target_dt]
            if pd.notna(val):
                val = float(val)
                return {
                    "code": code,
                    "signal": int(val) if val in (-1, 0, 1) else round(val, 2),
                    "signal_label": _SIGNAL_LABELS.get(int(val), "partial"),
                }
            return None

        # Fallback: target_date is a weekend or holiday — use the nearest
        # previous bar.
        prev_bars = series.index[series.index < target_dt]
        if len(prev_bars) == 0:
            return None
        nearest_date = prev_bars[-1]
        val = series.loc[nearest_date]
        if pd.isna(val):
            return None

        val = float(val)
        effective_date = (
            nearest_date.isoformat()
            if hasattr(nearest_date, "isoformat")
            else str(nearest_date)[:10]
        )
        # Only report non-zero signals by default.
        return {
            "code": code,
            "signal": int(val) if val in (-1, 0, 1) else round(val, 2),
            "signal_label": _SIGNAL_LABELS.get(int(val), "partial"),
            "effective_date": effective_date,
        }
    except Exception as exc:
        logger.debug("scanner: signal check failed for %s: %s", code, exc)
        return None


# --------------------------------------------------------------------------- #
# Main scanner entry point                                                      #
# --------------------------------------------------------------------------- #


def _collect_trading_dates(
    dfs: dict[str, pd.DataFrame],
    start_date: str,
    end_date: str,
) -> pd.DatetimeIndex:
    """Collect the union of all trading dates that exist in the data.

    Filters to the requested [start_date, end_date] window.
    """
    all_dates: set[pd.Timestamp] = set()
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date)
    for df in dfs.values():
        if df is None or df.empty:
            continue
        all_dates.update(d for d in df.index if start_dt <= d <= end_dt)
    return pd.DatetimeIndex(sorted(all_dates))


def scan_stocks(
    strategy: str | list[str],
    target_date: str | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    universe: str = "csi300",
    codes: list[str] | None = None,
    include_flat: bool = False,
    max_workers: int | None = None,
    max_stocks: int | None = None,
    interval: str = "1D",
) -> dict[str, Any]:
    """Scan a universe with one or more strategy engines.

    Supports two date modes:
      - **Single-date**: pass ``target_date``.
      - **Date range**: pass ``start_date`` + ``end_date``.  Only actual
        trading bars are checked; weekends/holidays are skipped.

    Multi-strategy mode: pass a **list** of strategy keys.  Data is fetched
    once, then each strategy's SignalEngine runs against the same data.
    Results are returned grouped by strategy under ``results_by_strategy``.

    Args:
        strategy: Strategy key (e.g. ``"ichimoku"``) or a list of keys
            (e.g. ``["elliott-wave", "ichimoku"]``).
        target_date: Single trading date (YYYY-MM-DD).
        start_date: Range start (YYYY-MM-DD).  Requires ``end_date``.
        end_date: Range end (YYYY-MM-DD).  Requires ``start_date``.
        universe: ``csi300``, ``csi500``, ``all_a``, ``sp500``, or ``custom``.
        codes: Explicit stock codes; takes precedence over ``universe``.
        include_flat: When ``False`` (default), only report non-zero signals.
        max_workers: Override concurrent fetch workers (default 4).
        max_stocks: Cap the number of stocks to scan (useful for ``all_a``).
        interval: Bar interval. ``1D`` (default), ``1H``, ``30m``, ``15m``, ``5m``.

    Returns:
        JSON-serializable result dict.
    """
    # ── normalize strategies ───────────────────────────────────────────
    if isinstance(strategy, str):
        strategy_keys = [strategy]
    else:
        strategy_keys = list(strategy)

    for key in strategy_keys:
        if key not in _SCANNER_STRATEGIES:
            return {
                "status": "error",
                "error": f"unknown strategy {key!r}; choose from {sorted(_SCANNER_STRATEGIES)}",
            }

    # ── argument validation ──────────────────────────────────────────
    range_mode = bool(start_date or end_date)
    if range_mode:
        if not start_date or not end_date:
            return {
                "status": "error",
                "error": "Pass both start_date and end_date for date range mode, or target_date for single-date mode.",
            }
        try:
            s_dt = pd.Timestamp(start_date)
            e_dt = pd.Timestamp(end_date)
        except Exception:
            return {"status": "error", "error": f"invalid date range: {start_date!r}..{end_date!r}"}
        if s_dt > e_dt:
            return {"status": "error", "error": f"start_date ({start_date}) must be before end_date ({end_date})"}
        fetch_target = end_date
    else:
        if not target_date:
            return {
                "status": "error",
                "error": "Pass target_date for single-date mode, or both start_date/end_date for range mode.",
            }
        try:
            pd.Timestamp(target_date)
        except Exception:
            return {"status": "error", "error": f"invalid target_date: {target_date!r}"}
        fetch_target = target_date

    # ── strategy loading (all at once) ──────────────────────────────────
    engine_classes: dict[str, Any] = {}
    for key in strategy_keys:
        cls = _load_signal_engine(key)
        if cls is None:
            return {"status": "error", "error": f"failed to load SignalEngine for {key!r}"}
        engine_classes[key] = cls

    # ── universe / codes ─────────────────────────────────────────────
    if codes is not None:
        if not codes:
            return {"status": "error", "error": "codes list must be non-empty"}
        stock_codes = list(codes)
        effective_universe = "custom"
    elif universe == "csi300":
        stock_codes = _get_csi300_codes()
        effective_universe = "csi300"
    elif universe == "csi500":
        stock_codes = _get_csi500_codes()
        effective_universe = "csi500"
    elif universe == "all_a":
        stock_codes = _get_all_a_codes()
        effective_universe = "all_a"
        if len(stock_codes) > 300:
            logger.warning(
                "all_a universe has %d stocks; scanning may take a long time. "
                "Consider using --max-stocks to cap.",
                len(stock_codes),
            )
    elif universe == "sp500":
        logger.warning("sp500 scanner is experimental; survivorship bias applies")
        stock_codes = _get_sp500_codes()
        effective_universe = "sp500"
    else:
        return {"status": "error", "error": f"unknown universe {universe!r}"}

    if not stock_codes:
        return {"status": "error", "error": "no codes to scan"}

    # Cap stock count.
    if max_stocks and len(stock_codes) > max_stocks:
        logger.info("scanner: capping %d codes to %d", len(stock_codes), max_stocks)
        stock_codes = stock_codes[:max_stocks]

    source = _infer_source(stock_codes[0], universe)
    name_map = _name_lookup() if source == "tushare" else {}

    # ── fetch all stocks (once, shared across all strategies) ─────────
    fetched_dfs: dict[str, pd.DataFrame] = {}
    effective_source: str | None = None
    first_source: str | None = None
    scanned = 0
    fetch_errors = 0
    failed_codes: list[str] = []

    import time as _time  # noqa: E402

    try:
        from backtest.loaders.registry import get_loader_cls_with_fallback as _glc  # noqa: F811
        _test_cls = _glc(source)
        _test_instance = _test_cls()
        first_source = getattr(_test_instance, "name", source)
    except Exception:
        first_source = source

    _tdx_backed = first_source in ("mootdx", "a_stock_data")

    # Intraday warning: minute data is large, so cap concurrent work.
    _is_intraday = interval != "1D"

    if _tdx_backed:
        _time.sleep(5)
        for code in stock_codes:
            r = _fetch_one_stock(code, fetch_target, source, interval=interval)
            if r is None:
                failed_codes.append(code)
                fetch_errors += 1
                _time.sleep(3)
                continue
            scanned += 1
            df = r.get("df")
            src_name = r.get("effective_source")
            if src_name and effective_source is None:
                effective_source = src_name
            if df is not None and not df.empty:
                fetched_dfs[code] = df
            else:
                failed_codes.append(code)
            _time.sleep(3)
    else:
        workers = max_workers or _MAX_FETCH_WORKERS

        def _fetch_one(code: str) -> tuple[str, pd.DataFrame | None, str | None]:
            r = _fetch_one_stock(code, fetch_target, source, interval=interval)
            if r is None:
                return code, None, None
            return code, r.get("df"), r.get("effective_source")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_one, c): c for c in stock_codes}
            for fut in as_completed(futures):
                code = futures[fut]
                try:
                    c, df, src_name = fut.result()
                except Exception as exc:
                    logger.debug("scanner: worker failed for %s: %s", code, exc)
                    fetch_errors += 1
                    failed_codes.append(code)
                    continue
                scanned += 1
                if src_name and effective_source is None:
                    effective_source = src_name
                if df is not None and not df.empty:
                    fetched_dfs[c] = df
                else:
                    failed_codes.append(c)

    # ── collect trading dates ─────────────────────────────────────────
    if range_mode:
        trading_dates = _collect_trading_dates(fetched_dfs, start_date, end_date)
    else:
        trading_dates = _collect_trading_dates(fetched_dfs, target_date, target_date)
        if len(trading_dates) == 0:
            trading_dates = pd.DatetimeIndex([pd.Timestamp(target_date)])

    # ── run each strategy ──────────────────────────────────────────────
    results_by_strategy: dict[str, dict[str, Any]] = {}
    all_triggered: list[dict[str, Any]] = []
    all_triggered_by_date: list[dict[str, Any]] = []
    total_hits = 0

    for key in strategy_keys:
        try:
            engine = engine_classes[key]()
        except Exception as exc:
            results_by_strategy[key] = {"status": "error", "error": str(exc)}
            continue

        # Generate signals for every fetched stock.
        strategy_signals: dict[str, pd.Series] = {}
        for code, df in fetched_dfs.items():
            try:
                sig = engine.generate({code: df})
                series = sig.get(code)
                if series is not None and not series.empty:
                    strategy_signals[code] = series
            except Exception as exc:
                logger.debug("scanner: signal gen failed for %s/%s: %s", key, code, exc)

        # Check each date.
        if range_mode:
            triggered_by_date: list[dict[str, Any]] = []
            hits = 0
            success_codes: set[str] = set()

            for day in trading_dates:
                # For intraday intervals, show full datetime; for daily, date only.
                _is_intra = interval != "1D"
                day_str = (
                    day.strftime("%Y-%m-%d %H:%M") if _is_intra
                    else day.strftime("%Y-%m-%d")
                )
                day_hits: list[dict[str, Any]] = []
                for code, series in strategy_signals.items():
                    if day not in series.index:
                        continue
                    val = series.loc[day]
                    if pd.isna(val):
                        continue
                    val = float(val)
                    if not include_flat and val == 0:
                        continue
                    # Look up the close price at this bar.
                    row = {
                        "code": code,
                        "name": name_map.get(code),
                        "signal": int(val) if val in (-1, 0, 1) else round(val, 2),
                        "signal_label": _SIGNAL_LABELS.get(int(val), "partial"),
                    }
                    df = fetched_dfs.get(code)
                    if df is not None and day in df.index:
                        row["bar_time"] = day_str
                        close_val = df.loc[day, "close"]
                        row["close_price"] = round(float(close_val), 2) if pd.notna(close_val) else None
                    day_hits.append(row)
                    success_codes.add(code)
                triggered_by_date.append({"date": day_str, "count": len(day_hits), "stocks": day_hits})
                hits += len(day_hits)

            results_by_strategy[key] = {
                "status": "ok",
                "scanned": len(strategy_signals),
                "trading_days": len(trading_dates),
                "triggered_count": hits,
                "hit_codes": sorted(success_codes),
                "hit_code_count": len(success_codes),
                "triggered_by_date": triggered_by_date,
            }
            total_hits += hits

        else:
            # Single-date mode.
            triggered: list[dict[str, Any]] = []
            success_codes: set[str] = set()
            for code, series in strategy_signals.items():
                res = _check_signal_from_series(code, series, target_date)
                if res is None:
                    continue
                if not include_flat and res.get("signal") == 0:
                    continue
                res["name"] = name_map.get(code)
                triggered.append(res)
                success_codes.add(code)

            results_by_strategy[key] = {
                "status": "ok",
                "scanned": len(strategy_signals),
                "triggered_count": len(triggered),
                "hit_codes": sorted(success_codes),
                "hit_code_count": len(success_codes),
                "triggered": triggered,
            }

    # ── build merged flat lists for backward compatibility ──────────
    if range_mode:
        merged_by_date: dict[str, dict[str, Any]] = {}
        for key, sr in results_by_strategy.items():
            if sr.get("status") != "ok":
                continue
            for dg in sr.get("triggered_by_date", []):
                date = dg["date"]
                if date not in merged_by_date:
                    merged_by_date[date] = {"date": date, "count": 0, "stocks": []}
                for stock in dg["stocks"]:
                    merged_by_date[date]["stocks"].append({**stock, "strategy": key})
                merged_by_date[date]["count"] = len(merged_by_date[date]["stocks"])
        all_triggered_by_date = sorted(merged_by_date.values(), key=lambda d: d["date"])
        all_triggered = [
            {**s, "date": d["date"]}
            for d in all_triggered_by_date
            for s in d["stocks"]
        ]
    else:
        for key, sr in results_by_strategy.items():
            if sr.get("status") != "ok":
                continue
            for stock in sr.get("triggered", []):
                all_triggered.append({**stock, "strategy": key})

    # ── build plot data for stocks with signals ────────────────────
    plot_data: dict[str, Any] = _build_plot_data(
        fetched_dfs, results_by_strategy, start_date or target_date or "",
        end_date or target_date or "", interval,
    )

    base: dict[str, Any] = {
        "status": "ok",
        "strategies": strategy_keys,
        "interval": interval,
        "universe": effective_universe,
        "effective_source": effective_source or source,
        "total_stock_count": len(stock_codes),
        "total_scanned": scanned,
        "fetch_errors": fetch_errors,
        "triggered_count": sum(
            sr.get("triggered_count", 0)
            for sr in results_by_strategy.values()
            if sr.get("status") == "ok"
        ),
        "results_by_strategy": results_by_strategy,
        "triggered": all_triggered,
    }

    if range_mode:
        base["start_date"] = start_date
        base["end_date"] = end_date
        base["trading_days_scanned"] = len(trading_dates)
        base["triggered_by_date"] = all_triggered_by_date
    else:
        base["target_date"] = target_date

    if failed_codes:
        base["failed_codes"] = failed_codes[:20]
    if plot_data:
        base["plot_data"] = plot_data

    return base


def _build_plot_data(
    fetched_dfs: dict[str, pd.DataFrame],
    results_by_strategy: dict[str, dict[str, Any]],
    window_start: str,
    window_end: str,
    interval: str,
) -> dict[str, Any]:
    """Build OHLCV + signal-marker data for inline chart rendering.

    Only includes stocks that have at least one signal.  Caps at 20 stocks
    to keep response sizes manageable.
    """
    if not window_start or not window_end:
        return {}

    # Collect all stocks that have signals, deduplicated.
    hit_stocks: set[str] = set()
    for sr in results_by_strategy.values():
        if sr.get("status") != "ok":
            continue
        hit_stocks.update(sr.get("hit_codes", []))

    if not hit_stocks:
        return {}

    plot_data: dict[str, Any] = {}
    count = 0
    start_dt = pd.Timestamp(window_start)
    end_dt = pd.Timestamp(window_end)

    for code in sorted(hit_stocks):
        if count >= 20:
            break
        df = fetched_dfs.get(code)
        if df is None or df.empty:
            continue

        # Slice to the scan window.
        window = df[(df.index >= start_dt) & (df.index <= end_dt)]
        if window.empty:
            continue

        # OHLCV bars.
        bars: list[dict[str, Any]] = []
        for idx, row in window.iterrows():
            ts = idx.strftime("%Y-%m-%d %H:%M") if interval != "1D" else idx.strftime("%Y-%m-%d")
            bars.append({
                "t": ts,
                "o": round(float(row["open"]), 2) if pd.notna(row.get("open")) else None,
                "h": round(float(row["high"]), 2) if pd.notna(row.get("high")) else None,
                "l": round(float(row["low"]), 2) if pd.notna(row.get("low")) else None,
                "c": round(float(row["close"]), 2) if pd.notna(row.get("close")) else None,
            })

        # Signal markers — collect from every strategy.
        signals: list[dict[str, Any]] = []
        for key, sr in results_by_strategy.items():
            if sr.get("status") != "ok":
                continue
            # Range-mode: triggered_by_date.
            for dg in sr.get("triggered_by_date", []):
                for s in dg.get("stocks", []):
                    if s.get("code") != code:
                        continue
                    signals.append({
                        "t": s.get("bar_time") or s.get("effective_date") or dg["date"],
                        "price": s.get("close_price") or (bars[-1]["c"] if bars else None),
                        "label": s.get("signal_label", "partial"),
                        "strategy": key,
                    })
            # Single-date mode: triggered.
            for s in sr.get("triggered", []):
                if s.get("code") != code:
                    continue
                signals.append({
                    "t": s.get("bar_time") or s.get("effective_date") or window_start,
                    "price": s.get("close_price") or (bars[-1]["c"] if bars else None),
                    "label": s.get("signal_label", "partial"),
                    "strategy": key,
                })

        # Deduplicate signals: same timestamp + code → merge strategies.
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for s in signals:
            key_sig = (s["t"], s["label"])
            if key_sig not in seen:
                seen.add(key_sig)
                deduped.append(s)

        plot_data[code] = {"bars": bars, "signals": deduped}
        count += 1

    return plot_data


def _check_signal_from_series(
    code: str,
    series: pd.Series,
    target_date: str,
) -> dict[str, Any] | None:
    """Extract signal from a pre-computed Series — single-date mode helper."""
    target_dt = pd.Timestamp(target_date)

    if target_dt in series.index:
        val = series.loc[target_dt]
        if pd.notna(val):
            val = float(val)
            return {
                "code": code,
                "signal": int(val) if val in (-1, 0, 1) else round(val, 2),
                "signal_label": _SIGNAL_LABELS.get(int(val), "partial"),
            }

    prev_bars = series.index[series.index < target_dt]
    if len(prev_bars) == 0:
        return None
    nearest_date = prev_bars[-1]
    val = series.loc[nearest_date]
    if pd.isna(val):
        return None

    val = float(val)
    effective_date = (
        nearest_date.isoformat()
        if hasattr(nearest_date, "isoformat")
        else str(nearest_date)[:10]
    )
    return {
        "code": code,
        "signal": int(val) if val in (-1, 0, 1) else round(val, 2),
        "signal_label": _SIGNAL_LABELS.get(int(val), "partial"),
        "effective_date": effective_date,
    }


def _infer_source(code: str, universe: str) -> str:
    """Infer the data source from a stock code and universe."""
    from backtest.engines._market_hooks import _detect_market

    market = _detect_market(code)
    if market == "a_share":
        return "tushare"
    if market == "us_equity":
        return "yfinance"
    if market == "crypto":
        return "okx"
    if market == "hk_equity":
        return "yfinance"
    return "tushare"


def _get_csi500_codes() -> list[str]:
    """Fetch CSI 500 constituents via akshare (free, no auth)."""
    codes = _try_akshare_index("000905")
    if codes:
        return codes

    # Fallback: top-50 mid-cap
    fallback = [
        "000063.SZ", "002049.SZ", "300014.SZ", "600570.SH", "002410.SZ",
        "300124.SZ", "601799.SH", "600588.SH", "002230.SZ", "000538.SZ",
        "300033.SZ", "002008.SZ", "600845.SH", "300347.SZ", "002271.SZ",
        "000425.SZ", "002236.SZ", "600299.SH", "300144.SZ", "002709.SZ",
        "000723.SZ", "300136.SZ", "601066.SH", "002456.SZ", "300274.SZ",
        "000963.SZ", "002078.SZ", "600885.SH", "300755.SZ", "002572.SZ",
        "000547.SZ", "002916.SZ", "600754.SH", "300408.SZ", "002841.SZ",
        "000733.SZ", "002152.SZ", "600436.SH", "300450.SZ", "002340.SZ",
        "000927.SZ", "002007.SZ", "600511.SH", "300763.SZ", "002812.SZ",
        "000860.SZ", "002371.SZ", "600161.SH", "300316.SZ", "002384.SZ",
    ]
    logger.info("scanner: csi500 using %d-name fallback", len(fallback))
    return fallback


def _get_all_a_codes() -> list[str]:
    """Get all A-share stock codes.

    Priority: Tushare stock_basic (fast, reliable) → East Money → Sina → CSI300.
    Name lookup is piggybacked when Tushare is available.
    """
    import time as _time

    # Strategy 0: Tushare stock_basic (fast, reliable, piggybacks names).
    name_map = _fetch_stock_basic()
    if name_map:
        codes = sorted(name_map)
        logger.info("scanner: all_a = %d stocks from tushare stock_basic", len(codes))
        return codes

    # Strategy 1: East Money real-time quotes (akshare).
    for attempt in range(3):
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            if df is not None and not df.empty and "代码" in df.columns:
                codes = _parse_em_code_list(df, "代码")
                if codes:
                    logger.info("scanner: all_a = %d stocks from stock_zh_a_spot_em", len(codes))
                    return codes
        except Exception as exc:
            logger.debug("scanner: stock_zh_a_spot_em attempt %d failed: %s", attempt + 1, exc)
            if attempt < 2:
                _time.sleep(3)

    # Strategy 2: Sina-based list (smaller payload, different backend).
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        if df is not None and not df.empty and "code" in df.columns:
            codes = _parse_em_code_list(df, "code")
            if codes:
                logger.info("scanner: all_a = %d stocks from stock_info_a_code_name", len(codes))
                return codes
    except Exception as exc:
        logger.debug("scanner: stock_info_a_code_name failed: %s", exc)

    logger.warning("scanner: all A-shares unavailable; degrading to csi300")
    return _get_csi300_codes()


def _parse_em_code_list(df: pd.DataFrame, col: str) -> list[str]:
    """Parse East Money code column (6-digit) into project-style codes."""
    codes: list[str] = []
    for _, row in df.iterrows():
        raw = str(row[col]).zfill(6)
        if len(raw) == 6 and raw.isdigit():
            if raw.startswith(("3", "0")):
                codes.append(f"{raw}.SZ")
            elif raw.startswith(("6", "9")):
                codes.append(f"{raw}.SH")
            elif raw.startswith("8"):
                codes.append(f"{raw}.BJ")
    return codes


def _try_akshare_index(index_code: str) -> list[str] | None:
    """Try to get index constituents via akshare.  Returns None on failure."""
    try:
        import akshare as ak
        df = ak.index_stock_cons(symbol=index_code)
        if df is not None and not df.empty:
            col = "品种代码" if "品种代码" in df.columns else "stock_code" if "stock_code" in df.columns else None
            if col is None:
                col = df.columns[0]
            codes = _parse_em_code_list(df, col)
            if codes:
                logger.info("scanner: %s -> %d constituents via akshare", index_code, len(codes))
                return codes
    except Exception as exc:
        logger.debug("scanner: akshare %s failed: %s", index_code, exc)
    return None


def _get_sp500_codes() -> list[str]:
    """Get current S&P 500 constituent codes (project-style)."""
    # Reuse the alpha_bench_tool fetcher when available.
    try:
        from src.tools.alpha_bench_tool import _fetch_sp500_constituents

        raw = _fetch_sp500_constituents()
        return [f"{c}.US" for c in raw] if raw else []
    except ImportError:
        pass

    # Fallback: top-30 S&P 500
    fallback = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B",
        "JPM", "V", "JNJ", "WMT", "MA", "PG", "UNH", "HD", "DIS", "BAC",
        "XOM", "PFE", "NFLX", "ADBE", "CRM", "CSCO", "INTC", "TMO", "ABT",
        "ORCL", "QCOM", "AMD",
    ]
    return [f"{c}.US" for c in fallback]


# --------------------------------------------------------------------------- #
# MCP Tool                                                                      #
# --------------------------------------------------------------------------- #


class StockScannerTool(BaseTool):
    """Stock scanner tool: screen a universe with a strategy engine."""

    name = "scan_stocks"
    description = (
        "Scan a universe of stocks (CSI 300, S&P 500, or custom list) with a "
        "single-stock strategy engine (elliott-wave, chanlun, smc, technical-basic, "
        "ichimoku, candlestick) and return which stocks triggered buy/sell signals "
        "on a specific trading date or within a date range."
    )
    parameters = {
        "type": "object",
        "properties": {
            "strategy": {
                "oneOf": [
                    {
                        "type": "string",
                        "description": "Single strategy key",
                        "enum": sorted(_SCANNER_STRATEGIES),
                    },
                    {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(_SCANNER_STRATEGIES)},
                        "description": "Multiple strategy keys — data is fetched once, shared across all strategies",
                    },
                ],
                "description": "Strategy key or list of keys: elliott-wave, chanlun, smc, technical-basic, ichimoku, candlestick",
            },
            "target_date": {
                "type": "string",
                "description": "Single trading date in YYYY-MM-DD format. Omit when start_date/end_date are set.",
            },
            "start_date": {
                "type": "string",
                "description": "Start of date range in YYYY-MM-DD format. Must pair with end_date.",
            },
            "end_date": {
                "type": "string",
                "description": "End of date range in YYYY-MM-DD format. Must pair with start_date.",
            },
            "universe": {
                "type": "string",
                "description": "Stock universe: csi300 (default) or sp500; ignored when codes is set",
                "default": "csi300",
            },
            "codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Explicit list of stock codes (overrides universe)",
            },
            "include_flat": {
                "type": "boolean",
                "description": "When true, include stocks with flat (0) signals in the output",
                "default": False,
            },
        },
        "required": ["strategy"],
    }
    repeatable = True
    is_readonly = True

    @classmethod
    def check_available(cls) -> bool:
        """Scanner is always available; it degrades gracefully with fallback data."""
        return True

    def execute(self, **kwargs: Any) -> str:
        """Execute stock scan."""
        strategy = kwargs["strategy"]
        if isinstance(strategy, list):
            strategy = [str(s) for s in strategy]
        result = scan_stocks(
            strategy=strategy,
            target_date=kwargs.get("target_date"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            universe=kwargs.get("universe", "csi300"),
            codes=kwargs.get("codes"),
            include_flat=bool(kwargs.get("include_flat", False)),
        )
        return json.dumps(result, ensure_ascii=False, default=str)
