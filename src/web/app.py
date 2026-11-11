from __future__ import annotations

import os
import re
import secrets

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.data.repository import Repository
from src.web.api import allocations, backtest, market, ops, settings, strategies, trading
from src.web.ws import WebSocketManager

ws_manager = WebSocketManager()

API_TOKEN_ENV = "OKX_BOT_API_TOKEN"
LOCAL_ORIGIN_PATTERN = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")


def _is_local_origin(origin: str) -> bool:
    return bool(LOCAL_ORIGIN_PATTERN.match(origin.rstrip("/")))


def _supplied_api_token(headers: object, query_token: str | None = None) -> str:
    if query_token:
        return query_token
    supplied = headers.get("x-api-token", "")
    if supplied:
        return supplied
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""



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
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def require_api_token(request, call_next):  # type: ignore[no-untyped-def]
        token = os.environ.get(API_TOKEN_ENV, "")
        path = request.url.path
        if (
            token
            and request.method != "OPTIONS"
            and path.startswith("/api")
            and path != "/api/health"
        ):
            supplied = _supplied_api_token(request.headers)
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        origin = ws.headers.get("origin", "")
        if origin and not _is_local_origin(origin):
            await ws.close(code=1008, reason="origin not allowed")
            return
        token = os.environ.get(API_TOKEN_ENV, "")
        if token:
            supplied = _supplied_api_token(ws.headers, ws.query_params.get("token"))
            if not secrets.compare_digest(supplied, token):
                await ws.close(code=1008, reason="unauthorized")
                return
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
    app.include_router(
        allocations.create_router(strategy_runtime),
        prefix="/api/allocations", tags=["allocations"],
    )
    app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])
    app.include_router(trading.router, prefix="/api/trading", tags=["trading"])
    app.include_router(market.router, prefix="/api/market", tags=["market"])
    app.include_router(ops.create_router(ws_manager.broadcast), prefix="/api/ops", tags=["ops"])
    app.include_router(settings.create_router(), prefix="/api/settings", tags=["settings"])
    return app


app = create_app()
