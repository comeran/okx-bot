from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException

from src.core.config import BacktestConfig, load_config
from src.core.engine import BotEngine
from src.core.types import Order, OrderStatus, OrderType
from src.data.repository import Repository
from src.order.manager import UnifiedOrderManager
from src.order.router import OrderHandler, OrderRouter
from src.strategy.builtin.ma_cross import register_ma_cross
from src.strategy.registry import StrategyRegistry
from src.web.api import trading

PriceProvider = Callable[[str], float | None]
RuntimeBroadcaster = Callable[[dict[str, object]], Awaitable[None]]
OrderUpdateCallback = Callable[[str], Awaitable[None]]


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)


class LocalPaperOrderHandler(OrderHandler):
    def __init__(self, latest_price: PriceProvider | None = None) -> None:
        self.latest_price = latest_price

    async def submit(self, order: Order) -> Order:
        fill_price = self._fill_price(order)
        if fill_price is None:
            order.status = OrderStatus.REJECTED
            order.fill_price = None
            order.fill_time = None
            return order

        order.status = OrderStatus.FILLED
        order.fill_price = fill_price
        order.fill_time = current_timestamp_ms()
        return order

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        return True

    def _fill_price(self, order: Order) -> float | None:
        if order.price is not None and order.price > 0:
            return order.price
        if order.type == OrderType.MARKET and self.latest_price is not None:
            price = self.latest_price(order.symbol)
            if price is not None and price > 0:
                return price
        return None


def paper_backtest_config() -> BacktestConfig:
    try:
        return load_config("config/settings.yaml").backtest
    except FileNotFoundError:
        return BacktestConfig()


def create_order_manager(
    latest_price: PriceProvider | None = None,
    repository: Repository | None = None,
    on_order_update: OrderUpdateCallback | None = None,
) -> UnifiedOrderManager:
    handler = LocalPaperOrderHandler(latest_price=latest_price)
    router = OrderRouter(backtest=handler, mode="backtest")
    backtest_config = paper_backtest_config()
    return UnifiedOrderManager(
        router=router,
        repository=repository or Repository(),
        timestamp_ms=current_timestamp_ms,
        initial_equity=backtest_config.initial_capital,
        fee_rate=backtest_config.fee_rate,
        on_order_update=on_order_update,
    )


def create_strategy_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    register_ma_cross(registry)
    return registry


def strategy_exists(name: str) -> bool:
    return name in create_strategy_registry().list_strategies()


class StrategyRuntimeState:
    def __init__(self, registry: StrategyRegistry | None = None) -> None:
        self.registry = registry or create_strategy_registry()
        self.strategy_status = {name: "stopped" for name in self.registry.list_strategies()}
        self.engines: dict[str, BotEngine] = {}

    def list_strategies(self) -> list[dict[str, str]]:
        return [
            {"name": name, "status": self.strategy_status[name]}
            for name in self.registry.list_strategies()
        ]

    def strategy_exists(self, name: str) -> bool:
        return name in self.strategy_status


def create_router(
    broadcast: RuntimeBroadcaster | None = None,
    runtime: StrategyRuntimeState | None = None,
) -> APIRouter:
    router = APIRouter()
    runtime = runtime or StrategyRuntimeState()

    def strategy_exists(name: str) -> bool:
        return name in runtime.strategy_status

    async def broadcast_status(name: str) -> None:
        if broadcast is None:
            return
        await broadcast(
            {
                "type": "strategy_status",
                "strategy": name,
                "status": runtime.strategy_status[name],
                "timestamp": current_timestamp_ms(),
            }
        )

    async def broadcast_trading_updates(repository: Repository, strategy: str) -> None:
        if broadcast is None:
            return
        positions = (
            repository.get_open_positions(strategy)
            if hasattr(repository, "get_open_positions")
            else repository.get_positions(strategy)
        )
        await broadcast(
            {"type": "orders", "orders": trading.serialize_records(repository.get_orders())}
        )
        await broadcast({"type": "positions", "positions": trading.serialize_records(positions)})
        await broadcast(
            {
                "type": "account",
                "account": trading.serialize_account(repository.get_account(strategy)),
            }
        )

    @router.get("")
    async def list_strategies() -> list[dict[str, str]]:
        return runtime.list_strategies()

    @router.post("/{name}/start")
    async def start_strategy(name: str) -> dict[str, str]:
        if not strategy_exists(name):
            raise HTTPException(status_code=404, detail="Strategy not found")
        if name not in runtime.engines:
            repository = Repository()
            strategy = runtime.registry.create(name)
            set_order_manager = getattr(strategy, "set_order_manager", None)
            if set_order_manager is not None:
                set_order_manager(
                    create_order_manager(
                        repository=repository,
                        on_order_update=lambda strategy_name: broadcast_trading_updates(
                            repository,
                            strategy_name,
                        ),
                    )
                )
            engine = BotEngine(strategies=[strategy])
            await engine.start()
            runtime.engines[name] = engine
        runtime.strategy_status[name] = "running"
        await broadcast_status(name)
        return {"status": "started", "strategy": name}

    @router.post("/{name}/stop")
    async def stop_strategy(name: str) -> dict[str, str]:
        if not strategy_exists(name):
            raise HTTPException(status_code=404, detail="Strategy not found")
        engine = runtime.engines.pop(name, None)
        if engine is not None:
            await engine.stop()
        runtime.strategy_status[name] = "stopped"
        await broadcast_status(name)
        return {"status": "stopped", "strategy": name}

    return router


router = create_router()
