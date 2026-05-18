"""FastAPI application factory for the web dashboard."""


from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from a_stock_quant.web.routes import create_api_router
from a_stock_quant.web.websocket import WebSocketManager, create_ws_router

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(engine) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="A-Stock Quant Monitor", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # WebSocket manager
    ws_manager = WebSocketManager()
    engine._ws_manager = ws_manager  # wire into the monitor engine

    # Routes
    app.include_router(create_api_router(engine))
    app.include_router(create_ws_router(ws_manager))

    # Static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def root():
        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return {"message": "A-Stock Quant Monitor API", "docs": "/docs"}

    return app
