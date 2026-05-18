"""A-stock trading hours detection and session scheduling.

Handles the A-stock market schedule (Beijing time, UTC+8):
  - Morning auction:   9:15 – 9:25  (not monitored by default)
  - Morning session:   9:30 – 11:30
  - Afternoon session: 13:00 – 15:00
  - Weekends excluded; Chinese holidays NOT excluded in base mode.
"""


import logging
from datetime import date, datetime, time, timedelta, timezone

logger = logging.getLogger(__name__)

# Beijing time (UTC+8, no DST)
_CST = timezone(timedelta(hours=8))

MORNING_START = time(9, 30)
MORNING_END = time(11, 30)
AFTERNOON_START = time(13, 0)
AFTERNOON_END = time(15, 0)


class TradingSession:
    """Know A-stock market session state."""

    @staticmethod
    def now_cst() -> datetime:
        return datetime.now(_CST)

    @classmethod
    def is_trading_time(cls, dt: datetime | None = None) -> bool:
        """Check if we are currently within continuous auction trading hours."""
        if dt is None:
            dt = cls.now_cst()
        t = dt.time()
        return (MORNING_START <= t < MORNING_END) or (AFTERNOON_START <= t < AFTERNOON_END)

    @classmethod
    def is_market_open_today(cls, dt: date | None = None) -> bool:
        """Check if today is a potential trading day.

        Excludes weekends. Chinese holidays require the optional akshare package.
        """
        if dt is None:
            dt = cls.now_cst().date()
        if dt.weekday() >= 5:
            return False
        return True

    @classmethod
    def seconds_until_next_session(cls, dt: datetime | None = None) -> float:
        """Seconds until the next trading session starts.

        During trading hours: returns 0.
        During lunch break: returns seconds until 13:00.
        After hours / weekend: returns seconds until next trading day 9:30.
        """
        if dt is None:
            dt = cls.now_cst()
        t = dt.time()

        # Currently in a trading session
        if (MORNING_START <= t < MORNING_END) or (AFTERNOON_START <= t < AFTERNOON_END):
            return 0.0

        today = dt.date()

        # During lunch break: wait until afternoon
        if MORNING_END <= t < AFTERNOON_START:
            afternoon = datetime.combine(today, AFTERNOON_START, tzinfo=_CST)
            return (afternoon - dt).total_seconds()

        # Before market opens today (and today IS a trading day)
        if t < MORNING_START and cls.is_market_open_today(today):
            morning = datetime.combine(today, MORNING_START, tzinfo=_CST)
            return (morning - dt).total_seconds()

        # After hours or non-trading day: find next trading day
        return cls._seconds_until_next_trading_day(dt)

    @classmethod
    def _seconds_until_next_trading_day(cls, dt: datetime) -> float:
        """Find the next trading day and return seconds until 9:30 CST."""
        next_day = dt.date() + timedelta(days=1)
        while not cls.is_market_open_today(next_day):
            next_day += timedelta(days=1)
        next_open = datetime.combine(next_day, MORNING_START, tzinfo=_CST)
        return (next_open - dt).total_seconds()
