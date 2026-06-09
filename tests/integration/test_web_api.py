import asyncio
import subprocess
import sys

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.core.types import Bar, Order, OrderSide, OrderStatus, OrderType
from src.data.models import AccountRecord, KlineCache, PositionRecord, StrategyConfigRecord
from src.strategy.base import BaseStrategy
from src.strategy.registry import StrategyRegistry
from src.web import app as web_app
from src.web.api import backtest as backtest_api
from src.web.api import market as market_api
from src.web.api import strategies as strategy_api
from src.web.api import trading as trading_api
from src.web.app import app as exported_app
from src.web.app import create_app


@pytest.fixture
def app():
    return create_app()


def test_module_exports_fastapi_app():
    assert isinstance(exported_app, FastAPI)


@pytest.mark.asyncio
async def test_cors_preflight_does_not_allow_credentials(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in resp.headers


@pytest.mark.asyncio
async def test_health(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_get_strategies(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/strategies")

    assert resp.status_code == 200
    assert {"name": "ma_cross", "status": "stopped"} in resp.json()


@pytest.mark.asyncio
async def test_get_strategies_includes_persisted_configs(monkeypatch):
    class FakeRepository:
        def get_strategy_configs(self):
            return [
                StrategyConfigRecord(
                    name="ma_cross_btc",
                    strategy_type="ma_cross",
                    symbol="BTC-USDT",
                    timeframe="1h",
                    params={"fast_window": 5, "slow_window": 20},
                    enabled=True,
                    created_at=1700000000000,
                    updated_at=1700000000000,
                )
            ]

    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/strategies")

    assert resp.status_code == 200
    assert {"name": "ma_cross_btc", "status": "stopped"} in resp.json()


@pytest.mark.asyncio
async def test_strategy_config_api_saves_and_lists_configs(monkeypatch):
    class FakeRepository:
        configs = []

        def get_strategy_configs(self):
            return self.configs

        def upsert_strategy_config(self, config):
            self.configs = [item for item in self.configs if item.name != config.name]
            self.configs.append(config)
            return config

    FakeRepository.configs = []
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        save_resp = await client.post(
            "/api/strategies/configs",
            json={
                "name": "ma_cross_btc",
                "strategy_type": "ma_cross",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "params": {"fast_window": 5, "slow_window": 20},
                "enabled": True,
            },
        )
        list_resp = await client.get("/api/strategies/configs")

    assert save_resp.status_code == 200
    assert save_resp.json() == {
        "id": None,
        "name": "ma_cross_btc",
        "strategy_type": "ma_cross",
        "symbol": "BTC-USDT",
        "timeframe": "1h",
        "params": {"fast_window": 5, "slow_window": 20},
        "enabled": True,
        "created_at": 1700000000000,
        "updated_at": 1700000000000,
    }
    assert list_resp.status_code == 200
    assert [item["name"] for item in list_resp.json()] == ["ma_cross_btc"]


def test_create_order_manager_uses_falsy_repository_instance(monkeypatch):
    class FalsyRepository:
        def __bool__(self):
            return False

    repository = FalsyRepository()
    monkeypatch.setattr(
        strategy_api,
        "paper_backtest_config",
        lambda: strategy_api.BacktestConfig(),
    )

    manager = strategy_api.create_order_manager(repository=repository)

    assert manager.repository is repository


def test_create_risk_manager_without_settings_uses_max_position_only(monkeypatch):
    def missing_settings(path):
        raise FileNotFoundError

    monkeypatch.setattr(strategy_api, "load_config", missing_settings)

    manager = strategy_api.create_risk_manager()

    assert manager.enforce_daily_loss is False
    assert manager.enforce_drawdown is False


@pytest.mark.asyncio
async def test_strategy_config_api_rejects_non_ma_cross_strategy_type(monkeypatch):
    class FakeRepository:
        def get_strategy_configs(self):
            return []

    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/strategies/configs",
            json={
                "name": "grid_btc",
                "strategy_type": "grid",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "params": {},
                "enabled": True,
            },
        )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Only ma_cross strategy configs are supported"


@pytest.mark.asyncio
async def test_start_persisted_strategy_config_uses_strategy_type_and_params(monkeypatch):
    class ConfigurableStrategy(BaseStrategy):
        created = []

        def __init__(
            self,
            symbol="BTC-USDT",
            fast_window=10,
            slow_window=30,
            amount=0.1,
        ):
            super().__init__()
            self.symbol = symbol
            self.fast_window = fast_window
            self.slow_window = slow_window
            self.amount = amount
            self.created.append(self)

        async def on_bar(self, bar):
            pass

    config = StrategyConfigRecord(
        name="ma_cross_btc",
        strategy_type="ma_cross",
        symbol="BTC-USDT-SWAP",
        timeframe="15m",
        params={"fast_window": 5, "slow_window": 20, "amount": 0.2},
        enabled=True,
        created_at=1700000000000,
        updated_at=1700000000000,
    )

    class FakeRepository:
        def get_strategy_configs(self):
            return [config]

        def get_strategy_config(self, name):
            if name == config.name:
                return config
            return None

    registry = StrategyRegistry()
    registry.register("ma_cross", ConfigurableStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_resp = await client.post("/api/strategies/ma_cross_btc/start")
        strategies_resp = await client.get("/api/strategies")

    assert start_resp.status_code == 200
    assert start_resp.json() == {"status": "started", "strategy": "ma_cross_btc"}
    assert {"name": "ma_cross_btc", "status": "running"} in strategies_resp.json()
    assert ConfigurableStrategy.created[0].name == "ma_cross_btc"
    assert ConfigurableStrategy.created[0].symbol == "BTC-USDT-SWAP"
    assert ConfigurableStrategy.created[0].fast_window == 5
    assert ConfigurableStrategy.created[0].slow_window == 20
    assert ConfigurableStrategy.created[0].amount == 0.2


@pytest.mark.asyncio
async def test_start_persisted_strategy_config_can_override_builtin_name(monkeypatch):
    class ConfigurableStrategy(BaseStrategy):
        created = []

        def __init__(self, symbol="BTC-USDT", fast_window=10, slow_window=30):
            super().__init__()
            self.symbol = symbol
            self.fast_window = fast_window
            self.slow_window = slow_window
            self.created.append(self)

        async def on_bar(self, bar):
            pass

    config = StrategyConfigRecord(
        name="ma_cross",
        strategy_type="ma_cross",
        symbol="ETH-USDT",
        timeframe="1h",
        params={"fast_window": 5, "slow_window": 20},
        enabled=True,
        created_at=1700000000000,
        updated_at=1700000000000,
    )

    class FakeRepository:
        def get_strategy_config(self, name):
            if name == config.name:
                return config
            return None

        def get_strategy_configs(self):
            return [config]

    registry = StrategyRegistry()
    registry.register("ma_cross", ConfigurableStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/ma_cross/start")

    assert resp.status_code == 200
    assert ConfigurableStrategy.created[0].name == "ma_cross"
    assert ConfigurableStrategy.created[0].symbol == "ETH-USDT"
    assert ConfigurableStrategy.created[0].fast_window == 5
    assert ConfigurableStrategy.created[0].slow_window == 20


@pytest.mark.asyncio
async def test_start_persisted_strategy_config_broadcasts_startup_error(monkeypatch):
    config = StrategyConfigRecord(
        name="bad_ma_cross",
        strategy_type="ma_cross",
        symbol="BTC-USDT",
        timeframe="1m",
        params={"fast_window": 30, "slow_window": 10},
        enabled=True,
        created_at=1700000000000,
        updated_at=1700000000000,
    )

    class FakeRepository:
        def get_strategy_config(self, name):
            if name == config.name:
                return config
            return None

        def get_strategy_configs(self):
            return [config]

    messages = []

    async def broadcast(message):
        messages.append(message)

    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000, raising=False)
    monkeypatch.setattr(web_app.ws_manager, "broadcast", broadcast)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_resp = await client.post("/api/strategies/bad_ma_cross/start")
        strategies_resp = await client.get("/api/strategies")

    assert start_resp.status_code == 400
    assert {"name": "bad_ma_cross", "status": "stopped"} in strategies_resp.json()
    assert messages == [
        {
            "type": "strategy_status",
            "strategy": "bad_ma_cross",
            "status": "stopped",
            "timestamp": 1700000000000,
        },
        {
            "type": "strategy_error",
            "strategy": "bad_ma_cross",
            "error": "fast_window must be less than or equal to slow_window",
            "timestamp": 1700000000000,
        },
    ]


@pytest.mark.asyncio
async def test_persisted_strategy_loop_fills_market_order_from_latest_bar(monkeypatch):
    class FakeMarketDataService:
        def __init__(self):
            self.callbacks = []
            self.latest_bar = None
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            self.callbacks.append(callback)

        def get_recent_bars(self, symbol, timeframe, count=1):
            if self.latest_bar is None:
                return []
            return [self.latest_bar]

        async def start(self):
            self._running = True

        async def stop(self):
            self._running = False

    class MarketBuyingStrategy(BaseStrategy):
        def __init__(self, symbol="BTC-USDT"):
            super().__init__()
            self.symbol = symbol

        async def on_bar(self, bar):
            await self.buy(self.symbol, 0.1)

    config = StrategyConfigRecord(
        name="market_buyer_btc",
        strategy_type="market_buyer",
        symbol="BTC-USDT",
        timeframe="1m",
        params={},
        enabled=True,
        created_at=1700000000000,
        updated_at=1700000000000,
    )

    class FakeRepository:
        orders = []
        trades = []
        positions = {}
        accounts = {}
        ledger = []

        def get_strategy_config(self, name):
            if name == config.name:
                return config
            return None

        def get_strategy_configs(self):
            return [config]

        def save_order(self, order):
            self.orders.append(order)
            return order

        def save_trade(self, trade):
            self.trades.append(trade)
            return trade

        def get_orders(self):
            return self.orders

        def get_account(self, strategy=None):
            if strategy is None:
                return next(iter(self.accounts.values()), None)
            return self.accounts.get(strategy)

        def upsert_account(self, account):
            self.accounts[account.strategy] = account
            return account

        def get_position(self, strategy, symbol):
            return self.positions.get((strategy, symbol))

        def upsert_position(self, position):
            self.positions[(position.strategy, position.symbol)] = position
            return position

        def delete_position(self, strategy, symbol):
            self.positions.pop((strategy, symbol), None)

        def get_open_positions(self, strategy=None):
            return [
                position
                for position in self.positions.values()
                if position.amount != 0 and (strategy is None or position.strategy == strategy)
            ]

        def save_cash_ledger(self, entry):
            self.ledger.append(entry)
            return entry

    FakeRepository.orders = []
    FakeRepository.trades = []
    FakeRepository.positions = {}
    FakeRepository.accounts = {}
    FakeRepository.ledger = []
    market_data = FakeMarketDataService()
    registry = StrategyRegistry()
    registry.register("market_buyer", MarketBuyingStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        lambda: market_data,
        raising=False,
    )
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000, raising=False)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_resp = await client.post("/api/strategies/market_buyer_btc/start")
        market_data.latest_bar = Bar(1, 50000.0, 50000.0, 50000.0, 50000.0, 1.0)
        await market_data.callbacks[0](market_data.latest_bar)

    assert start_resp.status_code == 200
    assert FakeRepository.orders[0].status == "filled"
    assert FakeRepository.orders[0].fill_price == 50000.0
    assert FakeRepository.trades[0].price == 50000.0


@pytest.mark.asyncio
async def test_runtime_risk_rejection_broadcasts_risk_event_before_trading_updates(monkeypatch):
    class FakeMarketDataService:
        def __init__(self):
            self.callbacks = []
            self.latest_bar = None
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            self.callbacks.append(callback)

        def get_recent_bars(self, symbol, timeframe, count=1):
            if self.latest_bar is None:
                return []
            return [self.latest_bar]

        async def start(self):
            self._running = True

        async def stop(self):
            self._running = False

    class OversizedBuyingStrategy(BaseStrategy):
        def __init__(self, symbol="BTC-USDT"):
            super().__init__()
            self.symbol = symbol

        async def on_bar(self, bar):
            await self.buy(self.symbol, 10.0)

    config = StrategyConfigRecord(
        name="oversized_buyer_btc",
        strategy_type="oversized_buyer",
        symbol="BTC-USDT",
        timeframe="1m",
        params={},
        enabled=True,
        created_at=1700000000000,
        updated_at=1700000000000,
    )

    class FakeRepository:
        orders = []

        def get_strategy_config(self, name):
            if name == config.name:
                return config
            return None

        def get_strategy_configs(self):
            return [config]

        def save_order(self, order):
            self.orders.append(order)
            return order

        def get_orders(self):
            return self.orders

        def get_account(self, strategy=None):
            return None

        def get_position(self, strategy, symbol):
            return None

        def get_open_positions(self, strategy=None):
            return []

    FakeRepository.orders = []
    market_data = FakeMarketDataService()
    messages = []

    async def broadcast(message):
        messages.append(message)

    registry = StrategyRegistry()
    registry.register("oversized_buyer", OversizedBuyingStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        lambda: market_data,
        raising=False,
    )
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000, raising=False)
    monkeypatch.setattr(web_app.ws_manager, "broadcast", broadcast)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_resp = await client.post("/api/strategies/oversized_buyer_btc/start")
        messages.clear()
        market_data.latest_bar = Bar(1700003600000, 50000.0, 50000.0, 50000.0, 50000.0, 1.0)
        await market_data.callbacks[0](market_data.latest_bar)

    assert start_resp.status_code == 200
    assert [message["type"] for message in messages] == ["risk_event", "orders", "positions"]
    assert messages[0] == {
        "type": "risk_event",
        "strategy": "oversized_buyer_btc",
        "order_id": FakeRepository.orders[0].order_id,
        "symbol": "BTC-USDT",
        "side": "buy",
        "order_type": "market",
        "amount": 10.0,
        "price": 50000.0,
        "requested_price": None,
        "order_value": 500000.0,
        "reason": "Order exceeds maximum position size",
        "reason_code": "max_position_exceeded",
        "timestamp": 1700000000000,
    }
    assert messages[1] == {
        "type": "orders",
        "orders": [order.model_dump() for order in FakeRepository.orders],
    }
    assert messages[2] == {"type": "positions", "positions": []}


@pytest.mark.asyncio
async def test_runtime_bar_marks_open_position_and_broadcasts_positions_then_account(monkeypatch):
    class FakeMarketDataService:
        def __init__(self):
            self.callbacks = []
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            self.callbacks.append(callback)

        def get_recent_bars(self, symbol, timeframe, count=1):
            return []

        async def start(self):
            self._running = True

        async def stop(self):
            self._running = False

    class PassiveStrategy(BaseStrategy):
        def __init__(self, symbol="BTC-USDT"):
            super().__init__()
            self.symbol = symbol
            self.bars = []

        async def on_bar(self, bar):
            self.bars.append(bar)

    config = StrategyConfigRecord(
        name="passive_btc",
        strategy_type="passive",
        symbol="BTC-USDT",
        timeframe="1m",
        params={},
        enabled=True,
        created_at=1700000000000,
        updated_at=1700000000000,
    )

    class FakeRepository:
        positions = {
            ("passive_btc", "BTC-USDT"): PositionRecord(
                strategy="passive_btc",
                symbol="BTC-USDT",
                side="long",
                amount=0.1,
                entry_price=50000.0,
                leverage=1,
                timestamp=1700000000000,
            )
        }
        accounts = {
            "passive_btc": AccountRecord(
                strategy="passive_btc",
                initial_equity=100000.0,
                cash_balance=95000.0,
                equity=100000.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                daily_pnl=0.0,
                fees_paid=0.0,
                updated_at=1700000000000,
            )
        }

        def get_strategy_config(self, name):
            if name == config.name:
                return config
            return None

        def get_strategy_configs(self):
            return [config]

        def get_position(self, strategy, symbol):
            return self.positions.get((strategy, symbol))

        def upsert_position(self, position):
            self.positions[(position.strategy, position.symbol)] = position
            return position

        def get_open_positions(self, strategy=None):
            return [
                position
                for position in self.positions.values()
                if position.amount != 0 and (strategy is None or position.strategy == strategy)
            ]

        def get_account(self, strategy=None):
            if strategy is None:
                return next(iter(self.accounts.values()), None)
            return self.accounts.get(strategy)

        def upsert_account(self, account):
            self.accounts[account.strategy] = account
            return account

        def get_orders(self):
            return []

    FakeRepository.positions = {
        ("passive_btc", "BTC-USDT"): PositionRecord(
            strategy="passive_btc",
            symbol="BTC-USDT",
            side="long",
            amount=0.1,
            entry_price=50000.0,
            leverage=1,
            timestamp=1700000000000,
        )
    }
    FakeRepository.accounts = {
        "passive_btc": AccountRecord(
            strategy="passive_btc",
            initial_equity=100000.0,
            cash_balance=95000.0,
            equity=100000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            daily_pnl=0.0,
            fees_paid=0.0,
            updated_at=1700000000000,
        )
    }
    market_data = FakeMarketDataService()
    messages = []

    async def broadcast(message):
        messages.append(message)

    registry = StrategyRegistry()
    registry.register("passive", PassiveStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        lambda: market_data,
        raising=False,
    )
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000, raising=False)
    monkeypatch.setattr(web_app.ws_manager, "broadcast", broadcast)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_resp = await client.post("/api/strategies/passive_btc/start")
        messages.clear()
        await market_data.callbacks[0](Bar(1700003600000, 51000.0, 51000.0, 51000.0, 51000.0, 1.0))

    position = FakeRepository.positions[("passive_btc", "BTC-USDT")]
    account = FakeRepository.accounts["passive_btc"]
    assert start_resp.status_code == 200
    assert [message["type"] for message in messages] == ["positions", "account"]
    assert position.mark_price == 51000.0
    assert position.unrealized_pnl == pytest.approx(100.0)
    assert position.timestamp == 1700003600000
    assert account.unrealized_pnl == pytest.approx(100.0)
    assert account.equity == pytest.approx(100100.0)
    assert messages == [
        {
            "type": "positions",
            "positions": [position.model_dump()],
        },
        {
            "type": "account",
            "account": {
                "cash_balance": 95000.0,
                "equity": 100100.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 100.0,
                "daily_pnl": 0.0,
                "fees_paid": 0.0,
            },
        },
    ]


