"""REST API routes for the web dashboard."""


from fastapi import APIRouter, HTTPException

from a_stock_quant.data.ticker import resolve_ticker


def create_api_router(engine) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/watchlist")
    async def get_watchlist():
        """Get current watchlist with latest prices."""
        if not engine._db:
            return {"watchlist": [], "snapshots": {}}

        codes = engine.get_watchlist()
        snapshots = await engine._db.get_latest_snapshots(codes)
        return {
            "watchlist": codes,
            "snapshots": {s.code: s.to_dict() for s in snapshots},
        }

    @router.post("/watchlist")
    async def add_stock(payload: dict):
        """Add a stock to the watchlist. Body: {"code": "000001"}."""
        user_input = payload.get("code", "").strip()
        if not user_input:
            raise HTTPException(400, "code is required")
        try:
            code = await engine.add_stock(user_input)
            return {"status": "ok", "code": code}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.delete("/watchlist/{code}")
    async def remove_stock(code: str):
        """Remove a stock from the watchlist."""
        try:
            resolved = await engine.remove_stock(code)
            return {"status": "ok", "code": resolved}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.get("/snapshots/{code}")
    async def get_history(code: str, minutes: int = 60):
        """Get recent price history for a stock."""
        from datetime import datetime, timedelta

        if not engine._db:
            return {"code": code, "snapshots": []}

        start = datetime.utcnow() - timedelta(minutes=minutes)
        snapshots = await engine._db.get_snapshot_history(code, start=start)
        return {
            "code": code,
            "snapshots": [s.to_dict() for s in snapshots],
        }

    @router.get("/alerts")
    async def get_alerts(limit: int = 50):
        """Get recent alerts."""
        if not engine._db:
            return {"alerts": []}
        alerts = await engine._db.get_recent_alerts(limit=limit)
        return {"alerts": alerts}

    @router.get("/status")
    async def get_status():
        """Get engine status."""
        from a_stock_quant.engine.scheduler import TradingSession

        return {
            "running": engine._running,
            "poll_count": engine.poll_count,
            "alert_count": engine.alert_count,
            "watchlist_size": len(engine._watchlist),
            "last_poll_time": engine.last_poll_time.isoformat() if engine.last_poll_time else None,
            "started_at": engine.started_at.isoformat() if engine.started_at else None,
            "market_open_today": TradingSession.is_market_open_today(),
            "is_trading_time": TradingSession.is_trading_time(),
        }

    return router
