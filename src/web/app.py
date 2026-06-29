from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.data.repository import Repository
from src.web.api import backtest, market, ops, settings, strategies, trading
from src.web.ws import WebSocketManager

ws_manager = WebSocketManager()


def runtime_snapshot(strategy_runtime: strategies.StrategyRuntimeState) -> dict[str, object]:
    repository = Repository()
    positions = (
        repository.get_open_positions()
        if hasattr(repository, "get_open_positions")
        else repository.get_positions()
    )
    orders = repository.get_orders() if hasattr(repository, "get_orders") else []
    account = repository.get_account() if hasattr(repository, "get_account") else None
    strategy_statuses = strategy_runtime.list_strategies()
    known_strategy_names = {strategy["name"] for strategy in strategy_statuses}
    get_strategy_configs = getattr(repository, "get_strategy_configs", None)
    if get_strategy_configs is not None:
        for config in get_strategy_configs():
            if config.name not in known_strategy_names:
                strategy_statuses.append(
                    {
                        "name": config.name,
                        "status": strategy_runtime.strategy_status.get(config.name, "stopped"),
                    }
                )
    return {
        "type": "snapshot",
        "data": {
            "account": trading.serialize_account(account),
            "positions": trading.serialize_records(positions),
            "orders": trading.serialize_records(orders),
            "strategies": strategy_statuses,
        },
    }


def create_app() -> FastAPI:
    strategy_runtime = strategies.StrategyRuntimeState()
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
        await ws.send_json(runtime_snapshot(strategy_runtime))
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)

    app.include_router(
        strategies.create_router(ws_manager.broadcast, strategy_runtime),
        prefix="/api/strategies",
        tags=["strategies"],
    )
    app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])
    app.include_router(trading.router, prefix="/api/trading", tags=["trading"])
    app.include_router(market.router, prefix="/api/market", tags=["market"])
    app.include_router(ops.create_router(ws_manager.broadcast), prefix="/api/ops", tags=["ops"])
    app.include_router(settings.create_router(), prefix="/api/settings", tags=["settings"])
    return app


app = create_app()