@pytest.mark.asyncio
async def test_runtime_bar_without_open_position_does_not_create_account_or_broadcast(monkeypatch):
    class FakeMarketDataService:
        def __init__(self):
            self.callbacks = []
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            self.callbacks.append(callback)

        def get_recent_bars(self, symbol, timeframe, count=1):
            return []

        async def start(self):
            self._running = True

        async def stop(self):
            self._running = False

    class PassiveStrategy(BaseStrategy):
        def __init__(self, symbol="BTC-USDT"):
            super().__init__()
            self.symbol = symbol

        async def on_bar(self, bar):
            pass

    config = StrategyConfigRecord(
        name="flat_passive_btc",
        strategy_type="flat_passive",
        symbol="BTC-USDT",
        timeframe="1m",
        params={},
        enabled=True,
        created_at=1700000000000,
        updated_at=1700000000000,
    )

    class FakeRepository:
        accounts = {}

        def get_strategy_config(self, name):
            if name == config.name:
                return config
            return None

        def get_strategy_configs(self):
            return [config]

        def get_position(self, strategy, symbol):
            return None

        def get_open_positions(self, strategy=None):
            return []

        def get_account(self, strategy=None):
            if strategy is None:
                return next(iter(self.accounts.values()), None)
            return self.accounts.get(strategy)

        def upsert_account(self, account):
            self.accounts[account.strategy] = account
            return account

        def get_orders(self):
            return []

    FakeRepository.accounts = {}
    market_data = FakeMarketDataService()
    messages = []

    async def broadcast(message):
        messages.append(message)

    registry = StrategyRegistry()
    registry.register("flat_passive", PassiveStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        lambda: market_data,
        raising=False,
    )
    monkeypatch.setattr(web_app.ws_manager, "broadcast", broadcast)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_resp = await client.post("/api/strategies/flat_passive_btc/start")
        messages.clear()
        await market_data.callbacks[0](Bar(1700003600000, 51000.0, 51000.0, 51000.0, 51000.0, 1.0))

    assert start_resp.status_code == 200
    assert messages == []
    assert FakeRepository.accounts == {}


