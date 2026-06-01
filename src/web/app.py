from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.web.api import backtest, market, strategies, trading
from src.web.ws import WebSocketManager

ws_manager = WebSocketManager()


def create_app() -> FastAPI:
    app = FastAPI(title="OKX Bot API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws_manager.connect(ws)
        await ws.send_json({"type": "connected"})
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)

    app.include_router(strategies.create_router(), prefix="/api/strategies", tags=["strategies"])
    app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])
    app.include_router(trading.router, prefix="/api/trading", tags=["trading"])
    app.include_router(market.router, prefix="/api/market", tags=["market"])
    return app


app = create_app()
