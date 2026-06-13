"""A-stock-data loader for Vibe-Trading.

Bridges the open-source ``/home/liucai/a-stock-data`` toolkit into the
Vibe-Trading loader registry.  Supports A-share OHLCV history plus
non-OHLCV data (research reports, news, fundamentals, announcements)
through a small adapter module.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from backtest.loaders.a_stock_data_api import (
    eastmoney_global_news,
    eastmoney_reports,
    eastmoney_stock_info,
    eastmoney_stock_news,
    fetch_a_share_ohlcv,
)
from backtest.loaders.base import validate_date_range
from backtest.loaders.registry import register

logger = logging.getLogger(__name__)


@register
class DataLoader:
    """A-share loader backed by the a-stock-data open-source toolkit.

    Provides:
      - OHLCV daily bars via Baidu K-line (used by the backtest runner)
      - Real-time quotes, research reports, news, fundamentals, and
        announcements via the adapter module
    """

    name = "a_stock_data"
    markets = {"a_share"}
    requires_auth = False

    def is_available(self) -> bool:
        """Available when the external toolkit file is present."""
        from pathlib import Path

        skill_path = Path("/home/liucai/a-stock-data/SKILL.md")
        return skill_path.exists() and skill_path.is_file()

    def __init__(self) -> None:
        pass

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch OHLCV data for A-share codes via Baidu K-line.

        Args:
            codes: Symbol list (e.g. ["000001.SZ", "600519.SH"]).
            start_date: YYYY-MM-DD.
            end_date: YYYY-MM-DD.
            interval: Bar size (only 1D supported by this loader).
            fields: Ignored.

        Returns:
            Mapping symbol -> OHLCV DataFrame.
        """
        validate_date_range(start_date, end_date)

        if interval != "1D":
            logger.warning("a_stock_data loader only supports 1D interval; ignoring %s", interval)

        result: Dict[str, pd.DataFrame] = {}
        for raw_code in codes:
            # Preserve the original symbol key so the caller's symbol map stays consistent.
            try:
                df = fetch_a_share_ohlcv(raw_code, start_date, end_date)
                if df is not None and not df.empty:
                    result[raw_code] = df
            except Exception as exc:
                logger.warning("a_stock_data failed for %s: %s", raw_code, exc)
        return result

    # -----------------------------------------------------------------------
    # Optional extended helpers (not part of DataLoaderProtocol)
    # -----------------------------------------------------------------------

    def fetch_reports(self, code: str, max_pages: int = 3) -> List[dict]:
        """Fetch research reports for ``code``."""
        return eastmoney_reports(code, max_pages=max_pages)

    def fetch_news(self, code: str, page_size: int = 20) -> List[dict]:
        """Fetch per-stock news for ``code``."""
        return eastmoney_stock_news(code, page_size=page_size)

    def fetch_global_news(self, page_size: int = 20) -> List[dict]:
        """Fetch global 7x24 finance news."""
        return eastmoney_global_news(page_size=page_size)

    def fetch_fundamentals(self, code: str) -> dict:
        """Fetch basic fundamental profile for ``code``."""
        return eastmoney_stock_info(code)

    def fetch_announcements(self, code: str, page_size: int = 30) -> List[dict]:
        """Fetch official announcements for ``code`` from cninfo."""
        from backtest.loaders.a_stock_data_api import cninfo_announcements

        return cninfo_announcements(code, page_size=page_size)