@pytest.mark.asyncio
async def test_start_persisted_strategy_stays_stopped_when_initial_bar_fails(monkeypatch):
    class ImmediateMarketDataService:
        def __init__(self):
            self.callbacks = []
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            self.callbacks.append(callback)

        async def start(self):
            self._running = True
            await self.callbacks[0](Bar(1, 1, 1, 1, 1, 1))

        async def stop(self):
            self._running = False

    class BrokenStrategy(BaseStrategy):
        def __init__(self, symbol="BTC-USDT"):
            super().__init__()
            self.symbol = symbol

        async def on_bar(self, bar):
            await asyncio.sleep(0)
            raise RuntimeError("boom")

    config = StrategyConfigRecord(
        name="broken_on_start_btc",
        strategy_type="broken_on_start",
        symbol="BTC-USDT",
        timeframe="1m",
        params={},
        enabled=True,
        created_at=1700000000000,
        updated_at=1700000000000,
    )

    class FakeRepository:
        def get_strategy_config(self, name):
            if name == config.name:
                return config
            return None

        def get_strategy_configs(self):
            return [config]

        def get_open_positions(self, strategy=None):
            return []

        def get_orders(self):
            return []

        def get_account(self, strategy=None):
            return None

    market_data = ImmediateMarketDataService()
    messages = []

    async def broadcast(message):
        messages.append(message)

    registry = StrategyRegistry()
    registry.register("broken_on_start", BrokenStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        lambda: market_data,
        raising=False,
    )
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000)
    monkeypatch.setattr(web_app.ws_manager, "broadcast", broadcast)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_resp = await client.post("/api/strategies/broken_on_start_btc/start")
        strategies_resp = await client.get("/api/strategies")

    assert start_resp.status_code == 400
    assert start_resp.json()["detail"] == "boom"
    assert {"name": "broken_on_start_btc", "status": "stopped"} in strategies_resp.json()
    assert messages == [
        {
            "type": "strategy_status",
            "strategy": "broken_on_start_btc",
            "status": "stopped",
            "timestamp": 1700000000000,
        },
        {
            "type": "strategy_error",
            "strategy": "broken_on_start_btc",
            "error": "boom",
            "timestamp": 1700000000000,
        },
    ]


