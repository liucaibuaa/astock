"""WebSocket manager for real-time price push to browser clients."""


import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages connected WebSocket clients and broadcasts snapshot updates."""

    def __init__(self):
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, snapshots: dict) -> None:
        """Push latest snapshots to all connected clients."""
        data = {
            "type": "snapshot_update",
            "data": {
                code: snap.to_dict() for code, snap in snapshots.items()
            },
            "timestamp": datetime.utcnow().isoformat(),
        }
        dead: set[WebSocket] = set()
        for ws in self._clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def broadcast_remove(self, code: str) -> None:
        """Notify all clients that a stock has been removed from the watchlist."""
        data = {
            "type": "stock_removed",
            "code": code,
            "timestamp": datetime.utcnow().isoformat(),
        }
        dead: set[WebSocket] = set()
        for ws in self._clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self._clients -= dead


def create_ws_router(ws_manager: WebSocketManager) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws_manager.connect(ws)
        try:
            while True:
                await ws.receive_text()  # keep-alive; handle client pings
        except WebSocketDisconnect:
            await ws_manager.disconnect(ws)
        except Exception:
            await ws_manager.disconnect(ws)

    return router
