from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.core.config import BacktestConfig
from src.core.engine import BotEngine
from src.core.runtime_settings import load_runtime_settings
from src.core.types import Order, OrderStatus, OrderType
from src.data.models import StrategyConfigRecord
from src.data.repository import Repository
from src.exchange.factory import create_okx_adapter
from src.exchange.live_sync import refresh_okx_live_state
from src.exchange.okx_futures import OKXFuturesAdapter
from src.exchange.okx_options import OKXOptionsAdapter
from src.exchange.okx_spot import OKXSpotAdapter
from src.exchange.okx_swap import OKXSwapAdapter
from src.market.service import MarketDataService
from src.notify.telegram import RiskEventTelegramNotifier, TelegramNotifier
from src.order.manager import UnifiedOrderManager
from src.order.mark_to_market import PaperMarkToMarketService
from src.order.router import OrderHandler, OrderRouter
from src.risk.manager import RiskManager
from src.strategy.builtin.ma_cross import register_ma_cross
from src.strategy.registry import StrategyRegistry
from src.web.api import settings as settings_api
from src.web.api import trading

logger = logging.getLogger(__name__)

PriceProvider = Callable[[str], float | None]
RuntimeBroadcaster = Callable[[dict[str, object]], Awaitable[None]]
OrderUpdateCallback = Callable[[str], Awaitable[None] | None]
RiskEventCallback = Callable[[dict[str, object]], Awaitable[None] | None]
KillSwitchChecker = Callable[[], bool]
_UNSET_ORDER_ROUTER_MODE = object()


class RiskEventNotifier(Protocol):
    async def send_risk_event(self, payload: dict[str, object]) -> None:
        ...


class StrategyConfigRequest(BaseModel):
    name: str
    strategy_type: str
    symbol: str
    timeframe: str
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


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
    return load_runtime_settings().backtest


def create_market_data_service() -> MarketDataService:
    exchange = load_runtime_settings().exchange
    return MarketDataService(exchange.api_key, exchange.secret, exchange.passphrase)


def create_risk_manager(live: bool = False) -> RiskManager:
    risk = load_runtime_settings().risk
    return RiskManager(
        max_position_pct=risk.max_total_position_pct,
        max_daily_loss_pct=risk.max_daily_loss_pct,
        max_drawdown_pct=risk.max_drawdown_pct,
        enforce_daily_loss=live,
        enforce_drawdown=False,
    )


def create_risk_event_notifier() -> RiskEventNotifier | None:
    notify = settings_api._load_settings().notify
    if not notify.telegram_bot_token or not notify.telegram_chat_id:
        return None
    return RiskEventTelegramNotifier(
        TelegramNotifier(
            bot_token=notify.telegram_bot_token,
            chat_id=notify.telegram_chat_id,
        )
    )


def resolve_order_router_mode(mode: object) -> str:
    if not isinstance(mode, str):
        raise ValueError(f"Unsupported strategy runtime mode: {mode}")
    normalized_mode = mode.strip().lower()
    if normalized_mode == "backtest":
        return "backtest"
    if normalized_mode in {"paper", "demo"}:
        return "demo"
    if normalized_mode == "live":
        return "live"
    raise ValueError(f"Unsupported strategy runtime mode: {mode}")


def current_order_router_mode() -> str:
    return resolve_order_router_mode(load_runtime_settings().mode)


def create_live_order_handler(settings: object) -> OrderHandler:
    return create_okx_adapter(
        settings.exchange,
        adapter_classes={
            "spot": OKXSpotAdapter,
            "swap": OKXSwapAdapter,
            "future": OKXFuturesAdapter,
            "futures": OKXFuturesAdapter,
            "option": OKXOptionsAdapter,
            "options": OKXOptionsAdapter,
        },
    )