@pytest.mark.asyncio
async def test_persisted_strategy_loop_broadcasts_error_and_stops_strategy(monkeypatch):
    class FakeMarketDataService:
        def __init__(self):
            self.callbacks = []
            self.subscription = None
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            self.subscription = (symbol, timeframe)
            self.callbacks.append(callback)

        async def start(self):
            self._running = True

        async def stop(self):
            self._running = False

    class BrokenStrategy(BaseStrategy):
        def __init__(self, symbol="BTC-USDT"):
            super().__init__()
            self.symbol = symbol

        async def on_bar(self, bar):
            raise RuntimeError("boom")

    config = StrategyConfigRecord(
        name="broken_btc",
        strategy_type="broken",
        symbol="BTC-USDT",
        timeframe="1m",
        params={},
        enabled=True,
        created_at=1700000000000,
        updated_at=1700000000000,
    )

    class FakeRepository:
        def get_strategy_config(self, name):
            if name == config.name:
                return config
            return None

        def get_strategy_configs(self):
            return [config]

        def get_open_positions(self, strategy=None):
            return []

        def get_orders(self):
            return []

        def get_account(self, strategy=None):
            return None

    market_data = FakeMarketDataService()
    messages = []

    async def broadcast(message):
        messages.append(message)

    registry = StrategyRegistry()
    registry.register("broken", BrokenStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        lambda: market_data,
        raising=False,
    )
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000, raising=False)
    monkeypatch.setattr(web_app.ws_manager, "broadcast", broadcast)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_resp = await client.post("/api/strategies/broken_btc/start")
        await market_data.callbacks[0](Bar(1, 1, 1, 1, 1, 1))
        strategies_resp = await client.get("/api/strategies")

    assert start_resp.status_code == 200
    assert market_data.subscription == ("BTC-USDT", "1m")
    assert {"name": "broken_btc", "status": "stopped"} in strategies_resp.json()
    assert messages[-2:] == [
        {
            "type": "strategy_status",
            "strategy": "broken_btc",
            "status": "stopped",
            "timestamp": 1700000000000,
        },
        {
            "type": "strategy_error",
            "strategy": "broken_btc",
            "error": "boom",
            "timestamp": 1700000000000,
        },
    ]


