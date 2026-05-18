"""Data models for the monitoring system."""


from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AlertType(str, Enum):
    PRICE_CHANGE = "price_change"
    VOLUME_SPIKE = "volume_spike"
    TURNOVER = "turnover"
    LIMIT_UP_DOWN_PROXIMITY = "limit_up_down"
    MA_CROSS = "ma_cross"
    RSI_EXTREME = "rsi_extreme"
    MACD_CROSS = "macd_cross"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Snapshot:
    code: str
    name: str
    price: float
    last_close: float
    open: float
    change_pct: float
    high: float
    low: float
    turnover_pct: float
    pe_ttm: float
    pe_static: float
    pb: float
    mcap_yi: float
    float_mcap_yi: float
    limit_up: float
    limit_down: float
    fetched_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "price": self.price,
            "last_close": self.last_close,
            "open": self.open,
            "change_pct": self.change_pct,
            "high": self.high,
            "low": self.low,
            "turnover_pct": self.turnover_pct,
            "pe_ttm": self.pe_ttm,
            "pe_static": self.pe_static,
            "pb": self.pb,
            "mcap_yi": self.mcap_yi,
            "float_mcap_yi": self.float_mcap_yi,
            "limit_up": self.limit_up,
            "limit_down": self.limit_down,
            "fetched_at": self.fetched_at.isoformat(),
        }


@dataclass
class AlertEvent:
    code: str
    name: str
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    price: float
    change_pct: float
    triggered_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "price": self.price,
            "change_pct": self.change_pct,
            "triggered_at": self.triggered_at.isoformat(),
        }


@dataclass
class DailyBar:
    code: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
