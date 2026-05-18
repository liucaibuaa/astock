"""Technical indicator calculation using stockstats.

Wraps stockstats to compute MA, RSI, MACD, Bollinger Bands from stored OHLCV data.
"""


import logging

import pandas as pd

logger = logging.getLogger(__name__)


class IndicatorEngine:
    """Calculate technical indicators from stored daily bar data."""

    def __init__(self, db):
        self._db = db

    async def _get_ohlcv(self, code: str, limit: int = 200) -> pd.DataFrame:
        """Get OHLCV DataFrame suitable for stockstats wrapping."""
        return await self._db.get_daily_bars(code, limit=limit)

    # ---- Moving Averages ----

    async def get_ma(self, code: str, periods: list[int] | None = None) -> dict[int, float]:
        """Get latest MA values for given periods (default: 5, 10, 20, 60)."""
        if periods is None:
            periods = [5, 10, 20, 60]

        df = await self._get_ohlcv(code)
        if df.empty or len(df) < max(periods):
            return {p: 0.0 for p in periods}

        from stockstats import wrap

        stock = wrap(df)
        result = {}
        for p in periods:
            col = f"close_{p}_sma"
            try:
                series = stock[col]
                result[p] = float(series.iloc[-1]) if not pd.isna(series.iloc[-1]) else 0.0
            except (KeyError, IndexError):
                result[p] = 0.0
        return result

    # ---- RSI ----

    async def get_rsi(self, code: str, period: int = 14) -> float:
        """Get the latest RSI value."""
        df = await self._get_ohlcv(code)
        if df.empty or len(df) < period + 1:
            return 50.0

        from stockstats import wrap

        stock = wrap(df)
        try:
            col = f"rsi_{period}"
            val = stock[col].iloc[-1]
            return float(val) if not pd.isna(val) else 50.0
        except (KeyError, IndexError):
            return 50.0

    # ---- MACD ----

    async def get_macd(self, code: str) -> dict[str, float]:
        """Get latest MACD values (DIF, DEA, histogram)."""
        df = await self._get_ohlcv(code)
        if df.empty or len(df) < 26 + 9:
            return {"dif": 0.0, "dea": 0.0, "macd": 0.0}

        from stockstats import wrap

        stock = wrap(df)
        try:
            dif = float(stock["dif"].iloc[-1])
            dea = float(stock["dea"].iloc[-1])
            macd_hist = float(stock["macd"].iloc[-1])
            return {"dif": dif, "dea": dea, "macd": macd_hist}
        except (KeyError, IndexError):
            return {"dif": 0.0, "dea": 0.0, "macd": 0.0}

    # ---- Bollinger Bands ----

    async def get_bollinger(self, code: str) -> dict[str, float]:
        """Get latest Bollinger Band values (upper, middle, lower)."""
        df = await self._get_ohlcv(code)
        if df.empty or len(df) < 20:
            return {"upper": 0.0, "middle": 0.0, "lower": 0.0}

        from stockstats import wrap

        stock = wrap(df)
        try:
            return {
                "upper": float(stock["boll_ub"].iloc[-1]),
                "middle": float(stock["boll"].iloc[-1]),
                "lower": float(stock["boll_lb"].iloc[-1]),
            }
        except (KeyError, IndexError):
            return {"upper": 0.0, "middle": 0.0, "lower": 0.0}

    # ---- Cross detection ----

    async def _check_ma_cross(
        self, code: str, fast: int, slow: int
    ) -> str | None:
        """Check if fast MA crossed above/below slow MA at the latest bar.

        Returns 'golden' for golden cross, 'death' for death cross, None if no cross.
        """
        df = await self._get_ohlcv(code)
        if df.empty or len(df) < max(fast, slow) + 2:
            return None

        from stockstats import wrap

        stock = wrap(df)
        try:
            fast_series = stock[f"close_{fast}_sma"]
            slow_series = stock[f"close_{slow}_sma"]

            if fast_series.iloc[-2] <= slow_series.iloc[-2] and fast_series.iloc[-1] > slow_series.iloc[-1]:
                return "golden"
            if fast_series.iloc[-2] >= slow_series.iloc[-2] and fast_series.iloc[-1] < slow_series.iloc[-1]:
                return "death"
        except (KeyError, IndexError):
            pass
        return None

    async def check_golden_cross(self, code: str, fast: int = 5, slow: int = 20) -> bool:
        return await self._check_ma_cross(code, fast, slow) == "golden"

    async def check_death_cross(self, code: str, fast: int = 5, slow: int = 20) -> bool:
        return await self._check_ma_cross(code, fast, slow) == "death"

    async def check_macd_cross(self, code: str) -> str | None:
        """Check MACD DIF/DEA cross. Returns 'golden' or 'death'."""
        df = await self._get_ohlcv(code)
        if df.empty or len(df) < 27:
            return None

        from stockstats import wrap

        stock = wrap(df)
        try:
            dif_last, dif_prev = float(stock["dif"].iloc[-1]), float(stock["dif"].iloc[-2])
            dea_last, dea_prev = float(stock["dea"].iloc[-1]), float(stock["dea"].iloc[-2])

            if dif_prev <= dea_prev and dif_last > dea_last:
                return "golden"
            if dif_prev >= dea_prev and dif_last < dea_last:
                return "death"
        except (KeyError, IndexError):
            pass
        return None
