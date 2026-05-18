"""Alert condition evaluation engine.

Evaluates configured alert conditions (price change, volume, turnover,
limit proximity, indicator signals) against current snapshots and historical data.
"""


import logging
from datetime import datetime

from a_stock_quant.storage.models import (
    AlertEvent,
    AlertSeverity,
    AlertType,
    Snapshot,
)

logger = logging.getLogger(__name__)

# Rule name to (human label, check method) mapping
INDICATOR_RULES = frozenset({
    "ma_golden_cross", "ma_death_cross",
    "rsi_overbought", "rsi_oversold",
    "macd_golden_cross", "macd_death_cross",
})


class AlertEngine:
    """Evaluates alert conditions against current and historical data."""

    def __init__(self, config: dict, db, indicator_engine):
        self._config = config
        self._db = db
        self._indicators = indicator_engine

    async def evaluate_all(self, snapshots: dict[str, Snapshot]) -> list[AlertEvent]:
        """Run all enabled alert checks. Returns triggered alerts."""
        alerts_cfg = self._config.get("alerts", {})
        triggered: list[AlertEvent] = []

        for code, snap in snapshots.items():
            # Price change
            cfg = alerts_cfg.get("price_change_pct", {})
            if cfg.get("enabled"):
                alert = await self._check_price_change(
                    code, snap, cfg.get("threshold", 3.0), cfg.get("cooldown_seconds", 60)
                )
                if alert:
                    triggered.append(alert)

            # Turnover
            cfg = alerts_cfg.get("turnover_pct", {})
            if cfg.get("enabled"):
                alert = await self._check_turnover(
                    code, snap, cfg.get("threshold", 5.0), cfg.get("cooldown_seconds", 60)
                )
                if alert:
                    triggered.append(alert)

            # Limit up/down proximity
            cfg = alerts_cfg.get("limit_up_down_proximity", {})
            if cfg.get("enabled"):
                alert = await self._check_limit_proximity(
                    code, snap, cfg.get("within_pct", 1.0), cfg.get("cooldown_seconds", 120)
                )
                if alert:
                    triggered.append(alert)

            # Volume spike
            cfg = alerts_cfg.get("volume_spike", {})
            if cfg.get("enabled"):
                alert = await self._check_volume_spike(
                    code, snap, cfg.get("multiplier", 3.0), cfg.get("cooldown_seconds", 300)
                )
                if alert:
                    triggered.append(alert)

            # Indicator signals
            cfg = alerts_cfg.get("indicator_signals", {})
            if cfg.get("enabled") and cfg.get("rules"):
                alerts = await self._check_indicator_signals(
                    code, snap, cfg["rules"], cfg.get("cooldown_seconds", 300)
                )
                triggered.extend(alerts)

        return triggered

    # ---- individual checks ----

    async def _check_price_change(
        self, code: str, snap: Snapshot, threshold: float, cooldown: int
    ) -> AlertEvent | None:
        if abs(snap.change_pct) < threshold:
            return None
        if await self._db.is_in_cooldown(code, AlertType.PRICE_CHANGE.value, cooldown):
            return None

        direction = "up" if snap.change_pct > 0 else "down"
        severity = AlertSeverity.CRITICAL if abs(snap.change_pct) >= threshold * 2 else AlertSeverity.WARNING

        await self._db.set_cooldown(code, AlertType.PRICE_CHANGE.value)
        return AlertEvent(
            code=code,
            name=snap.name,
            alert_type=AlertType.PRICE_CHANGE,
            severity=severity,
            message=f"Price {direction} {abs(snap.change_pct):.2f}% to {snap.price:.2f}",
            price=snap.price,
            change_pct=snap.change_pct,
        )

    async def _check_volume_spike(
        self, code: str, snap: Snapshot, multiplier: float, cooldown: int
    ) -> AlertEvent | None:
        if await self._db.is_in_cooldown(code, AlertType.VOLUME_SPIKE.value, cooldown):
            return None

        df = await self._db.get_daily_bars(code, limit=21)
        if df.empty or len(df) < 20:
            return None

        avg_vol = float(df["Volume"].tail(20).mean())
        current_vol = float(df["Volume"].iloc[-1])
        if avg_vol <= 0 or current_vol <= 0:
            return None

        ratio = current_vol / avg_vol
        if ratio < multiplier:
            return None

        await self._db.set_cooldown(code, AlertType.VOLUME_SPIKE.value)
        return AlertEvent(
            code=code,
            name=snap.name,
            alert_type=AlertType.VOLUME_SPIKE,
            severity=AlertSeverity.WARNING,
            message=f"Volume {ratio:.1f}x average ({current_vol:.0f} vs avg {avg_vol:.0f})",
            price=snap.price,
            change_pct=snap.change_pct,
        )

    async def _check_turnover(
        self, code: str, snap: Snapshot, threshold: float, cooldown: int
    ) -> AlertEvent | None:
        if snap.turnover_pct < threshold:
            return None
        if await self._db.is_in_cooldown(code, AlertType.TURNOVER.value, cooldown):
            return None

        await self._db.set_cooldown(code, AlertType.TURNOVER.value)
        severity = AlertSeverity.WARNING if snap.turnover_pct >= threshold * 2 else AlertSeverity.INFO
        return AlertEvent(
            code=code,
            name=snap.name,
            alert_type=AlertType.TURNOVER,
            severity=severity,
            message=f"Turnover rate {snap.turnover_pct:.2f}%",
            price=snap.price,
            change_pct=snap.change_pct,
        )

    async def _check_limit_proximity(
        self, code: str, snap: Snapshot, within_pct: float, cooldown: int
    ) -> AlertEvent | None:
        if snap.price <= 0 or snap.limit_up <= 0:
            return None

        dist_up = (snap.limit_up - snap.price) / snap.price * 100
        dist_down = (snap.price - snap.limit_down) / snap.price * 100 if snap.limit_down > 0 else 100

        nearest = min(dist_up, dist_down)
        if nearest > within_pct:
            return None
        if await self._db.is_in_cooldown(code, AlertType.LIMIT_UP_DOWN_PROXIMITY.value, cooldown):
            return None

        direction = "limit-up" if dist_up <= dist_down else "limit-down"
        await self._db.set_cooldown(code, AlertType.LIMIT_UP_DOWN_PROXIMITY.value)
        return AlertEvent(
            code=code,
            name=snap.name,
            alert_type=AlertType.LIMIT_UP_DOWN_PROXIMITY,
            severity=AlertSeverity.WARNING,
            message=f"Price {snap.price:.2f} within {nearest:.1f}% of {direction} ({snap.limit_up:.2f}/{snap.limit_down:.2f})",
            price=snap.price,
            change_pct=snap.change_pct,
        )

    async def _check_indicator_signals(
        self, code: str, snap: Snapshot, rules: list[str], cooldown: int
    ) -> list[AlertEvent]:
        triggered: list[AlertEvent] = []

        for rule in rules:
            if rule not in INDICATOR_RULES:
                logger.warning("Unknown indicator rule: %s", rule)
                continue

            if await self._db.is_in_cooldown(code, rule, cooldown):
                continue

            alert = await self._eval_indicator_rule(code, snap, rule)
            if alert:
                await self._db.set_cooldown(code, rule)
                triggered.append(alert)

        return triggered

    async def _eval_indicator_rule(
        self, code: str, snap: Snapshot, rule: str
    ) -> AlertEvent | None:
        if rule == "ma_golden_cross":
            if await self._indicators.check_golden_cross(code):
                return self._make_indicator_alert(code, snap, AlertType.MA_CROSS, "MA golden cross (5/20)")
        elif rule == "ma_death_cross":
            if await self._indicators.check_death_cross(code):
                return self._make_indicator_alert(code, snap, AlertType.MA_CROSS, "MA death cross (5/20)")
        elif rule == "rsi_overbought":
            rsi = await self._indicators.get_rsi(code)
            if rsi > 70:
                return self._make_indicator_alert(code, snap, AlertType.RSI_EXTREME, f"RSI(14) overbought: {rsi:.1f}")
        elif rule == "rsi_oversold":
            rsi = await self._indicators.get_rsi(code)
            if rsi < 30:
                return self._make_indicator_alert(code, snap, AlertType.RSI_EXTREME, f"RSI(14) oversold: {rsi:.1f}")
        elif rule == "macd_golden_cross":
            cross = await self._indicators.check_macd_cross(code)
            if cross == "golden":
                return self._make_indicator_alert(code, snap, AlertType.MACD_CROSS, "MACD golden cross")
        elif rule == "macd_death_cross":
            cross = await self._indicators.check_macd_cross(code)
            if cross == "death":
                return self._make_indicator_alert(code, snap, AlertType.MACD_CROSS, "MACD death cross")
        return None

    @staticmethod
    def _make_indicator_alert(
        code: str, snap: Snapshot, alert_type: AlertType, description: str
    ) -> AlertEvent:
        return AlertEvent(
            code=code,
            name=snap.name,
            alert_type=alert_type,
            severity=AlertSeverity.INFO,
            message=f"[{description}] at price {snap.price:.2f}",
            price=snap.price,
            change_pct=snap.change_pct,
        )
