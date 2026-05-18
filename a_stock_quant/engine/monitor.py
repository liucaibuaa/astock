"""Core async monitoring loop — the central engine coordinating all subsystems."""


import asyncio
import logging
from datetime import datetime

from a_stock_quant.data.fetcher import QuoteFetchError, QuoteFetcher
from a_stock_quant.data.indicators import IndicatorEngine
from a_stock_quant.data.ticker import resolve_ticker
from a_stock_quant.engine.alerts import AlertEngine
from a_stock_quant.engine.scheduler import TradingSession
from a_stock_quant.notify.channels import NotificationManager
from a_stock_quant.storage.database import Database

logger = logging.getLogger(__name__)


class MonitorEngine:
    """Central monitoring engine.

    Owns and coordinates the quote fetcher, database, alert evaluator,
    indicator engine, notification manager, and websocket broadcaster.
    """

    def __init__(self, config: dict):
        self._config = config
        self._running = False
        self._watchlist: list[str] = []
        self._task: asyncio.Task | None = None

        # Subsystems (initialized in start())
        self._db: Database | None = None
        self._fetcher: QuoteFetcher | None = None
        self._indicators: IndicatorEngine | None = None
        self._alerts: AlertEngine | None = None
        self._notifier: NotificationManager | None = None
        self._ws_manager = None  # set by web layer after construction

        # Stats
        self.poll_count: int = 0
        self.alert_count: int = 0
        self.last_poll_time: datetime | None = None
        self.started_at: datetime | None = None

    # ---- public API ----

    async def start(self, web_enabled: bool = True) -> None:
        """Initialize subsystems and begin the monitoring loop."""
        if self._running:
            logger.warning("Monitor is already running")
            return

        # Initialize subsystems
        self._db = Database(self._config["database"]["path"])
        await self._db.initialize()

        self._fetcher = QuoteFetcher()
        self._indicators = IndicatorEngine(self._db)
        self._alerts = AlertEngine(self._config, self._db, self._indicators)
        self._notifier = NotificationManager(self._config)

        # Load watchlist: CLI args > config file > database
        configured = self._config.get("watchlist", [])
        if configured:
            self._watchlist = self._resolve_list(configured)
        else:
            db_watchlist = await self._db.load_watchlist()
            if db_watchlist:
                self._watchlist = db_watchlist

        if not self._watchlist:
            logger.warning("Watchlist is empty. Add stocks via CLI or config.yaml.")
        else:
            await self._db.save_watchlist(self._watchlist)
            logger.info("Watchlist: %s", ", ".join(self._watchlist))

        self._running = True
        self.started_at = datetime.utcnow()
        self.poll_count = 0
        self.alert_count = 0

        # Seed daily bars for indicator-capable alerts
        if self._config.get("alerts", {}).get("indicator_signals", {}).get("enabled"):
            await self._seed_daily_bars()

        # Always do an initial poll so the dashboard has data immediately
        if self._watchlist:
            try:
                await self._poll_cycle()
                logger.info("Initial poll completed")
            except Exception:
                logger.warning("Initial poll failed; dashboard will populate on next cycle")

        logger.info("Monitor engine started")
        self._task = asyncio.create_task(self._main_loop())

    async def stop(self) -> None:
        """Gracefully stop the monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._db:
            await self._db.close()
        logger.info("Monitor engine stopped")

    async def add_stock(self, user_input: str) -> str:
        """Add a stock to the watchlist. Returns resolved code.

        Immediately fetches the current quote and broadcasts via WebSocket
        so the dashboard updates without waiting for the next poll cycle.
        """
        code = resolve_ticker(user_input)
        if code not in self._watchlist:
            self._watchlist.append(code)
            if self._db:
                await self._db.save_watchlist(self._watchlist)
            logger.info("Added %s to watchlist", code)

            # Fetch current quote immediately
            if self._fetcher:
                try:
                    snapshots = await self._fetcher.fetch_batch([code])
                    for _c, snap in snapshots.items():
                        if self._db:
                            await self._db.insert_snapshot(snap)
                    if self._ws_manager and snapshots:
                        await self._ws_manager.broadcast(snapshots)
                except Exception:
                    logger.warning("Failed to fetch immediate quote for %s", code)
        return code

    async def remove_stock(self, user_input: str) -> str:
        """Remove a stock from the watchlist. Returns resolved code.

        Broadcasts a removal event via WebSocket so the dashboard updates immediately.
        """
        code = resolve_ticker(user_input)
        if code in self._watchlist:
            self._watchlist.remove(code)
            if self._db:
                await self._db.save_watchlist(self._watchlist)
            logger.info("Removed %s from watchlist", code)

            # Notify dashboard to remove this stock from the display
            if self._ws_manager:
                await self._ws_manager.broadcast_remove(code)
        return code

    def get_watchlist(self) -> list[str]:
        return list(self._watchlist)

    # ---- main loop ----

    async def _main_loop(self) -> None:
        interval = self._config.get("poll_interval_seconds", 5)
        trading_only = self._config.get("trading_hours_only", True)
        retention = self._config.get("database", {}).get("retention_days", 30)

        # Periodic cleanup counter
        cleanup_counter = 0

        while self._running:
            # Trading hours check
            if trading_only:
                if not TradingSession.is_market_open_today():
                    secs = TradingSession.seconds_until_next_session()
                    logger.info("Non-trading day; sleeping %.0f min", secs / 60)
                    await self._sleep(min(secs, 3600))
                    continue

                if not TradingSession.is_trading_time():
                    secs = TradingSession.seconds_until_next_session()
                    logger.info("Outside trading hours; sleeping %.0f min", secs / 60)
                    await self._sleep(min(secs, 300))
                    continue

            # Poll cycle
            try:
                await self._poll_cycle()
                self.poll_count += 1

            except QuoteFetchError as e:
                logger.error("Quote fetch failed: %s", e)
                await self._sleep(interval)
            except Exception:
                logger.exception("Unexpected error in monitoring loop")
                await self._sleep(5)

            # Periodic database cleanup (every ~1000 polls)
            cleanup_counter += 1
            if cleanup_counter >= 1000:
                await self._db.vacuum(retention)
                cleanup_counter = 0

            await self._sleep(interval)

    async def _poll_cycle(self) -> None:
        """Single poll-evaluate-store-notify-broadcast cycle."""
        if not self._watchlist:
            return

        # 1. Fetch quotes
        snapshots = await self._fetcher.fetch_batch(self._watchlist)
        if not snapshots:
            return

        self.last_poll_time = datetime.utcnow()

        # 2. Store snapshots
        for code, snap in snapshots.items():
            await self._db.insert_snapshot(snap)

        # 3. Evaluate alerts
        triggered = await self._alerts.evaluate_all(snapshots)
        self.alert_count += len(triggered)

        # 4. Send notifications
        for alert in triggered:
            await self._notifier.send(alert)

        # 5. Broadcast via WebSocket
        if self._ws_manager:
            await self._ws_manager.broadcast(snapshots)

    async def _sleep(self, seconds: float) -> None:
        """Sleep with cancellation support."""
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            raise

    async def _seed_daily_bars(self) -> None:
        """Seed historical daily bars for stocks that don't have data yet."""
        import pandas as pd

        for code in self._watchlist:
            existing = await self._db.get_daily_bars(code, limit=1)
            if not existing.empty:
                continue

            from a_stock_quant.data.ticker import get_prefix

            logger.info("Seeding daily bars for %s ...", code)
            bars = await self._fetch_historical_bars(code)
            if bars:
                await self._db.insert_daily_bars(bars)
                logger.info("Seeded %d bars for %s", len(bars), code)

    async def _fetch_historical_bars(self, code: str) -> list:
        """Fetch historical daily bars for a stock via mootdx or akshare fallback."""
        from a_stock_quant.data.ticker import get_prefix
        from a_stock_quant.storage.models import DailyBar

        # Primary: mootdx
        try:
            from mootdx.quotes import Quotes

            client = Quotes.factory(market="std")
            prefix = get_prefix(code)
            market_code = f"{prefix}{code}"
            df = client.bars(symbol=market_code, category=4, offset=800)
            if df is not None and not df.empty:
                return self._parse_historical_df(code, df)
        except Exception as e:
            logger.debug("mootdx historical bars failed for %s: %s", code, e)

        # Fallback: akshare (Sina source)
        try:
            import akshare as ak

            prefix = get_prefix(code)
            end = datetime.now().strftime("%Y%m%d")
            start = "20150101"
            df = ak.stock_zh_a_daily(
                symbol=f"{prefix}{code}",
                start_date=start,
                end_date=end,
                adjust="qfq",
            )
            if df is not None and not df.empty:
                bars = []
                for _, row in df.iterrows():
                    bars.append(DailyBar(
                        code=code,
                        date=str(row["date"])[:10],
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                    ))
                return bars
        except Exception as e:
            logger.debug("akshare historical bars failed for %s: %s", code, e)

        logger.warning("Could not seed historical bars for %s", code)
        return []

    @staticmethod
    def _parse_historical_df(code: str, df) -> list:
        from a_stock_quant.storage.models import DailyBar

        bars = []
        for _, row in df.iterrows():
            date_val = str(row.get("datetime", "")).split(" ")[0]
            if not date_val or date_val == "nan":
                continue
            bars.append(DailyBar(
                code=code,
                date=date_val,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            ))
        return bars

    @staticmethod
    def _resolve_list(items: list[str]) -> list[str]:
        resolved = []
        for item in items:
            try:
                resolved.append(resolve_ticker(item))
            except ValueError as e:
                logger.warning("Skipping unresolvable ticker '%s': %s", item, e)
        return resolved