@pytest.mark.asyncio
async def test_start_and_stop_strategy(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_resp = await client.post("/api/strategies/ma_cross/start")
        running_resp = await client.get("/api/strategies")
        stop_resp = await client.post("/api/strategies/ma_cross/stop")
        stopped_resp = await client.get("/api/strategies")

    assert start_resp.status_code == 200
    assert start_resp.json() == {"status": "started", "strategy": "ma_cross"}
    assert {"name": "ma_cross", "status": "running"} in running_resp.json()
    assert stop_resp.status_code == 200
    assert stop_resp.json() == {"status": "stopped", "strategy": "ma_cross"}
    assert {"name": "ma_cross", "status": "stopped"} in stopped_resp.json()


@pytest.mark.asyncio
async def test_strategy_status_is_isolated_per_app_instance():
    first_app = create_app()
    second_app = create_app()

    first_transport = ASGITransport(app=first_app)
    second_transport = ASGITransport(app=second_app)
    async with (
        AsyncClient(transport=first_transport, base_url="http://first") as first_client,
        AsyncClient(transport=second_transport, base_url="http://second") as second_client,
    ):
        await first_client.post("/api/strategies/ma_cross/start")
        resp = await second_client.get("/api/strategies")

    assert {"name": "ma_cross", "status": "stopped"} in resp.json()


@pytest.mark.asyncio
async def test_start_and_stop_strategy_runs_bot_engine_lifecycle(monkeypatch):
    class LifecycleStrategy(BaseStrategy):
        events: list[str] = []

        async def on_init(self):
            self.events.append("started")

        async def on_bar(self, bar):
            pass

        async def on_shutdown(self):
            self.events.append("stopped")

    registry = StrategyRegistry()
    registry.register("lifecycle", LifecycleStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/strategies/lifecycle/start")
        await client.post("/api/strategies/lifecycle/stop")

    assert LifecycleStrategy.events == ["started", "stopped"]


@pytest.mark.asyncio
async def test_start_strategy_wires_repository_backed_order_manager(monkeypatch):
    class FakeRepository:
        orders = []
        trades = []
        positions = {}
        accounts = {}
        ledger = []

        def save_order(self, order):
            self.orders.append(order)
            return order

        def save_trade(self, trade):
            self.trades.append(trade)
            return trade

        def get_orders(self):
            return self.orders

        def get_account(self, strategy=None):
            if strategy is None:
                return next(iter(self.accounts.values()), None)
            return self.accounts.get(strategy)

        def upsert_account(self, account):
            self.accounts[account.strategy] = account
            return account

        def get_position(self, strategy, symbol):
            return self.positions.get((strategy, symbol))

        def upsert_position(self, position):
            self.positions[(position.strategy, position.symbol)] = position
            return position

        def delete_position(self, strategy, symbol):
            self.positions.pop((strategy, symbol), None)

        def get_open_positions(self, strategy=None):
            return [
                position
                for position in self.positions.values()
                if position.amount != 0 and (strategy is None or position.strategy == strategy)
            ]

        def save_cash_ledger(self, entry):
            self.ledger.append(entry)
            return entry

    class BuyingStrategy(BaseStrategy):
        name = "buyer"

        async def on_init(self):
            await self.buy("BTC-USDT", 0.1, price=50000.0)

        async def on_bar(self, bar):
            pass

    messages = []

    async def broadcast(message):
        messages.append(message)

    registry = StrategyRegistry()
    registry.register("buyer", BuyingStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000, raising=False)
    monkeypatch.setattr(web_app.ws_manager, "broadcast", broadcast)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/buyer/start")

    assert resp.status_code == 200
    assert [order.model_dump() for order in FakeRepository.orders] == [
        {
            "id": None,
            "order_id": FakeRepository.orders[0].order_id,
            "strategy": "buyer",
            "symbol": "BTC-USDT",
            "side": "buy",
            "type": "limit",
            "amount": 0.1,
            "price": 50000.0,
            "status": "filled",
            "fill_price": 50000.0,
            "timestamp": 1700000000000,
        }
    ]
    assert [trade.model_dump() for trade in FakeRepository.trades] == [
        {
            "id": None,
            "strategy": "buyer",
            "symbol": "BTC-USDT",
            "side": "buy",
            "amount": 0.1,
            "price": 50000.0,
            "fee": 2.5,
            "timestamp": 1700000000000,
        }
    ]
    assert [position.model_dump() for position in FakeRepository.positions.values()] == [
        {
            "id": None,
            "strategy": "buyer",
            "symbol": "BTC-USDT",
            "side": "long",
            "amount": 0.1,
            "entry_price": 50000.0,
            "leverage": 1,
            "timestamp": 1700000000000,
            "mark_price": None,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
        }
    ]
    assert FakeRepository.accounts["buyer"].cash_balance == 94997.5
    assert FakeRepository.accounts["buyer"].equity == 99997.5
    assert messages[:3] == [
        {
            "type": "orders",
            "orders": [order.model_dump() for order in FakeRepository.orders],
        },
        {
            "type": "positions",
            "positions": [position.model_dump() for position in FakeRepository.positions.values()],
        },
        {
            "type": "account",
            "account": {
                "cash_balance": 94997.5,
                "equity": 99997.5,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "daily_pnl": 0.0,
                "fees_paid": 2.5,
            },
        },
    ]
    assert messages[3] == {
        "type": "strategy_status",
        "strategy": "buyer",
        "status": "running",
        "timestamp": 1700000000000,
    }


@pytest.mark.asyncio
async def test_local_paper_order_handler_rejects_market_order_without_price():
    handler = strategy_api.LocalPaperOrderHandler()
    order = Order(
        id="order-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.1,
    )

    result = await handler.submit(order)

    assert result.status == OrderStatus.REJECTED
    assert result.fill_price is None
    assert result.fill_time is None


@pytest.mark.asyncio
async def test_local_paper_order_handler_fills_limit_order_at_explicit_price():
    handler = strategy_api.LocalPaperOrderHandler()
    order = Order(
        id="order-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=0.1,
        price=50000.0,
    )

    result = await handler.submit(order)

    assert result.status == OrderStatus.FILLED
    assert result.fill_price == 50000.0
    assert result.fill_time is not None


@pytest.mark.asyncio
async def test_local_paper_order_handler_fills_market_order_from_price_provider():
    handler = strategy_api.LocalPaperOrderHandler(latest_price=lambda symbol: 51000.0)
    order = Order(
        id="order-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.1,
    )

    result = await handler.submit(order)

    assert result.status == OrderStatus.FILLED
    assert result.fill_price == 51000.0


@pytest.mark.asyncio
async def test_start_unknown_strategy_returns_404(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/unknown/start")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_run_backtest_uses_cached_bars_and_persists_summary(monkeypatch):
    class FakeRepository:
        results = []
        klines = [
            KlineCache(
                symbol="BTC-USDT",
                timeframe="1h",
                timestamp=1700002800000,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=10.0,
            ),
            KlineCache(
                symbol="BTC-USDT",
                timeframe="1h",
                timestamp=1700006400000,
                open=110.0,
                high=111.0,
                low=109.0,
                close=110.0,
                volume=12.0,
            ),
        ]

        def get_klines(self, symbol, timeframe, start, end):
            return [
                kline
                for kline in self.klines
                if kline.symbol == symbol
                and kline.timeframe == timeframe
                and start <= kline.timestamp <= end
            ]

        def save_backtest_result(self, result):
            self.results.append(result)
            return result

        def get_backtest_results(self, limit=50):
            return sorted(self.results, key=lambda result: result.created_at, reverse=True)[:limit]

    class BuyOnceStrategy(BaseStrategy):
        name = "buy_once"

        def __init__(self):
            super().__init__()
            self._bought = False

        async def on_bar(self, bar):
            if self._bought:
                return None
            self._bought = True
            return await self.buy("BTC-USDT", 1.0)

    FakeRepository.results = []
    registry = StrategyRegistry()
    registry.register("buy_once", BuyOnceStrategy)
    monkeypatch.setattr(backtest_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(backtest_api, "create_strategy_registry", lambda: registry, raising=False)
    monkeypatch.setattr(backtest_api, "current_timestamp_ms", lambda: 1700007200000, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/backtest/run",
            json={
                "strategy": "buy_once",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "start_time": 1700002800000,
                "end_time": 1700006400000,
                "initial_capital": 100000,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_trades"] == 1
    assert data["total_return"] < 0
    assert isinstance(data["sharpe_ratio"], float)
    assert len(FakeRepository.results) == 1
    saved = FakeRepository.results[0]
    assert saved.strategy == "buy_once"
    assert saved.symbol == "BTC-USDT"
    assert saved.timeframe == "1h"
    assert saved.initial_capital == 100000
    assert saved.total_trades == 1
    assert saved.created_at == 1700007200000


@pytest.mark.asyncio
async def test_run_backtest_fetches_missing_historical_bars_before_running(monkeypatch):
    class FakeRepository:
        results = []
        klines = []

        def get_klines(self, symbol, timeframe, start, end):
            return [
                kline
                for kline in self.klines
                if kline.symbol == symbol
                and kline.timeframe == timeframe
                and start <= kline.timestamp <= end
            ]

        def save_kline(self, kline):
            self.klines.append(kline)
            return kline

        def save_backtest_result(self, result):
            self.results.append(result)
            return result

    class BuyOnceStrategy(BaseStrategy):
        name = "fetch_buy_once"

        def __init__(self):
            super().__init__()
            self._bought = False

        async def on_bar(self, bar):
            if self._bought:
                return None
            self._bought = True
            return await self.buy("BTC-USDT", 1.0)

    class FakeAdapter:
        calls = []
        closed = False

        async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
            self.calls.append(
                {"symbol": symbol, "timeframe": timeframe, "limit": limit, "since": since}
            )
            return [
                Bar(timestamp=1700002800000, open=100, high=101, low=99, close=100, volume=1),
                Bar(timestamp=1700006400000, open=110, high=111, low=109, close=110, volume=1),
            ]

        async def close(self):
            self.closed = True

    FakeRepository.results = []
    FakeRepository.klines = []
    FakeAdapter.calls = []
    FakeAdapter.closed = False
    registry = StrategyRegistry()
    registry.register("fetch_buy_once", BuyOnceStrategy)
    monkeypatch.setattr(backtest_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        backtest_api, "OKXSpotAdapter", lambda **kwargs: FakeAdapter(), raising=False
    )
    monkeypatch.setattr(backtest_api, "create_strategy_registry", lambda: registry, raising=False)
    monkeypatch.setattr(backtest_api, "current_timestamp_ms", lambda: 1700007200000, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/backtest/run",
            json={
                "strategy": "fetch_buy_once",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "start_time": 1700002800000,
                "end_time": 1700006400000,
                "initial_capital": 100000,
            },
        )

    assert resp.status_code == 200
    assert resp.json()["total_trades"] == 1
    assert FakeAdapter.calls == [
        {"symbol": "BTC-USDT", "timeframe": "1h", "limit": 2, "since": 1700002800000}
    ]
    assert [kline.timestamp for kline in FakeRepository.klines] == [1700002800000, 1700006400000]
    assert FakeRepository.results[0].strategy == "fetch_buy_once"


@pytest.mark.asyncio
async def test_run_backtest_returns_502_when_historical_fetch_fails(monkeypatch):
    class FakeRepository:
        def get_klines(self, symbol, timeframe, start, end):
            return []

    class EmptyStrategy(BaseStrategy):
        name = "fetch_failure"

        async def on_bar(self, bar):
            return None

    class FailingAdapter:
        async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
            raise ValueError("malformed provider response")

        async def close(self):
            pass

    registry = StrategyRegistry()
    registry.register("fetch_failure", EmptyStrategy)
    monkeypatch.setattr(backtest_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        backtest_api, "OKXSpotAdapter", lambda **kwargs: FailingAdapter(), raising=False
    )
    monkeypatch.setattr(backtest_api, "create_strategy_registry", lambda: registry, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/backtest/run",
            json={
                "strategy": "fetch_failure",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "start_time": 1700002800000,
                "end_time": 1700006400000,
                "initial_capital": 100000,
            },
        )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "failed to fetch historical market data"


@pytest.mark.asyncio
async def test_run_backtest_rejects_unsupported_historical_timeframe(monkeypatch):
    class FakeRepository:
        def get_klines(self, symbol, timeframe, start, end):
            return []

    class EmptyStrategy(BaseStrategy):
        name = "unsupported_timeframe"

        async def on_bar(self, bar):
            return None

    registry = StrategyRegistry()
    registry.register("unsupported_timeframe", EmptyStrategy)
    monkeypatch.setattr(backtest_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(backtest_api, "create_strategy_registry", lambda: registry, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/backtest/run",
            json={
                "strategy": "unsupported_timeframe",
                "symbol": "BTC-USDT",
                "timeframe": "2h",
                "start_time": 1700002800000,
                "end_time": 1700006400000,
                "initial_capital": 100000,
            },
        )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "unsupported timeframe for historical backtest data"


@pytest.mark.asyncio
async def test_run_backtest_rejects_insufficient_cached_bars(monkeypatch):
    class FakeRepository:
        results = []

        def get_klines(self, symbol, timeframe, start, end):
            return []

        def save_backtest_result(self, result):
            self.results.append(result)
            return result

    class EmptyStrategy(BaseStrategy):
        name = "empty"

        async def on_bar(self, bar):
            return None

    class EmptyAdapter:
        async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
            return []

        async def close(self):
            pass

    registry = StrategyRegistry()
    registry.register("empty", EmptyStrategy)
    monkeypatch.setattr(backtest_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        backtest_api, "OKXSpotAdapter", lambda **kwargs: EmptyAdapter(), raising=False
    )
    monkeypatch.setattr(backtest_api, "create_strategy_registry", lambda: registry, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/backtest/run",
            json={
                "strategy": "empty",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "start_time": 1700002800000,
                "end_time": 1700006400000,
                "initial_capital": 100000,
            },
        )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "insufficient historical data for requested backtest range"
    assert FakeRepository.results == []


@pytest.mark.asyncio
async def test_run_backtest_rejects_unknown_strategy(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/backtest/run",
            json={
                "strategy": "unknown",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "start_time": 1700000000000,
                "end_time": 1700100000000,
                "initial_capital": 100000,
            },
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_backtest_results_returns_persisted_summaries(monkeypatch):
    class FakeResult:
        def __init__(self, result_id, symbol, created_at):
            self.id = result_id
            self.strategy = "ma_cross"
            self.symbol = symbol
            self.timeframe = "4h"
            self.start_time = 1700000000000
            self.end_time = 1700100000000
            self.initial_capital = 50000.0
            self.total_return = 0.02
            self.sharpe_ratio = 1.1
            self.max_drawdown = 0.03
            self.win_rate = 0.5
            self.total_trades = 2
            self.created_at = created_at

        def model_dump(self):
            return self.__dict__

    class FakeRepository:
        def get_backtest_results(self, limit=50):
            return [FakeResult("bt-new", "ETH-USDT", 2), FakeResult("bt-old", "BTC-USDT", 1)]

    monkeypatch.setattr(backtest_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/backtest/results")

    assert resp.status_code == 200
    data = resp.json()
    assert [result["id"] for result in data] == ["bt-new", "bt-old"]
    assert data[0]["symbol"] == "ETH-USDT"
    assert data[0]["total_trades"] == 2


def test_websocket_accepts_connection_and_sends_snapshot(monkeypatch):
    class FakeRepository:
        def get_account(self, strategy=None):
            return {
                "cash_balance": 100000.0,
                "equity": 100100.0,
                "realized_pnl": 100.0,
                "unrealized_pnl": 0.0,
                "daily_pnl": 100.0,
                "fees_paid": 1.5,
            }

        def get_open_positions(self, strategy=None):
            return []

        def get_orders(self):
            return []

    monkeypatch.setattr(web_app, "Repository", FakeRepository, raising=False)

    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws") as websocket:
            snapshot = websocket.receive_json()

    assert snapshot == {
        "type": "snapshot",
        "data": {
            "account": {
                "cash_balance": 100000.0,
                "equity": 100100.0,
                "realized_pnl": 100.0,
                "unrealized_pnl": 0.0,
                "daily_pnl": 100.0,
                "fees_paid": 1.5,
            },
            "positions": [],
            "orders": [],
            "strategies": [{"name": "ma_cross", "status": "stopped"}],
        },
    }


def test_websocket_snapshot_reflects_running_strategy_status():
    app = create_app()

    with TestClient(app) as client:
        resp = client.post("/api/strategies/ma_cross/start")
        with client.websocket_connect("/ws") as websocket:
            snapshot = websocket.receive_json()

    assert resp.status_code == 200
    assert {"name": "ma_cross", "status": "running"} in snapshot["data"]["strategies"]


def test_websocket_snapshot_includes_persisted_strategy_configs(monkeypatch):
    config = StrategyConfigRecord(
        name="ma_cross_btc",
        strategy_type="ma_cross",
        symbol="BTC-USDT",
        timeframe="1h",
        params={"fast_window": 5, "slow_window": 20},
        enabled=True,
        created_at=1700000000000,
        updated_at=1700000000000,
    )

    class FakeRepository:
        def get_account(self, strategy=None):
            return None

        def get_open_positions(self, strategy=None):
            return []

        def get_orders(self):
            return []

        def get_strategy_configs(self):
            return [config]

    monkeypatch.setattr(web_app, "Repository", FakeRepository, raising=False)

    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws") as websocket:
            snapshot = websocket.receive_json()

    assert {"name": "ma_cross_btc", "status": "stopped"} in snapshot["data"]["strategies"]


@pytest.mark.asyncio
async def test_strategy_start_broadcasts_status_event(monkeypatch):
    messages = []

    async def broadcast(message):
        messages.append(message)

    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000)
    monkeypatch.setattr(web_app.ws_manager, "broadcast", broadcast)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/ma_cross/start")

    assert resp.status_code == 200
    assert messages == [
        {
            "type": "strategy_status",
            "strategy": "ma_cross",
            "status": "running",
            "timestamp": 1700000000000,
        }
    ]


@pytest.mark.asyncio
async def test_get_and_update_settings(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        initial_resp = await client.get("/api/settings")
        update_resp = await client.put(
            "/api/settings",
            json={
                "mode": "paper",
                "exchange": {
                    "api_key": "okx-api-key",
                    "secret": "okx-secret-value",
                    "passphrase": "okx-passphrase",
                },
                "backtest": {
                    "initial_capital": 250000,
                    "fee_rate": 0.0007,
                    "slippage": 0.0015,
                    "data_cache_dir": "./data/backtests",
                },
                "risk": {
                    "max_daily_loss_pct": 0.03,
                    "max_drawdown_pct": 0.12,
                    "max_total_position_pct": 0.65,
                },
                "notify": {
                    "telegram_bot_token": "telegram-token",
                    "telegram_chat_id": "123456",
                },
            },
        )
        saved_resp = await client.get("/api/settings")

    assert initial_resp.status_code == 200
    assert initial_resp.json()["mode"] == "backtest"
    assert update_resp.status_code == 200
    saved = saved_resp.json()
    assert saved["mode"] == "paper"
    assert saved["exchange"] == {
        "api_key": "ok*******ey",
        "api_key_set": True,
        "secret": "ok************ue",
        "secret_set": True,
        "passphrase": "ok**********se",
        "passphrase_set": True,
    }
    assert saved["backtest"]["initial_capital"] == 250000
    assert saved["risk"]["max_daily_loss_pct"] == 0.03
    assert saved["notify"] == {
        "telegram_bot_token": "te**********en",
        "telegram_bot_token_set": True,
        "telegram_chat_id": "123456",
    }


def test_settings_module_import_does_not_load_settings_file(tmp_path):
    settings_path = tmp_path / "settings.local.yaml"
    settings_path.write_text("mode: [not-a-string]\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-c", "import src.web.api.settings; print('imported')"],
        check=False,
        capture_output=True,
        env={"OKX_BOT_SETTINGS_PATH": str(settings_path)},
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "imported"


@pytest.mark.asyncio
async def test_settings_defaults_load_project_config_environment(monkeypatch):
    monkeypatch.setenv("OKX_API_KEY", "env-api-key")
    monkeypatch.setenv("OKX_SECRET", "env-secret-value")
    monkeypatch.setenv("OKX_PASSPHRASE", "env-passphrase")
    monkeypatch.setenv("TG_BOT_TOKEN", "env-telegram-token")
    monkeypatch.setenv("TG_CHAT_ID", "987654")

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/settings")

    assert resp.status_code == 200
    settings = resp.json()
    assert settings["exchange"] == {
        "api_key": "en*******ey",
        "api_key_set": True,
        "secret": "en************ue",
        "secret_set": True,
        "passphrase": "en**********se",
        "passphrase_set": True,
    }
    assert settings["notify"] == {
        "telegram_bot_token": "en**************en",
        "telegram_bot_token_set": True,
        "telegram_chat_id": "987654",
    }


@pytest.mark.asyncio
async def test_settings_round_trip_keeps_existing_secrets(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.local.yaml"
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(settings_path))
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            "/api/settings",
            json={
                "mode": "paper",
                "exchange": {
                    "api_key": "real-api-key",
                    "secret": "real-secret-value",
                    "passphrase": "real-passphrase",
                },
                "backtest": {
                    "initial_capital": 250000,
                    "fee_rate": 0.0007,
                    "slippage": 0.0015,
                    "data_cache_dir": "./data/backtests",
                },
                "risk": {
                    "max_daily_loss_pct": 0.03,
                    "max_drawdown_pct": 0.12,
                    "max_total_position_pct": 0.65,
                },
                "notify": {
                    "telegram_bot_token": "real-telegram-token",
                    "telegram_chat_id": "123456",
                },
            },
        )
        settings = (await client.get("/api/settings")).json()
        settings["web"]["port"] = 9001
        await client.put("/api/settings", json=settings)
        saved = (await client.get("/api/settings")).json()

    persisted = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    assert persisted["exchange"] == {
        "api_key": "real-api-key",
        "secret": "real-secret-value",
        "passphrase": "real-passphrase",
    }
    assert persisted["notify"] == {
        "telegram_bot_token": "real-telegram-token",
        "telegram_chat_id": "123456",
    }
    assert saved["web"]["port"] == 9001


@pytest.mark.asyncio
async def test_settings_persist_across_app_instances(tmp_path, monkeypatch):
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(tmp_path / "settings.local.yaml"))

    first_app = create_app()
    first_transport = ASGITransport(app=first_app)
    async with AsyncClient(transport=first_transport, base_url="http://first") as client:
        await client.put(
            "/api/settings",
            json={
                "mode": "live",
                "exchange": {
                    "api_key": "persisted-api-key",
                    "secret": "persisted-secret",
                    "passphrase": "persisted-passphrase",
                },
                "backtest": {
                    "initial_capital": 300000,
                    "fee_rate": 0.0008,
                    "slippage": 0.0012,
                    "data_cache_dir": "./data/persisted",
                },
                "risk": {
                    "max_daily_loss_pct": 0.02,
                    "max_drawdown_pct": 0.1,
                    "max_total_position_pct": 0.5,
                },
                "notify": {
                    "telegram_bot_token": "persisted-token",
                    "telegram_chat_id": "654321",
                },
                "web": {
                    "host": "127.0.0.1",
                    "port": 9000,
                },
            },
        )

    second_app = create_app()
    second_transport = ASGITransport(app=second_app)
    async with AsyncClient(transport=second_transport, base_url="http://second") as client:
        resp = await client.get("/api/settings")

    assert resp.status_code == 200
    saved = resp.json()
    assert saved["mode"] == "live"
    assert saved["exchange"] == {
        "api_key": "pe*************ey",
        "api_key_set": True,
        "secret": "pe************et",
        "secret_set": True,
        "passphrase": "pe****************se",
        "passphrase_set": True,
    }
    assert saved["backtest"]["initial_capital"] == 300000
    assert saved["risk"]["max_total_position_pct"] == 0.5
    assert saved["notify"] == {
        "telegram_bot_token": "pe***********en",
        "telegram_bot_token_set": True,
        "telegram_chat_id": "654321",
    }
    assert saved["web"] == {"host": "127.0.0.1", "port": 9000}


@pytest.mark.asyncio
async def test_get_positions_returns_persisted_position_records(monkeypatch, app):
    class Position:
        def model_dump(self):
            return {
                "id": 1,
                "strategy": "ma_cross",
                "symbol": "BTC-USDT",
                "side": "long",
                "amount": 0.2,
                "entry_price": 65000.0,
                "leverage": 3,
                "timestamp": 1700000000000,
            }

    class FakeRepository:
        def get_positions(self, strategy=None):
            assert strategy is None
            return [Position()]

    monkeypatch.setattr(trading_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trading/positions")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "id": 1,
            "strategy": "ma_cross",
            "symbol": "BTC-USDT",
            "side": "long",
            "amount": 0.2,
            "entry_price": 65000.0,
            "leverage": 3,
            "timestamp": 1700000000000,
        }
    ]


@pytest.mark.asyncio
async def test_get_orders_returns_persisted_order_records(monkeypatch, app):
    class Order:
        def model_dump(self):
            return {
                "id": 1,
                "order_id": "order-1",
                "strategy": "ma_cross",
                "symbol": "BTC-USDT",
                "side": "buy",
                "type": "limit",
                "amount": 0.2,
                "price": 65000.0,
                "status": "open",
                "fill_price": 0.0,
                "timestamp": 1700000000000,
            }

    class FakeRepository:
        def get_orders(self):
            return [Order()]

    monkeypatch.setattr(trading_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trading/orders")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "id": 1,
            "order_id": "order-1",
            "strategy": "ma_cross",
            "symbol": "BTC-USDT",
            "side": "buy",
            "type": "limit",
            "amount": 0.2,
            "price": 65000.0,
            "status": "open",
            "fill_price": 0.0,
            "timestamp": 1700000000000,
        }
    ]


@pytest.mark.asyncio
async def test_get_account_returns_paper_account_state(monkeypatch, app):
    class Account:
        cash_balance = 95000.0
        equity = 100500.0
        realized_pnl = 500.0
        unrealized_pnl = 0.0
        daily_pnl = 500.0
        fees_paid = 2.5

    class FakeRepository:
        def get_account(self, strategy=None):
            assert strategy is None
            return Account()

    monkeypatch.setattr(trading_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trading/account")

    assert resp.status_code == 200
    assert resp.json() == {
        "cash_balance": 95000.0,
        "equity": 100500.0,
        "realized_pnl": 500.0,
        "unrealized_pnl": 0.0,
        "daily_pnl": 500.0,
        "fees_paid": 2.5,
    }
    assert "available_balance" not in resp.json()


@pytest.mark.asyncio
async def test_get_positions_forwards_strategy_filter(monkeypatch, app):
    calls = []

    class FakeRepository:
        def get_open_positions(self, strategy=None):
            calls.append(strategy)
            return []

    monkeypatch.setattr(trading_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trading/positions?strategy=ma_cross")

    assert resp.status_code == 200
    assert resp.json() == []
    assert calls == ["ma_cross"]


@pytest.mark.asyncio
async def test_get_positions_filters_flat_rows_without_open_position_method(monkeypatch, app):
    class Position:
        def __init__(self, symbol, amount):
            self.symbol = symbol
            self.amount = amount
            self.timestamp = 1700000000000

        def model_dump(self):
            return {
                "symbol": self.symbol,
                "amount": self.amount,
                "timestamp": self.timestamp,
            }

    class FakeRepository:
        def get_positions(self, strategy=None):
            return [Position("BTC-USDT", 0.1), Position("ETH-USDT", 0.0)]

    monkeypatch.setattr(trading_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trading/positions")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "symbol": "BTC-USDT",
            "amount": 0.1,
            "timestamp": 1700000000000,
        }
    ]


@pytest.mark.asyncio
async def test_get_trading_state(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        positions_resp = await client.get("/api/trading/positions")
        orders_resp = await client.get("/api/trading/orders")
        account_resp = await client.get("/api/trading/account")

    assert positions_resp.status_code == 200
    assert isinstance(positions_resp.json(), list)
    assert orders_resp.status_code == 200
    assert isinstance(orders_resp.json(), list)
    assert account_resp.status_code == 200
    assert "equity" in account_resp.json()
    assert "daily_pnl" in account_resp.json()


@pytest.mark.asyncio
async def test_get_trades_returns_persisted_trade_records(monkeypatch, app):
    class Trade:
        def __init__(self, **values):
            self.values = values
            self.timestamp = values["timestamp"]

        def model_dump(self):
            return self.values

    class FakeRepository:
        def get_trades(self, strategy=None):
            assert strategy is None
            return [
                Trade(
                    id=1,
                    strategy="ma_cross",
                    symbol="BTC-USDT",
                    side="buy",
                    amount=0.1,
                    price=68000.0,
                    fee=1.2,
                    timestamp=1700000000000,
                )
            ]

    monkeypatch.setattr(trading_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trading/trades")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "id": 1,
            "strategy": "ma_cross",
            "symbol": "BTC-USDT",
            "side": "buy",
            "amount": 0.1,
            "price": 68000.0,
            "fee": 1.2,
            "timestamp": 1700000000000,
        }
    ]


@pytest.mark.asyncio
async def test_get_trades_forwards_strategy_filter(monkeypatch, app):
    calls = []

    class FakeRepository:
        def get_trades(self, strategy=None):
            calls.append(strategy)
            return []

    monkeypatch.setattr(trading_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trading/trades?strategy=ma_cross")

    assert resp.status_code == 200
    assert resp.json() == []
    assert calls == ["ma_cross"]


@pytest.mark.asyncio
async def test_get_trades_returns_newest_first(monkeypatch, app):
    class Trade:
        def __init__(self, timestamp):
            self.timestamp = timestamp

        def model_dump(self):
            return {
                "id": self.timestamp,
                "strategy": "ma_cross",
                "symbol": "BTC-USDT",
                "side": "buy",
                "amount": 0.1,
                "price": 68000.0,
                "fee": 1.2,
                "timestamp": self.timestamp,
            }

    class FakeRepository:
        def get_trades(self, strategy=None):
            return [Trade(1700000000000), Trade(1700100000000)]

    monkeypatch.setattr(trading_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trading/trades")

    assert resp.status_code == 200
    assert [trade["timestamp"] for trade in resp.json()] == [1700100000000, 1700000000000]


@pytest.mark.asyncio
async def test_market_klines_returns_rows_from_real_public_adapter(monkeypatch, app):
    events = []

    class FakeOKXSpotAdapter:
        def __init__(self, api_key: str, secret: str, passphrase: str) -> None:
            events.append(("init", api_key, secret, passphrase))

        async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100):
            events.append(("fetch_ohlcv", symbol, timeframe, limit))
            return [
                Bar(timestamp=1700000000000, open=1.1, high=1.3, low=1.0, close=1.2, volume=10.5),
                Bar(timestamp=1700000060000, open=1.2, high=1.4, low=1.1, close=1.3, volume=11.5),
            ]

        async def close(self) -> None:
            events.append(("close",))

    monkeypatch.setattr(market_api, "OKXSpotAdapter", FakeOKXSpotAdapter, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/market/klines?symbol=ETH-USDT&timeframe=1m&limit=2")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "symbol": "ETH-USDT",
            "timeframe": "1m",
            "timestamp": 1700000000000,
            "open": 1.1,
            "high": 1.3,
            "low": 1.0,
            "close": 1.2,
            "volume": 10.5,
        },
        {
            "symbol": "ETH-USDT",
            "timeframe": "1m",
            "timestamp": 1700000060000,
            "open": 1.2,
            "high": 1.4,
            "low": 1.1,
            "close": 1.3,
            "volume": 11.5,
        },
    ]
    assert events == [
        ("init", "", "", ""),
        ("fetch_ohlcv", "ETH-USDT", "1m", 2),
        ("close",),
    ]


@pytest.mark.asyncio
async def test_market_tickers_returns_rows_from_real_public_adapter(monkeypatch, app):
    events = []

    class FakeOKXSpotAdapter:
        def __init__(self, api_key: str, secret: str, passphrase: str) -> None:
            events.append(("init", api_key, secret, passphrase))

        async def fetch_tickers(self, symbols: list[str]):
            events.append(("fetch_tickers", symbols))
            return [
                {
                    "symbol": "BTC-USDT",
                    "last": 68000.0,
                    "bidPx": 67999.5,
                    "askPx": 68000.5,
                    "vol24h": 123.45,
                },
                {
                    "symbol": "ETH-USDT",
                    "last": 3800.0,
                    "bidPx": 3799.5,
                    "askPx": 3800.5,
                    "vol24h": 456.78,
                },
            ]

        async def close(self) -> None:
            events.append(("close",))

    monkeypatch.setattr(market_api, "OKXSpotAdapter", FakeOKXSpotAdapter, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/market/tickers")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "symbol": "BTC-USDT",
            "last": 68000.0,
            "bidPx": 67999.5,
            "askPx": 68000.5,
            "vol24h": 123.45,
        },
        {
            "symbol": "ETH-USDT",
            "last": 3800.0,
            "bidPx": 3799.5,
            "askPx": 3800.5,
            "vol24h": 456.78,
        },
    ]
    assert events == [
        ("init", "", "", ""),
        ("fetch_tickers", ["BTC-USDT", "ETH-USDT", "OKB-USDT", "SOL-USDT"]),
        ("close",),
    ]


@pytest.mark.asyncio
async def test_get_market_data(monkeypatch, app):
    class FakeOKXSpotAdapter:
        def __init__(self, api_key: str, secret: str, passphrase: str) -> None:
            pass

        async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100):
            return [
                Bar(
                    timestamp=1700000000000 + index * 3_600_000,
                    open=1,
                    high=2,
                    low=0.5,
                    close=1.5,
                    volume=10,
                )
                for index in range(limit)
            ]

        async def fetch_tickers(self, symbols: list[str]):
            return [
                {"symbol": symbol, "last": 1.0, "bidPx": 0.9, "askPx": 1.1, "vol24h": 10.0}
                for symbol in symbols
            ]

        async def close(self) -> None:
            pass

    monkeypatch.setattr(market_api, "OKXSpotAdapter", FakeOKXSpotAdapter)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        klines_resp = await client.get("/api/market/klines?symbol=BTC-USDT&timeframe=1h&limit=100")
        tickers_resp = await client.get("/api/market/tickers")

    assert klines_resp.status_code == 200
    klines = klines_resp.json()
    assert len(klines) == 100
    assert klines[0]["symbol"] == "BTC-USDT"
    assert klines[0]["timeframe"] == "1h"
    assert {"timestamp", "open", "high", "low", "close", "volume"} <= klines[0].keys()
    assert tickers_resp.status_code == 200
    tickers = tickers_resp.json()
    assert {"symbol": "BTC-USDT"}.items() <= tickers[0].items()
