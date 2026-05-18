"""Async real-time quote fetcher using Tencent Finance API (qt.gtimg.cn)."""


import logging
from datetime import datetime

import aiohttp

from a_stock_quant.data.ticker import get_prefix
from a_stock_quant.storage.models import Snapshot

logger = logging.getLogger(__name__)


class QuoteFetchError(Exception):
    """Raised when all retry attempts to fetch quotes have failed."""


class QuoteFetcher:
    """Async wrapper around the Tencent Finance HTTP quote API.

    Fetches real-time prices for multiple A-stocks in a single batch request.
    Uses aiohttp for non-blocking I/O with retry + exponential backoff.
    """

    def __init__(self, timeout: float = 10.0, max_retries: int = 3):
        self._timeout = timeout
        self._max_retries = max_retries

    async def fetch_batch(self, codes: list[str]) -> dict[str, Snapshot]:
        """Fetch real-time quotes for multiple stock codes.

        Args:
            codes: List of 6-digit A-stock codes (e.g. ['000001', '600519']).

        Returns:
            dict mapping code to Snapshot.

        Raises:
            QuoteFetchError: When all retry attempts fail.
        """
        if not codes:
            return {}

        prefixed = [f"{get_prefix(c)}{c}" for c in codes]
        url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)

        raw_text = await self._http_get(url)

        return self._parse_response(raw_text)

    async def _http_get(self, url: str) -> str:
        """HTTP GET with retry + exponential backoff."""
        import asyncio

        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=aiohttp.ClientTimeout(total=self._timeout),
                    ) as resp:
                        raw_bytes = await resp.read()
                        return raw_bytes.decode("gbk")
            except UnicodeDecodeError:
                # Try gb2312 as fallback encoding
                try:
                    return raw_bytes.decode("gb2312")
                except Exception:
                    pass
                raise
            except Exception as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    sleep_time = 2 ** attempt
                    logger.warning(
                        "Quote fetch attempt %d/%d failed: %s. Retrying in %ds...",
                        attempt + 1, self._max_retries, e, sleep_time,
                    )
                    await asyncio.sleep(sleep_time)
                else:
                    logger.error(
                        "Quote fetch failed after %d attempts: %s",
                        self._max_retries, e,
                    )

        raise QuoteFetchError(
            f"Failed to fetch quotes after {self._max_retries} attempts: {last_error}"
        )

    @staticmethod
    def _parse_response(raw_text: str) -> dict[str, Snapshot]:
        """Parse Tencent Finance GBK response into Snapshot objects.

        Response format per stock:
            v_sh600379="1~宝光股份~...~price~last_close~open~...~53+fields~...";

        Key field indices (tilde-delimited):
            1=name, 3=price, 4=last_close, 5=open,
            32=change_pct, 33=high, 34=low, 38=turnover_pct,
            39=pe_ttm, 44=mcap_yi, 45=float_mcap_yi, 46=pb,
            47=limit_up, 48=limit_down, 52=pe_static
        """
        now = datetime.utcnow()
        result: dict[str, Snapshot] = {}

        for line in raw_text.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line or '"' not in line:
                continue

            try:
                key = line.split("=")[0].split("_")[-1]
                vals = line.split('"')[1].split("~")
                if len(vals) < 53:
                    continue

                code = key[2:]  # strip sh/sz/bj prefix

                def _f(idx: int) -> float:
                    try:
                        return float(vals[idx]) if vals[idx] else 0.0
                    except (ValueError, IndexError):
                        return 0.0

                result[code] = Snapshot(
                    code=code,
                    name=vals[1],
                    price=_f(3),
                    last_close=_f(4),
                    open=_f(5),
                    change_pct=_f(32),
                    high=_f(33),
                    low=_f(34),
                    turnover_pct=_f(38),
                    pe_ttm=_f(39),
                    pe_static=_f(52),
                    pb=_f(46),
                    mcap_yi=_f(44),
                    float_mcap_yi=_f(45),
                    limit_up=_f(47),
                    limit_down=_f(48),
                    fetched_at=now,
                )
            except (IndexError, ValueError) as e:
                logger.debug("Failed to parse line: %s ... (%s)", line[:80], e)
                continue

        return result
