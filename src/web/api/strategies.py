from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy.exc import IntegrityError

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
from src.ops.private_sync import sync_private_state
from src.order.manager import UnifiedOrderManager
from src.order.mark_to_market import PaperMarkToMarketService
from src.order.router import OrderHandler, OrderRouter
from src.risk.manager import RiskManager
from src.strategy.builtin import register_builtin_strategies
from src.strategy.definitions import (
    NormalizedStrategyConfig,
    StrategyConfigValidationError,
    StrategyValidationIssue,
)
from src.strategy.registry import StrategyRegistry
from src.web.api import settings as settings_api
from src.web.api import trading
from src.web.strategy_config_yaml import (
    StrategyConfigYamlError,
    dump_strategy_config_yaml,
    load_strategy_config_yaml,
)

logger = logging.getLogger(__name__)

PriceProvider = Callable[[str], float | None]
RuntimeBroadcaster = Callable[[dict[str, object]], Awaitable[None]]
OrderUpdateCallback = Callable[[str], Awaitable[None] | None]
RiskEventCallback = Callable[[dict[str, object]], Awaitable[None] | None]
KillSwitchChecker = Callable[[], bool]
_UNSET_ORDER_ROUTER_MODE = object()


class RiskEventNotifier(Protocol):
    async def send_risk_event(self, payload: dict[str, object]) -> None: ...


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
    return MarketDataService(
        exchange.api_key,
        exchange.secret,
        exchange.passphrase,
        default_type=exchange.market_type,
        demo=exchange.demo,
    )


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

    async def post_live_order_sync(strategy_name: str, symbol: str) -> None:
        if live_handler is None:
            return
        await sync_private_state(
            order_repository,
            live_handler,
            symbols=[symbol],
            timestamp_ms=current_timestamp_ms,
            risk_event_notifier=on_risk_event,
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
        post_live_order_sync=post_live_order_sync if resolved_mode == "live" else None,
        allow_live_open_orders=(
            risk_config.allow_live_open_orders if resolved_mode == "live" else False
        ),
        live_max_order_notional=(
            risk_config.live_max_order_notional if resolved_mode == "live" else 0.0
        ),
    )


def create_strategy_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    register_builtin_strategies(registry)
    return registry


def strategy_exists(name: str) -> bool:
    return name in create_strategy_registry().list_strategies()


class _StrategyOwnerEpochTracker:
    def __init__(self) -> None:
        self._epochs: dict[str, int] = {}
        self._last_owner: dict[str, tuple[weakref.ReferenceType[object] | None, int]] = {}

    def record_assignment(self, name: str, owner: BotEngine) -> None:
        previous_owner_identity = self._last_owner.get(name)
        if previous_owner_identity is not None and not self._same_owner(
            previous_owner_identity,
            owner,
        ):
            self._epochs[name] = self.epoch(name) + 1
        else:
            self._epochs.setdefault(name, 0)
        self._last_owner[name] = self._owner_identity(owner)

    def _owner_identity(self, owner: object) -> tuple[weakref.ReferenceType[object] | None, int]:
        try:
            return weakref.ref(owner), id(owner)
        except TypeError:
            return None, id(owner)

    def _same_owner(
        self,
        previous_owner_identity: tuple[weakref.ReferenceType[object] | None, int],
        owner: object,
    ) -> bool:
        previous_owner_ref, previous_owner_id = previous_owner_identity
        if previous_owner_ref is None:
            return previous_owner_id == id(owner)
        previous_owner = previous_owner_ref()
        return previous_owner is owner

    def epoch(self, name: str) -> int:
        return self._epochs.get(name, 0)


class _StrategyOwnerMap(dict[str, BotEngine]):
    def __init__(self, tracker: _StrategyOwnerEpochTracker) -> None:
        super().__init__()
        self._tracker = tracker

    def __setitem__(self, name: str, owner: BotEngine) -> None:
        self._tracker.record_assignment(name, owner)
        super().__setitem__(name, owner)

    def update(self, *args: Any, **kwargs: BotEngine) -> None:
        for name, owner in dict(*args, **kwargs).items():
            self[name] = owner

    def owner_epoch(self, name: str) -> int:
        return self._tracker.epoch(name)


