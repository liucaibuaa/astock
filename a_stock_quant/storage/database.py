"""Async SQLite database for price snapshots, alerts, and OHLCV data."""


import logging
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
import pandas as pd

from a_stock_quant.storage.models import AlertEvent, DailyBar, Snapshot

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    last_close REAL,
    open REAL,
    change_pct REAL,
    high REAL,
    low REAL,
    turnover_pct REAL,
    pe_ttm REAL,
    pe_static REAL,
    pb REAL,
    mcap_yi REAL,
    float_mcap_yi REAL,
    limit_up REAL,
    limit_down REAL,
    fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_code_time
    ON snapshots(code, fetched_at);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    price REAL,
    change_pct REAL,
    triggered_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_code_time
    ON alerts(code, triggered_at);

CREATE TABLE IF NOT EXISTS daily_bars (
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS watchlist (
    code TEXT PRIMARY KEY,
    added_at TEXT NOT NULL,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS alert_cooldowns (
    code TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    last_triggered_at TEXT NOT NULL,
    PRIMARY KEY (code, alert_type)
);
"""


class Database:
    """Async SQLite database manager."""

    def __init__(self, db_path: str):
        self._db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ---- Snapshot CRUD ----

    async def insert_snapshot(self, snap: Snapshot) -> int:
        cursor = await self._conn.execute(
            """INSERT OR REPLACE INTO snapshots
               (code, name, price, last_close, open, change_pct, high, low,
                turnover_pct, pe_ttm, pe_static, pb, mcap_yi, float_mcap_yi,
                limit_up, limit_down, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                snap.code, snap.name, snap.price, snap.last_close, snap.open,
                snap.change_pct, snap.high, snap.low, snap.turnover_pct,
                snap.pe_ttm, snap.pe_static, snap.pb, snap.mcap_yi,
                snap.float_mcap_yi, snap.limit_up, snap.limit_down,
                snap.fetched_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_latest_snapshots(
        self, codes: list[str] | None = None
    ) -> list[Snapshot]:
        if codes:
            placeholders = ",".join("?" * len(codes))
            rows = await self._conn.execute_fetchall(
                f"""SELECT s.* FROM snapshots s
                    INNER JOIN (
                        SELECT code, MAX(fetched_at) as max_time
                        FROM snapshots
                        WHERE code IN ({placeholders})
                        GROUP BY code
                    ) latest ON s.code = latest.code AND s.fetched_at = latest.max_time""",
                codes,
            )
        else:
            rows = await self._conn.execute_fetchall(
                """SELECT s.* FROM snapshots s
                    INNER JOIN (
                        SELECT code, MAX(fetched_at) as max_time
                        FROM snapshots GROUP BY code
                    ) latest ON s.code = latest.code AND s.fetched_at = latest.max_time"""
            )
        return [self._row_to_snapshot(r) for r in rows]

    async def get_snapshot_history(
        self, code: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[Snapshot]:
        query = "SELECT * FROM snapshots WHERE code = ?"
        params = [code]
        if start:
            query += " AND fetched_at >= ?"
            params.append(start.isoformat())
        if end:
            query += " AND fetched_at <= ?"
            params.append(end.isoformat())
        query += " ORDER BY fetched_at ASC"
        rows = await self._conn.execute_fetchall(query, params)
        return [self._row_to_snapshot(r) for r in rows]

    # ---- Alert CRUD ----

    async def insert_alert(self, alert: AlertEvent) -> int:
        cursor = await self._conn.execute(
            """INSERT INTO alerts (code, name, alert_type, severity, message, price, change_pct, triggered_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                alert.code, alert.name, alert.alert_type.value,
                alert.severity.value, alert.message, alert.price,
                alert.change_pct, alert.triggered_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_recent_alerts(self, limit: int = 50) -> list[dict]:
        rows = await self._conn.execute_fetchall(
            "SELECT * FROM alerts ORDER BY triggered_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    async def get_alerts_for_code(self, code: str, limit: int = 50) -> list[dict]:
        rows = await self._conn.execute_fetchall(
            "SELECT * FROM alerts WHERE code = ? ORDER BY triggered_at DESC LIMIT ?",
            (code, limit),
        )
        return [dict(r) for r in rows]

    # ---- Cooldown tracking ----

    async def is_in_cooldown(self, code: str, alert_type: str, cooldown_seconds: int) -> bool:
        row = await self._conn.execute_fetchall(
            "SELECT last_triggered_at FROM alert_cooldowns WHERE code = ? AND alert_type = ?",
            (code, alert_type),
        )
        if not row:
            return False
        last_time = datetime.fromisoformat(row[0]["last_triggered_at"])
        return (datetime.utcnow() - last_time).total_seconds() < cooldown_seconds

    async def set_cooldown(self, code: str, alert_type: str) -> None:
        await self._conn.execute(
            """INSERT OR REPLACE INTO alert_cooldowns (code, alert_type, last_triggered_at)
               VALUES (?, ?, ?)""",
            (code, alert_type, datetime.utcnow().isoformat()),
        )
        await self._conn.commit()

    # ---- Daily Bars ----

    async def insert_daily_bars(self, bars: list[DailyBar]) -> None:
        await self._conn.executemany(
            """INSERT OR REPLACE INTO daily_bars (code, date, open, high, low, close, volume)
               VALUES (?,?,?,?,?,?,?)""",
            [(b.code, b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        )
        await self._conn.commit()

    async def get_daily_bars(self, code: str, limit: int = 200) -> pd.DataFrame:
        rows = await self._conn.execute_fetchall(
            "SELECT date, open, high, low, close, volume FROM daily_bars "
            "WHERE code = ? ORDER BY date ASC LIMIT ?",
            (code, limit),
        )
        if not rows:
            return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        df = pd.DataFrame(
            [dict(r) for r in rows],
            columns=["date", "open", "high", "low", "close", "volume"],
        )
        df = df.rename(columns={
            "date": "Date", "open": "Open", "high": "High",
            "low": "Low", "close": "Close", "volume": "Volume",
        })
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")
        return df

    # ---- Watchlist ----

    async def save_watchlist(self, codes: list[str]) -> None:
        await self._conn.execute("DELETE FROM watchlist")
        now = datetime.utcnow().isoformat()
        await self._conn.executemany(
            "INSERT INTO watchlist (code, added_at) VALUES (?, ?)",
            [(c, now) for c in codes],
        )
        await self._conn.commit()

    async def load_watchlist(self) -> list[str]:
        rows = await self._conn.execute_fetchall(
            "SELECT code FROM watchlist ORDER BY added_at"
        )
        return [r[0] for r in rows]

    # ---- Cleanup ----

    async def vacuum(self, retention_days: int) -> None:
        cutoff = (datetime.utcnow() - timedelta(days=retention_days)).isoformat()
        await self._conn.execute(
            "DELETE FROM snapshots WHERE fetched_at < ?", (cutoff,)
        )
        await self._conn.execute("PRAGMA optimize")
        await self._conn.commit()

    # -- helpers --

    @staticmethod
    def _row_to_snapshot(row: aiosqlite.Row) -> Snapshot:
        return Snapshot(
            code=row["code"],
            name=row["name"],
            price=row["price"],
            last_close=row["last_close"],
            open=row["open"],
            change_pct=row["change_pct"],
            high=row["high"],
            low=row["low"],
            turnover_pct=row["turnover_pct"],
            pe_ttm=row["pe_ttm"],
            pe_static=row["pe_static"],
            pb=row["pb"],
            mcap_yi=row["mcap_yi"],
            float_mcap_yi=row["float_mcap_yi"],
            limit_up=row["limit_up"],
            limit_down=row["limit_down"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )
