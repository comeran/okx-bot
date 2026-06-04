from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from src.core.engine import BotEngine
from src.core.types import Order, OrderStatus
from src.data.repository import Repository
from src.order.manager import UnifiedOrderManager
from src.order.router import OrderHandler, OrderRouter
from src.strategy.builtin.ma_cross import register_ma_cross
from src.strategy.registry import StrategyRegistry


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)


class LocalPaperOrderHandler(OrderHandler):
    async def submit(self, order: Order) -> Order:
        order.status = OrderStatus.FILLED
        order.fill_price = order.price or 0.0
        order.fill_time = current_timestamp_ms()
        return order

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        return True


def create_order_manager() -> UnifiedOrderManager:
    handler = LocalPaperOrderHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    return UnifiedOrderManager(
        router=router,
        repository=Repository(),
        timestamp_ms=current_timestamp_ms,
    )


def create_strategy_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    register_ma_cross(registry)
    return registry


def strategy_exists(name: str) -> bool:
    return name in create_strategy_registry().list_strategies()


def create_router() -> APIRouter:
    router = APIRouter()
    registry = create_strategy_registry()
    strategy_status: dict[str, str] = {name: "stopped" for name in registry.list_strategies()}
    engines: dict[str, BotEngine] = {}

    def strategy_exists(name: str) -> bool:
        return name in strategy_status

    @router.get("")
    async def list_strategies() -> list[dict[str, str]]:
        return [
            {"name": name, "status": strategy_status[name]} for name in registry.list_strategies()
        ]

    @router.post("/{name}/start")
    async def start_strategy(name: str) -> dict[str, str]:
        if not strategy_exists(name):
            raise HTTPException(status_code=404, detail="Strategy not found")
        if name not in engines:
            strategy = registry.create(name)
            set_order_manager = getattr(strategy, "set_order_manager", None)
            if set_order_manager is not None:
                set_order_manager(create_order_manager())
            engine = BotEngine(strategies=[strategy])
            await engine.start()
            engines[name] = engine
        strategy_status[name] = "running"
        return {"status": "started", "strategy": name}

    @router.post("/{name}/stop")
    async def stop_strategy(name: str) -> dict[str, str]:
        if not strategy_exists(name):
            raise HTTPException(status_code=404, detail="Strategy not found")
        engine = engines.pop(name, None)
        if engine is not None:
            await engine.stop()
        strategy_status[name] = "stopped"
        return {"status": "stopped", "strategy": name}

    return router


router = create_router()