class StrategyRuntimeState:
    def __init__(self, registry: StrategyRegistry | None = None) -> None:
        self.registry = registry or create_strategy_registry()
        self.strategy_status = {
            name: "stopped" for name in self.registry.list_implicit_strategies()
        }
        self.strategy_errors: dict[str, str] = {}
        owner_epochs = _StrategyOwnerEpochTracker()
        self.engines: dict[str, BotEngine] = _StrategyOwnerMap(owner_epochs)
        self.starting_engines: dict[str, BotEngine] = _StrategyOwnerMap(owner_epochs)
        self.lifecycle_locks: dict[str, asyncio.Lock] = {}
        self.market_data_lifecycle_lock = asyncio.Lock()

    def lifecycle_lock(self, name: str) -> asyncio.Lock:
        lock = self.lifecycle_locks.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self.lifecycle_locks[name] = lock
        return lock

    def list_strategies(self) -> list[dict[str, str]]:
        return [{"name": name, "status": status} for name, status in self.strategy_status.items()]

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
        strategy = runtime.registry.create_instance(
            config.name,
            config.strategy_type,
            config.symbol,
            config.timeframe,
            config.params,
        )
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
        def owner_epoch() -> int:
            engines_epoch = getattr(runtime.engines, "owner_epoch", None)
            if engines_epoch is not None:
                return engines_epoch(name)
            starting_epoch = getattr(runtime.starting_engines, "owner_epoch", None)
            if starting_epoch is not None:
                return starting_epoch(name)
            return 0

        def engine_superseded(initial_epoch: int) -> bool:
            if engine is None:
                return False
            if owner_epoch() != initial_epoch:
                return True
            return any(
                owner is not None and owner is not engine
                for owner in (
                    runtime.engines.get(name),
                    runtime.starting_engines.get(name),
                )
            )

        if engine is not None and (
            runtime.engines.get(name) is not engine
            and runtime.starting_engines.get(name) is not engine
        ):
            return
        initial_owner_epoch = owner_epoch()
        if engine is None or runtime.engines.get(name) is engine:
            runtime.engines.pop(name, None)
        if engine is None:
            runtime.starting_engines.pop(name, None)
        await release_market_data_service_if_idle()
        if engine_superseded(initial_owner_epoch):
            return
        runtime.strategy_status[name] = "stopped"
        runtime.strategy_errors[name] = str(error)
        await broadcast_status(name)
        if engine_superseded(initial_owner_epoch):
            return
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

    def is_active(name: str) -> bool:
        return name in runtime.engines or name in runtime.starting_engines

    def status_for_strategy(name: str) -> str:
        if name in runtime.starting_engines:
            return "starting"
        if name in runtime.engines:
            return "running"
        return runtime.strategy_status.get(name, "stopped")

    def issue_to_dict(issue: StrategyValidationIssue) -> dict[str, Any]:
        return {
            "path": issue.path,
            "code": issue.code,
            "message": issue.message,
            "line": None,
            "column": None,
        }

    def validation_detail(
        code: str,
        message: str,
        issues: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {"code": code, "message": message, "issues": issues}

    def malformed_config_detail(issue_code: str, message: str) -> dict[str, Any]:
        return validation_detail(
            "malformed_config",
            "Strategy config must be a JSON object",
            [
                {
                    "path": "",
                    "code": issue_code,
                    "message": message,
                    "line": None,
                    "column": None,
                }
            ],
        )

    async def read_json_mapping(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400,
                detail=malformed_config_detail("malformed_json", "Malformed JSON body"),
            ) from None
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail=malformed_config_detail(
                    "invalid_root",
                    "Strategy config must be a mapping",
                ),
            )
        return payload

    def semantic_validation_detail(issues: list[dict[str, Any]]) -> dict[str, Any]:
        return validation_detail(
            "strategy_validation_failed",
            "Strategy configuration is invalid",
            issues,
        )

    def normalized_to_dict(config: NormalizedStrategyConfig) -> dict[str, Any]:
        return {
            "name": config.name,
            "strategy_type": config.strategy_type,
            "symbol": config.symbol,
            "timeframe": config.timeframe,
            "enabled": config.enabled,
            "params": config.params,
        }

    def record_from_normalized(
        config: NormalizedStrategyConfig,
        *,
        created_at: int,
        updated_at: int,
    ) -> StrategyConfigRecord:
        return StrategyConfigRecord(
            name=config.name,
            strategy_type=config.strategy_type,
            symbol=config.symbol,
            timeframe=config.timeframe,
            params=config.params,
            enabled=config.enabled,
            created_at=created_at,
            updated_at=updated_at,
        )

    def normalize_config_payload(
        payload: dict[str, Any],
        *,
        expected_name: str | None = None,
        yaml_payload: bool = False,
    ) -> NormalizedStrategyConfig:
        issues: list[StrategyValidationIssue] = []
        try:
            normalized = runtime.registry.normalize_config(payload)
        except StrategyConfigValidationError as exc:
            issues.extend(exc.issues)
            normalized = None
        if expected_name is not None:
            if normalized is not None:
                payload_name = normalized.name
            else:
                raw_name = payload.get("name")
                payload_name = raw_name.strip() if isinstance(raw_name, str) else raw_name
            if payload_name != expected_name:
                issues.append(
                    StrategyValidationIssue(
                        path="name",
                        code="name_mismatch",
                        message="Config name must match expected name",
                    )
                )
        if issues:
            unsupported = any(issue.code == "unsupported_strategy_type" for issue in issues)
            unknown_field = any(issue.code == "unknown_field" for issue in issues)
            status_code = 400 if unsupported or (yaml_payload and unknown_field) else 422
            raise HTTPException(
                status_code=status_code,
                detail=semantic_validation_detail([issue_to_dict(issue) for issue in issues]),
            )
        assert normalized is not None
        return normalized

    def ensure_mutation_allowed(name: str, *, deleting: bool = False) -> None:
        if is_active(name):
            raise HTTPException(status_code=409, detail="Strategy is active")
        positions = (
            config_repository.get_open_positions(name)
            if hasattr(config_repository, "get_open_positions")
            else []
        )
        if any(position.amount != 0 for position in positions):
            action = "delete" if deleting else "update"
            raise HTTPException(
                status_code=409,
                detail=f"Cannot {action} strategy config with open positions",
            )

    async def broadcast_config_event(
        event_type: str,
        config: StrategyConfigRecord | str,
        *,
        timestamp: int,
    ) -> None:
        if broadcast is None:
            return
        name = config if isinstance(config, str) else config.name
        payload: dict[str, object] = {
            "type": event_type,
            "strategy": name,
            "timestamp": timestamp,
        }
        if not isinstance(config, str):
            payload["config"] = serialize_strategy_config(config)
        await broadcast(payload)

    def list_persisted_strategy_statuses() -> list[dict[str, str]]:
        return [
            {
                "name": config.name,
                "status": status_for_strategy(config.name),
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

    @router.get("/types")
    async def list_strategy_types() -> list[dict[str, Any]]:
        return [definition.to_dict() for definition in runtime.registry.list_definitions()]

    @router.get("/configs")
    async def list_strategy_configs() -> list[dict[str, Any]]:
        return [
            serialize_strategy_config(config) for config in config_repository.get_strategy_configs()
        ]

    @router.post("/configs/validate")
    async def validate_strategy_config(
        request: Request,
        expected_name: str | None = Query(default=None),
    ) -> dict[str, Any]:
        payload = await read_json_mapping(request)
        normalized = normalize_config_payload(payload, expected_name=expected_name)
        config_dict = normalized_to_dict(normalized)
        return {"config": config_dict, "yaml": dump_strategy_config_yaml(config_dict)}

    @router.post("/configs/validate-yaml")
    async def validate_strategy_config_yaml(
        request: Request,
        expected_name: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            content = (await request.body()).decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail=validation_detail(
                    "strategy_yaml_invalid",
                    "Strategy YAML is invalid",
                    [
                        {
                            "path": "",
                            "code": "invalid_encoding",
                            "message": "YAML body must be valid UTF-8",
                            "line": None,
                            "column": None,
                        }
                    ],
                ),
            ) from None
        try:
            payload = load_strategy_config_yaml(content)
        except StrategyConfigYamlError as exc:
            raise HTTPException(
                status_code=400,
                detail=validation_detail(
                    "strategy_yaml_invalid",
                    "Strategy YAML is invalid",
                    [
                        {
                            "path": "",
                            "code": exc.code,
                            "message": exc.message,
                            "line": exc.line,
                            "column": exc.column,
                        }
                    ],
                ),
            ) from None
        normalized = normalize_config_payload(
            payload,
            expected_name=expected_name,
            yaml_payload=True,
        )
        config_dict = normalized_to_dict(normalized)
        return {"config": config_dict, "yaml": dump_strategy_config_yaml(config_dict)}

    @router.get("/configs/{name}")
    async def get_strategy_config(name: str) -> dict[str, Any]:
        config = get_persisted_strategy_config(name)
        if config is None:
            raise HTTPException(status_code=404, detail="Strategy config not found")
        return serialize_strategy_config(config)

    @router.post("/configs", status_code=201)
    async def create_strategy_config(request: Request) -> dict[str, Any]:
        payload = await read_json_mapping(request)
        normalized = normalize_config_payload(payload)
        async with runtime.lifecycle_lock(normalized.name):
            if is_active(normalized.name):
                raise HTTPException(status_code=409, detail="Strategy is active")
            try:
                now = current_timestamp_ms()
                saved = config_repository.create_strategy_config(
                    record_from_normalized(normalized, created_at=now, updated_at=now)
                )
            except IntegrityError:
                raise HTTPException(
                    status_code=409,
                    detail="Strategy config already exists",
                ) from None
            except Exception:
                logger.exception("Failed to create strategy config %s", normalized.name)
                raise HTTPException(
                    status_code=500,
                    detail="Failed to create strategy config",
                ) from None
            runtime.strategy_status.setdefault(saved.name, "stopped")
            event_timestamp = saved.updated_at
        await broadcast_config_event(
            "strategy_config_created",
            saved,
            timestamp=event_timestamp,
        )
        return serialize_strategy_config(saved)

    @router.put("/configs/{name}")
    async def update_strategy_config(name: str, request: Request) -> dict[str, Any]:
        payload = await read_json_mapping(request)
        normalized = normalize_config_payload(payload, expected_name=name)
        async with runtime.lifecycle_lock(name):
            existing = get_persisted_strategy_config(name)
            if existing is None:
                raise HTTPException(status_code=404, detail="Strategy config not found")
            ensure_mutation_allowed(name)
            if normalized.strategy_type != existing.strategy_type:
                issue = StrategyValidationIssue(
                    path="strategy_type",
                    code="strategy_type_mismatch",
                    message="Config strategy type must match existing strategy type",
                )
                raise HTTPException(
                    status_code=422,
                    detail=semantic_validation_detail([issue_to_dict(issue)]),
                )
            try:
                saved = config_repository.update_strategy_config(
                    name,
                    record_from_normalized(
                        normalized,
                        created_at=existing.created_at,
                        updated_at=current_timestamp_ms(),
                    ),
                )
            except Exception:
                logger.exception("Failed to update strategy config %s", name)
                raise HTTPException(
                    status_code=500,
                    detail="Failed to update strategy config",
                ) from None
            if saved is None:
                raise HTTPException(status_code=404, detail="Strategy config not found")
            runtime.strategy_status.setdefault(saved.name, "stopped")
            event_timestamp = saved.updated_at
        await broadcast_config_event(
            "strategy_config_updated",
            saved,
            timestamp=event_timestamp,
        )
        return serialize_strategy_config(saved)

    @router.post("/configs/{name}/clone", status_code=201)
    async def clone_strategy_config(name: str, request: Request) -> dict[str, Any]:
        clone = await read_json_mapping(request)
        unknown_request_fields = sorted(set(clone) - {"target_name", "overrides"})
        if unknown_request_fields:
            raise HTTPException(
                status_code=422,
                detail=semantic_validation_detail(
                    [
                        {
                            "path": field,
                            "code": "unknown_field",
                            "message": "Unknown clone request field",
                            "line": None,
                            "column": None,
                        }
                        for field in unknown_request_fields
                    ]
                ),
            )
        target_name = clone.get("target_name")
        overrides = clone.get("overrides", {})
        if not isinstance(overrides, dict):
            raise HTTPException(
                status_code=422,
                detail=semantic_validation_detail(
                    [
                        {
                            "path": "overrides",
                            "code": "invalid_type",
                            "message": "Overrides must be a mapping",
                            "line": None,
                            "column": None,
                        }
                    ]
                ),
            )
        source = get_persisted_strategy_config(name)
        if source is None:
            raise HTTPException(status_code=404, detail="Strategy config not found")
        payload = {
            "name": target_name,
            "strategy_type": source.strategy_type,
            "symbol": source.symbol,
            "timeframe": source.timeframe,
            "enabled": False,
            "params": dict(source.params),
        }
        overrides = dict(overrides)
        override_params = overrides.pop("params", None)
        if override_params is not None and not isinstance(override_params, dict):
            raise HTTPException(
                status_code=422,
                detail=semantic_validation_detail(
                    [
                        {
                            "path": "overrides.params",
                            "code": "invalid_type",
                            "message": "Override params must be a mapping",
                            "line": None,
                            "column": None,
                        }
                    ]
                ),
            )
        payload.update(overrides)
        strategy_type = payload["strategy_type"]
        comparable_strategy_type = (
            strategy_type.strip() if isinstance(strategy_type, str) else strategy_type
        )
        comparable_source_strategy_type = (
            source.strategy_type.strip()
            if isinstance(source.strategy_type, str)
            else source.strategy_type
        )
        if comparable_strategy_type != comparable_source_strategy_type:
            payload["params"] = dict(override_params or {})
        elif override_params is not None:
            params = dict(payload["params"])
            params.update(override_params)
            payload["params"] = params
        payload["name"] = target_name
        payload["enabled"] = False
        normalized = normalize_config_payload(payload, expected_name=target_name)
        async with runtime.lifecycle_lock(normalized.name):
            if is_active(normalized.name):
                raise HTTPException(status_code=409, detail="Strategy is active")
            try:
                now = current_timestamp_ms()
                saved = config_repository.create_strategy_config(
                    record_from_normalized(normalized, created_at=now, updated_at=now)
                )
            except IntegrityError:
                raise HTTPException(
                    status_code=409,
                    detail="Strategy config already exists",
                ) from None
            except Exception:
                logger.exception("Failed to clone strategy config %s to %s", name, normalized.name)
                raise HTTPException(
                    status_code=500,
                    detail="Failed to clone strategy config",
                ) from None
            runtime.strategy_status.setdefault(saved.name, "stopped")
            event_timestamp = saved.updated_at
        await broadcast_config_event(
            "strategy_config_created",
            saved,
            timestamp=event_timestamp,
        )
        return serialize_strategy_config(saved)

    @router.delete("/configs/{name}", status_code=204)
    async def delete_strategy_config(name: str) -> Response:
        async with runtime.lifecycle_lock(name):
            if get_persisted_strategy_config(name) is None:
                raise HTTPException(status_code=404, detail="Strategy config not found")
            ensure_mutation_allowed(name, deleting=True)
            try:
                deleted = config_repository.delete_strategy_config(name)
            except Exception:
                logger.exception("Failed to delete strategy config %s", name)
                raise HTTPException(
                    status_code=500,
                    detail="Failed to delete strategy config",
                ) from None
            if not deleted:
                raise HTTPException(status_code=404, detail="Strategy config not found")
            event_timestamp = current_timestamp_ms()
            runtime.strategy_status.pop(name, None)
            runtime.strategy_errors.pop(name, None)
        await broadcast_config_event(
            "strategy_config_deleted",
            name,
            timestamp=event_timestamp,
        )
        return Response(status_code=204)

    @router.post("/{name}/start")
    async def start_strategy(name: str) -> dict[str, str]:
        try:
            order_router_mode = current_order_router_mode()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if not strategy_exists(name):
            raise HTTPException(status_code=404, detail="Strategy not found")
        async with runtime.lifecycle_lock(name):
            if not strategy_exists(name):
                raise HTTPException(status_code=404, detail="Strategy not found")
            persisted_config = get_persisted_strategy_config(name)
            if persisted_config is not None and not persisted_config.enabled:
                raise HTTPException(status_code=409, detail="Strategy config is disabled")
            startup_finalized = False
            if name not in runtime.engines and name not in runtime.starting_engines:
                engine: BotEngine | None = None
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
                                on_risk_event=lambda payload: (
                                    persist_broadcast_and_notify_risk_event(repository, payload)
                                ),
                                kill_switch_checker=lambda: kill_switch_engaged(repository),
                                order_router_mode=order_router_mode,
                            )
                        )
                    transferred = False
                    startup_finalized = False
                    startup_callback_error: str | None = None

                    async def handle_current_engine_error(
                        error_name: str,
                        error: Exception,
                    ) -> None:
                        nonlocal startup_callback_error
                        if transferred and runtime.engines.get(error_name) is engine:
                            startup_callback_error = str(error)
                        await handle_strategy_error(error_name, error, engine)

                    try:
                        try:
                            async with runtime.market_data_lifecycle_lock:
                                engine = BotEngine(
                                    strategies=[strategy],
                                    market_data_service=get_market_data_service(),
                                    on_strategy_error=handle_current_engine_error,
                                    before_live_strategy_bar=(
                                        lambda strategy, bar: mark_to_market_before_bar(
                                            mark_to_market,
                                            strategy,
                                            bar,
                                        )
                                    ),
                                    stop_market_data_on_stop=False,
                                )
                                runtime.starting_engines[name] = engine
                            await engine.start()
                            async with runtime.market_data_lifecycle_lock:
                                if runtime.starting_engines.get(name) is not engine:
                                    raise HTTPException(
                                        status_code=409,
                                        detail="Strategy startup was superseded",
                                    )
                                if name in runtime.strategy_errors:
                                    raise HTTPException(
                                        status_code=400,
                                        detail=runtime.strategy_errors[name],
                                    )
                                runtime.starting_engines.pop(name, None)
                                runtime.engines[name] = engine
                                transferred = True
                        except HTTPException:
                            raise
                        except Exception as exc:
                            await handle_strategy_error(name, exc, engine)
                            raise

                        await asyncio.sleep(0)
                        if startup_callback_error is not None:
                            raise HTTPException(
                                status_code=400,
                                detail=startup_callback_error,
                            )
                        if transferred and runtime.engines.get(name) is not engine:
                            if name in runtime.engines or name in runtime.starting_engines:
                                raise HTTPException(
                                    status_code=409,
                                    detail="Strategy startup was superseded",
                                )
                            if startup_callback_error is not None:
                                raise HTTPException(
                                    status_code=400,
                                    detail=startup_callback_error,
                                )
                            raise HTTPException(
                                status_code=409,
                                detail="Strategy startup was superseded",
                            )
                        runtime.strategy_status[name] = "running"
                        runtime.strategy_errors.pop(name, None)
                        await broadcast_status(name)
                        startup_finalized = True
                    finally:
                        if engine is not None and not startup_finalized:

                            async def cleanup_startup_engine() -> None:
                                owner_removed = False
                                try:
                                    async with runtime.market_data_lifecycle_lock:
                                        if runtime.starting_engines.get(name) is engine:
                                            runtime.starting_engines.pop(name, None)
                                            owner_removed = True
                                        if runtime.engines.get(name) is engine:
                                            runtime.engines.pop(name, None)
                                            owner_removed = True
                                        if (
                                            owner_removed
                                            and name not in runtime.engines
                                            and name not in runtime.starting_engines
                                        ):
                                            runtime.strategy_status[name] = "stopped"
                                except BaseException:
                                    logger.exception(
                                        "Failed to clear cancelled startup owner %s",
                                        name,
                                    )
                                try:
                                    await engine.stop()
                                except BaseException:
                                    logger.exception(
                                        "Failed to stop cancelled startup engine %s",
                                        name,
                                    )
                                try:
                                    await release_market_data_service_if_idle()
                                except BaseException:
                                    logger.exception(
                                        "Failed to release market data after cancelled startup %s",
                                        name,
                                    )

                            cleanup_task = asyncio.create_task(cleanup_startup_engine())
                            while not cleanup_task.done():
                                try:
                                    await asyncio.shield(cleanup_task)
                                except asyncio.CancelledError:
                                    pass
                            try:
                                cleanup_task.result()
                            except BaseException:
                                logger.exception(
                                    "Failed to clean up cancelled startup engine %s",
                                    name,
                                )
                except HTTPException:
                    raise
                except Exception as exc:
                    await handle_strategy_error(name, exc, engine)
                    raise HTTPException(status_code=400, detail=str(exc)) from None
            if not startup_finalized:
                runtime.strategy_status[name] = "running"
                runtime.strategy_errors.pop(name, None)
                await broadcast_status(name)
        return {"status": "started", "strategy": name}

    @router.post("/{name}/stop")
    async def stop_strategy(name: str) -> dict[str, str]:
        if not strategy_exists(name):
            raise HTTPException(status_code=404, detail="Strategy not found")
        async with runtime.lifecycle_lock(name):
            engine = runtime.engines.get(name)
            if engine is not None:
                await engine.stop()
                if runtime.engines.get(name) is engine:
                    runtime.engines.pop(name, None)
                await release_market_data_service_if_idle()
            runtime.strategy_status[name] = "stopped"
            await broadcast_status(name)
        return {"status": "stopped", "strategy": name}

    return router


router = create_router()
