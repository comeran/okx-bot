from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.data.repository import Repository
from src.web.api import backtest, market, ops, settings, strategies, trading
from src.web.ws import WebSocketManager

ws_manager = WebSocketManager()


def runtime_snapshot(strategy_runtime: strategies.StrategyRuntimeState) -> dict[str, object]:
    return ops.exchange_snapshot(Repository())


def strategy_runtime_snapshot(
    strategy_runtime: strategies.StrategyRuntimeState,
    requested_strategy: str,
) -> dict[str, object]:
    repository = Repository()
    wildcard = requested_strategy == "*"
    strategy_statuses = [
        item
        for item in strategy_runtime.list_strategies()
        if wildcard or item["name"] == requested_strategy
    ]
    strategy_configs = []
    get_strategy_configs = getattr(repository, "get_strategy_configs", None)
    if get_strategy_configs is not None:
        for config in get_strategy_configs():
            if not wildcard and config.name != requested_strategy:
                continue
            strategy_configs.append(config.model_dump())
            status = strategy_runtime.strategy_status.get(config.name, "stopped")
            if config.name in strategy_runtime.starting_engines:
                status = "starting"
            elif config.name in strategy_runtime.engines:
                status = "running"
            if not any(item["name"] == config.name for item in strategy_statuses):
                strategy_statuses.append({"name": config.name, "status": status})
    for strategy in strategy_statuses:
        error = strategy_runtime.strategy_errors.get(strategy["name"])
        if error is not None:
            strategy["error"] = error
    strategy_errors = {
        name: error
        for name, error in strategy_runtime.strategy_errors.items()
        if wildcard or name == requested_strategy
    }
    return {
        "type": "snapshot",
        "scope": "strategy",
        "strategy": requested_strategy,
        "data": {
            "scope": "strategy",
            "strategy": requested_strategy,
            "strategies": strategy_statuses,
            "strategy_configs": strategy_configs,
            "strategy_errors": strategy_errors,
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
        requested_scope = ws.query_params.get("scope")
        requested_strategy = ws.query_params.get("strategy")
        if requested_scope not in {"exchange", "strategy"}:
            await ws.close(code=1008, reason="explicit websocket scope required")
            return
        if requested_scope == "strategy" and not requested_strategy:
            await ws.close(code=1008, reason="strategy websocket requires strategy")
            return
        if requested_scope == "strategy" and requested_strategy != "*":
            known_strategies = {
                item["name"] for item in strategy_runtime.list_strategies()
            }
            get_strategy_configs = getattr(Repository(), "get_strategy_configs", None)
            if get_strategy_configs is not None:
                known_strategies.update(config.name for config in get_strategy_configs())
            if requested_strategy not in known_strategies:
                await ws.close(code=1008, reason="unknown websocket strategy")
                return
        await ws_manager.connect(
            ws,
            scope=requested_scope,
            strategy=requested_strategy,
        )
        if requested_scope == "exchange":
            await ws.send_json(runtime_snapshot(strategy_runtime))
        if requested_scope == "strategy":
            await ws.send_json(strategy_runtime_snapshot(strategy_runtime, requested_strategy))
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