def create_order_manager(
    latest_price: PriceProvider | None = None,
    repository: Repository | None = None,
    on_order_update: OrderUpdateCallback | None = None,
    on_risk_event: RiskEventCallback | None = None,
    kill_switch_checker: KillSwitchChecker | None = None,
    order_router_mode: object = _UNSET_ORDER_ROUTER_MODE,
) -> UnifiedOrderManager:
    settings = load_runtime_settings()
    resolved_mode = (
        resolve_order_router_mode(settings.mode)
        if order_router_mode is _UNSET_ORDER_ROUTER_MODE
        else resolve_order_router_mode(order_router_mode)
    )
    if resolved_mode == "live":
        live_handler = create_live_order_handler(settings)
        handler = None
    else:
        live_handler = None
        handler = LocalPaperOrderHandler(latest_price=latest_price)
    order_repository = repository if repository is not None else Repository()
    router = OrderRouter(
        backtest=handler if resolved_mode == "backtest" else None,
        demo=handler if resolved_mode == "demo" else None,
        live=live_handler,
        mode=resolved_mode,
    )
    backtest_config = paper_backtest_config()
    risk_config = settings.risk

    async def live_state_refresher(strategy_name: str, symbol: str) -> None:
        await refresh_okx_live_state(
            settings.exchange,
            order_repository,
            strategy_name,
            [symbol],
            current_timestamp_ms,
        )

    return UnifiedOrderManager(
        router=router,
        repository=order_repository,
        timestamp_ms=current_timestamp_ms,
        initial_equity=backtest_config.initial_capital,
        fee_rate=backtest_config.fee_rate,
        on_order_update=on_order_update,
        on_risk_event=on_risk_event,
        risk_manager=create_risk_manager(live=resolved_mode == "live"),
        price_provider=latest_price,
        kill_switch_checker=kill_switch_checker,
        live_safeguards=resolved_mode == "live",
        live_market_type=settings.exchange.market_type if resolved_mode == "live" else "",
        live_state_refresher=live_state_refresher if resolved_mode == "live" else None,
        allow_live_open_orders=(
            risk_config.allow_live_open_orders if resolved_mode == "live" else False
        ),
        live_max_order_notional=(
            risk_config.live_max_order_notional if resolved_mode == "live" else 0.0
        ),
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
        self.strategy_errors: dict[str, str] = {}
        self.engines: dict[str, BotEngine] = {}
        self.starting_engines: dict[str, BotEngine] = {}
        self.lifecycle_locks: dict[str, asyncio.Lock] = {}
        self.market_data_lifecycle_lock = asyncio.Lock()

    def lifecycle_lock(self, name: str) -> asyncio.Lock:
        lock = self.lifecycle_locks.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self.lifecycle_locks[name] = lock
        return lock

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
    config_repository = Repository()
    risk_event_notifier = create_risk_event_notifier()
    market_data_service: MarketDataService | None = None

    def get_market_data_service() -> MarketDataService:
        nonlocal market_data_service
        if market_data_service is None:
            market_data_service = create_market_data_service()
        return market_data_service

    async def release_market_data_service_if_idle() -> None:
        nonlocal market_data_service
        async with runtime.market_data_lifecycle_lock:
            if runtime.engines or runtime.starting_engines or market_data_service is None:
                return
            stop = getattr(market_data_service, "stop", None)
            if stop is not None:
                await stop()
            market_data_service = None

    def get_persisted_strategy_config(name: str) -> StrategyConfigRecord | None:
        get_strategy_config = getattr(config_repository, "get_strategy_config", None)
        if get_strategy_config is not None:
            return get_strategy_config(name)
        get_strategy_configs = getattr(config_repository, "get_strategy_configs", None)
        if get_strategy_configs is None:
            return None
        return next(
            (config for config in get_strategy_configs() if config.name == name),
            None,
        )

    def strategy_exists(name: str) -> bool:
        return name in runtime.strategy_status or get_persisted_strategy_config(name) is not None

    def create_strategy(name: str):
        config = get_persisted_strategy_config(name)
        if config is None:
            return runtime.registry.create(name)
        if config.strategy_type not in runtime.registry.list_strategies():
            raise HTTPException(status_code=404, detail="Strategy not found")
        params = {**config.params, "symbol": config.symbol}
        strategy = runtime.registry.create(config.strategy_type, **params)
        strategy.name = config.name
        strategy.timeframe = config.timeframe
        runtime.strategy_status.setdefault(config.name, "stopped")
        return strategy

    def latest_price_for_strategy(strategy) -> PriceProvider:
        timeframe = getattr(strategy, "timeframe", None)

        def latest_price(symbol: str) -> float | None:
            if timeframe is None:
                return None
            bars = get_market_data_service().get_recent_bars(symbol, timeframe, count=1)
            if not bars:
                return None
            return bars[-1].close

        return latest_price

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

    async def handle_strategy_error(
        name: str,
        error: Exception,
        engine: BotEngine | None = None,
    ) -> None:
        if engine is not None and (
            runtime.engines.get(name) is not engine
            and runtime.starting_engines.get(name) is not engine
        ):
            return
        runtime.strategy_status[name] = "stopped"
        runtime.strategy_errors[name] = str(error)
        if engine is None or runtime.engines.get(name) is engine:
            runtime.engines.pop(name, None)
        if engine is None or runtime.starting_engines.get(name) is engine:
            runtime.starting_engines.pop(name, None)
        await release_market_data_service_if_idle()
        await broadcast_status(name)
        if broadcast is not None:
            await broadcast(
                {
                    "type": "strategy_error",
                    "strategy": name,
                    "error": str(error),
                    "timestamp": current_timestamp_ms(),
                }
            )

    async def broadcast_trading_updates(
        repository: Repository,
        strategy: str,
        include_orders: bool = True,
    ) -> None:
        if broadcast is None:
            return
        positions = (
            repository.get_open_positions(strategy)
            if hasattr(repository, "get_open_positions")
            else repository.get_positions(strategy)
        )
        if include_orders:
            await broadcast(
                {"type": "orders", "orders": trading.serialize_records(repository.get_orders())}
            )
        account = repository.get_account(strategy)
        if account is None:
            await broadcast(
                {"type": "positions", "positions": trading.serialize_records(positions)}
            )
        else:
            await broadcast_position_account_updates(positions, account)

    async def broadcast_position_account_updates(positions, account) -> None:
        if broadcast is None:
            return
        await broadcast({"type": "positions", "positions": trading.serialize_records(positions)})
        await broadcast(
            {
                "type": "account",
                "account": trading.serialize_account(account),
            }
        )

    async def persist_broadcast_and_notify_risk_event(
        repository: Repository,
        payload: dict[str, object],
    ) -> None:
        repository.save_risk_event(payload)
        if broadcast is not None:
            await broadcast(payload)
        if risk_event_notifier is None:
            return
        try:
            await risk_event_notifier.send_risk_event(payload)
        except Exception:
            logger.warning("Failed to send Telegram risk notification", exc_info=True)

    def kill_switch_engaged(repository: Repository) -> bool:
        get_kill_switch = getattr(repository, "get_kill_switch", None)
        return get_kill_switch is not None and get_kill_switch().engaged

    def create_mark_to_market_service(repository: Repository) -> PaperMarkToMarketService | None:
        required_repository_methods = (
            "get_position",
            "upsert_position",
            "get_open_positions",
            "get_account",
            "upsert_account",
        )
        if not all(hasattr(repository, method) for method in required_repository_methods):
            return None
        return PaperMarkToMarketService(
            repository,
            initial_equity=paper_backtest_config().initial_capital,
        )

    async def mark_to_market_before_bar(
        mark_to_market: PaperMarkToMarketService | None,
        strategy,
        bar,
    ) -> None:
        symbol = getattr(strategy, "symbol", None)
        if symbol is None or mark_to_market is None:
            return
        update = mark_to_market.mark_update(
            strategy_name=strategy.name,
            symbol=symbol,
            mark_price=bar.close,
            timestamp=bar.timestamp,
        )
        if update is not None:
            await broadcast_position_account_updates(update.positions, update.account)

    def serialize_strategy_config(config: StrategyConfigRecord) -> dict[str, Any]:
        return config.model_dump()

    def list_persisted_strategy_statuses() -> list[dict[str, str]]:
        return [
            {
                "name": config.name,
                "status": runtime.strategy_status.get(config.name, "stopped"),
            }
            for config in config_repository.get_strategy_configs()
        ]

    @router.get("")
    async def list_strategies() -> list[dict[str, str]]:
        strategies = runtime.list_strategies()
        known_names = {strategy["name"] for strategy in strategies}
        for strategy in list_persisted_strategy_statuses():
            if strategy["name"] not in known_names:
                strategies.append(strategy)
        return strategies

    @router.get("/configs")
    async def list_strategy_configs() -> list[dict[str, Any]]:
        return [
            serialize_strategy_config(config) for config in config_repository.get_strategy_configs()
        ]

    @router.post("/configs")
    async def save_strategy_config(config: StrategyConfigRequest) -> dict[str, Any]:
        if config.strategy_type != "ma_cross":
            raise HTTPException(
                status_code=400,
                detail="Only ma_cross strategy configs are supported",
            )
        now = current_timestamp_ms()
        saved = config_repository.upsert_strategy_config(
            StrategyConfigRecord(
                name=config.name,
                strategy_type=config.strategy_type,
                symbol=config.symbol,
                timeframe=config.timeframe,
                params=config.params,
                enabled=config.enabled,
                created_at=now,
                updated_at=now,
            )
        )
        runtime.strategy_status.setdefault(saved.name, "stopped")
        return serialize_strategy_config(saved)

    @router.post("/{name}/start")
    async def start_strategy(name: str) -> dict[str, str]:
        try:
            order_router_mode = current_order_router_mode()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if not strategy_exists(name):
            raise HTTPException(status_code=404, detail="Strategy not found")
        async with runtime.lifecycle_lock(name):
            if name not in runtime.engines:
                try:
                    runtime.strategy_errors.pop(name, None)
                    repository = Repository()
                    if kill_switch_engaged(repository):
                        raise HTTPException(status_code=423, detail="Kill switch engaged")
                    mark_to_market = create_mark_to_market_service(repository)
                    strategy = create_strategy(name)
                    if order_router_mode == "live":
                        await refresh_okx_live_state(
                            load_runtime_settings().exchange,
                            repository,
                            strategy.name,
                            [strategy.symbol],
                            current_timestamp_ms,
                        )
                    set_order_manager = getattr(strategy, "set_order_manager", None)
                    if set_order_manager is not None:
                        set_order_manager(
                            create_order_manager(
                                latest_price=latest_price_for_strategy(strategy),
                                repository=repository,
                                on_order_update=lambda strategy_name: broadcast_trading_updates(
                                    repository,
                                    strategy_name,
                                ),
                                on_risk_event=lambda payload: persist_broadcast_and_notify_risk_event(
                                    repository,
                                    payload,
                                ),
                                kill_switch_checker=lambda: kill_switch_engaged(repository),
                                order_router_mode=order_router_mode,
                            )
                        )
                    engine: BotEngine | None = None

                    async def handle_current_engine_error(
                        error_name: str,
                        error: Exception,
                    ) -> None:
                        await handle_strategy_error(error_name, error, engine)

                    async with runtime.market_data_lifecycle_lock:
                        engine = BotEngine(
                            strategies=[strategy],
                            market_data_service=get_market_data_service(),
                            on_strategy_error=handle_current_engine_error,
                            before_strategy_bar=lambda strategy, bar: mark_to_market_before_bar(
                                mark_to_market,
                                strategy,
                                bar,
                            ),
                            stop_market_data_on_stop=False,
                        )
                        runtime.starting_engines[name] = engine
                    await engine.start()
                    async with runtime.market_data_lifecycle_lock:
                        if name in runtime.strategy_errors:
                            if runtime.starting_engines.get(name) is engine:
                                runtime.starting_engines.pop(name, None)
                            raise HTTPException(
                                status_code=400,
                                detail=runtime.strategy_errors[name],
                            )
                        if runtime.starting_engines.get(name) is engine:
                            runtime.starting_engines.pop(name, None)
                        runtime.engines[name] = engine
                except HTTPException:
                    raise
                except Exception as exc:
                    await handle_strategy_error(name, exc)
                    raise HTTPException(status_code=400, detail=str(exc)) from None
            runtime.strategy_status[name] = "running"
            runtime.strategy_errors.pop(name, None)
            await broadcast_status(name)
        return {"status": "started", "strategy": name}

    @router.post("/{name}/stop")
    async def stop_strategy(name: str) -> dict[str, str]:
        if not strategy_exists(name):
            raise HTTPException(status_code=404, detail="Strategy not found")
        async with runtime.lifecycle_lock(name):
            engine = runtime.engines.pop(name, None)
            if engine is not None:
                await engine.stop()
                await release_market_data_service_if_idle()
            runtime.strategy_status[name] = "stopped"
            await broadcast_status(name)
        return {"status": "stopped", "strategy": name}

    return router


router = create_router()
