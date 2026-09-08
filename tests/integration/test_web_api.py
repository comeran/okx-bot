import asyncio
import subprocess
import sys
from dataclasses import asdict
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.analytics.strategy_performance import build_strategy_performance
from src.core.config import AppConfig, ExchangeConfig, RiskConfig
from src.core.engine import BotEngine
from src.core.types import Bar, Order, OrderSide, OrderStatus, OrderType
from src.data.models import (
    AccountRecord,
    BacktestResultRecord,
    BacktestTradeRecord,
    KlineCache,
    OrderRecord,
    PositionRecord,
    StrategyConfigRecord,
    TradeRecord,
)
from src.data.repository import Repository as SqliteRepository
from src.exchange.live_sync import LiveStateSyncResult
from src.order.manager import UnifiedOrderManager
from src.strategy.base import BaseStrategy
from src.strategy.builtin.bollinger_mean_reversion import BollingerMeanReversionStrategy
from src.strategy.builtin.donchian_breakout import DonchianBreakoutStrategy
from src.strategy.builtin.ma_cross import MACrossStrategy
from src.strategy.builtin.rsi_mean_reversion import RSIMeanReversionStrategy
from src.strategy.registry import StrategyRegistry
from src.web import app as web_app
from src.web.api import backtest as backtest_api
from src.web.api import market as market_api
from src.web.api import ops as ops_api
from src.web.api import strategies as strategy_api
from src.web.api import trading as trading_api
from src.web.app import app as exported_app
from src.web.app import create_app


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_path = tmp_path / "bot.db"

    def repository_factory(db_path=db_path):
        return SqliteRepository(db_path=str(db_path))

    for module in (
        web_app,
        strategy_api,
        ops_api,
        trading_api,
        market_api,
        backtest_api,
    ):
        monkeypatch.setattr(module, "Repository", repository_factory, raising=False)

    return create_app()


def test_module_exports_fastapi_app():
    assert isinstance(exported_app, FastAPI)


class FakeKillSwitchRepository:
    state = {"engaged": False, "reason": "", "updated_at": 0}

    def get_kill_switch(self):
        class State:
            engaged = FakeKillSwitchRepository.state["engaged"]
            reason = FakeKillSwitchRepository.state["reason"]
            updated_at = FakeKillSwitchRepository.state["updated_at"]

        return State()

    def set_kill_switch(self, engaged, reason, updated_at):
        self.state = {"engaged": engaged, "reason": reason, "updated_at": updated_at}
        FakeKillSwitchRepository.state = self.state
        return self.get_kill_switch()

    def get_strategy_configs(self):
        return []


def test_kill_switch_api_defaults_to_disengaged(monkeypatch):
    FakeKillSwitchRepository.state = {"engaged": False, "reason": "", "updated_at": 0}
    monkeypatch.setattr(ops_api, "Repository", FakeKillSwitchRepository, raising=False)

    client = TestClient(create_app())
    response = client.get("/api/ops/kill-switch")

    assert response.status_code == 200
    assert response.json()["engaged"] is False
    assert response.json()["reason"] == ""


def test_kill_switch_api_persists_engaged_state(monkeypatch):
    FakeKillSwitchRepository.state = {"engaged": False, "reason": "", "updated_at": 0}
    monkeypatch.setattr(ops_api, "Repository", FakeKillSwitchRepository, raising=False)
    monkeypatch.setattr(ops_api, "current_timestamp_ms", lambda: 1700000000000, raising=False)

    client = TestClient(create_app())
    response = client.put(
        "/api/ops/kill-switch",
        json={"engaged": True, "reason": "operator emergency stop"},
    )

    assert response.status_code == 200
    assert response.json()["engaged"] is True
    assert response.json()["reason"] == "operator emergency stop"
    assert response.json()["updated_at"] == 1700000000000
    assert client.get("/api/ops/kill-switch").json()["engaged"] is True


def private_sync_settings(demo=True, market_type="spot"):
    return SimpleNamespace(
        exchange=SimpleNamespace(
            api_key="api-key",
            secret="secret",
            passphrase="passphrase",
            market_type=market_type,
            demo=demo,
        ),
        notify=SimpleNamespace(telegram_bot_token="", telegram_chat_id=""),
    )


def test_private_sync_endpoint_rejects_live_mode(monkeypatch):
    monkeypatch.setattr(
        ops_api.settings_api,
        "_load_settings",
        lambda: private_sync_settings(demo=False),
        raising=False,
    )
    monkeypatch.setattr(
        ops_api,
        "_create_adapter",
        lambda settings: (_ for _ in ()).throw(AssertionError("adapter should not be created")),
    )
    app = FastAPI()
    app.include_router(ops_api.create_router(), prefix="/api/ops")

    response = TestClient(app).post("/api/ops/sync/private", json={})

    assert response.status_code == 403
    assert response.json()["detail"] == "Private sync is only available in OKX demo mode"


def test_private_sync_endpoint_returns_counts_in_demo_mode(monkeypatch):
    class FakeAdapter:
        closed = False

        async def close(self):
            self.closed = True

    class FakeRepository:
        pass

    class FakeResult:
        def as_response(self):
            return {
                "account_upserted": 1,
                "orders_upserted": 2,
                "trades_upserted": 3,
                "risk_events_saved": 1,
                "kill_switch_engaged": True,
                "divergences": [{"type": "risk_event"}],
            }

    adapter = FakeAdapter()
    captured = {}

    async def fake_sync(repository, sync_adapter, **kwargs):
        captured["repository"] = repository
        captured["adapter"] = sync_adapter
        captured["kwargs"] = kwargs
        return FakeResult()

    monkeypatch.setattr(
        ops_api.settings_api,
        "_load_settings",
        lambda: private_sync_settings(demo=True),
        raising=False,
    )
    monkeypatch.setattr(ops_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(ops_api, "_create_adapter", lambda settings: adapter)
    monkeypatch.setattr(ops_api, "sync_private_state", fake_sync)
    app = FastAPI()
    app.include_router(ops_api.create_router(), prefix="/api/ops")

    response = TestClient(app).post(
        "/api/ops/sync/private",
        json={"symbols": ["BTC-USDT"], "since": 1700000000000},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "account_upserted": 1,
        "orders_upserted": 2,
        "trades_upserted": 3,
        "risk_events_saved": 1,
        "kill_switch_engaged": True,
        "divergences": [{"type": "risk_event"}],
    }
    assert isinstance(captured["repository"], FakeRepository)
    assert captured["adapter"] is adapter
    assert captured["kwargs"]["symbols"] == ["BTC-USDT"]
    assert captured["kwargs"]["since"] == 1700000000000
    assert adapter.closed is True


@pytest.mark.asyncio
async def test_strategy_start_blocked_when_kill_switch_engaged(monkeypatch):
    FakeKillSwitchRepository.state = {
        "engaged": True,
        "reason": "operator emergency stop",
        "updated_at": 1700000000000,
    }
    monkeypatch.setattr(ops_api, "Repository", FakeKillSwitchRepository, raising=False)
    monkeypatch.setattr(strategy_api, "Repository", FakeKillSwitchRepository, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/strategies/ma_cross/start")

    assert response.status_code == 423
    assert response.json()["detail"] == "Kill switch engaged"


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
    assert resp.json() == [{"name": "ma_cross", "status": "stopped"}]


@pytest.mark.asyncio
async def test_registered_builtin_types_are_not_implicit_runtime_strategies(app):
    registry = strategy_api.create_strategy_registry()

    assert [definition.name for definition in registry.list_definitions()] == [
        "ma_cross",
        "rsi_mean_reversion",
        "bollinger_mean_reversion",
        "donchian_breakout",
    ]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/strategies")
        start_resp = await client.post("/api/strategies/rsi_mean_reversion/start")

    assert resp.status_code == 200
    assert resp.json() == [{"name": "ma_cross", "status": "stopped"}]
    assert start_resp.status_code == 404


@pytest.mark.asyncio
async def test_legacy_ma_cross_runtime_defaults_to_1m_subscription_and_latest_price(monkeypatch):
    class FakeRepository:
        def get_strategy_configs(self):
            return []

    class FakeMarketDataService:
        def __init__(self):
            self.subscriptions = []
            self.bars = {("BTC-USDT", "1m"): [Bar(1, 10, 11, 9, 123.45, 1)]}
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            self.subscriptions.append((symbol, timeframe, callback))

        def get_recent_bars(self, symbol, timeframe, count=1):
            return self.bars.get((symbol, timeframe), [])[-count:]

        async def start(self):
            self._running = True
            for symbol, timeframe, callback in list(self.subscriptions):
                for bar in self.bars.get((symbol, timeframe), []):
                    await callback(bar)

    class CapturingOrderManager:
        async def submit(self, *args, **kwargs):
            return None

    captured = {}
    market_data = FakeMarketDataService()

    def capture_order_manager(**kwargs):
        captured["latest_price"] = kwargs["latest_price"]
        return CapturingOrderManager()

    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(strategy_api, "create_market_data_service", lambda: market_data)
    monkeypatch.setattr(strategy_api, "create_order_manager", capture_order_manager)
    monkeypatch.setattr(strategy_api, "load_runtime_settings", lambda: AppConfig(mode="paper"))

    app = FastAPI()
    app.include_router(
        strategy_api.create_router(runtime=strategy_api.StrategyRuntimeState()),
        prefix="/api/strategies",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/ma_cross/start")

    assert resp.status_code == 200
    assert [(symbol, timeframe) for symbol, timeframe, _ in market_data.subscriptions] == [
        ("BTC-USDT", "1m")
    ]
    assert captured["latest_price"]("BTC-USDT") == 123.45


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

        def create_strategy_config(self, config):
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

    assert save_resp.status_code == 201
    assert save_resp.json() == {
        "id": None,
        "name": "ma_cross_btc",
        "strategy_type": "ma_cross",
        "symbol": "BTC-USDT",
        "timeframe": "1h",
        "params": {"fast_window": 5, "slow_window": 20, "amount": 0.1},
        "enabled": True,
        "created_at": 1700000000000,
        "updated_at": 1700000000000,
    }
    assert list_resp.status_code == 200
    assert [item["name"] for item in list_resp.json()] == ["ma_cross_btc"]


@pytest.mark.asyncio
async def test_strategy_config_crud_clone_validate_and_yaml_use_normalized_configs(monkeypatch):
    class FakeRepository:
        configs = {}

        def get_strategy_configs(self):
            return list(self.configs.values())

        def get_strategy_config(self, name):
            return self.configs.get(name)

        def create_strategy_config(self, config):
            if config.name in self.configs:
                raise strategy_api.IntegrityError("duplicate", None, None)
            config.id = len(self.configs) + 1
            self.configs[config.name] = config
            return config

        def update_strategy_config(self, name, config):
            existing = self.configs.get(name)
            if existing is None:
                return None
            existing.strategy_type = config.strategy_type
            existing.symbol = config.symbol
            existing.timeframe = config.timeframe
            existing.params = config.params
            existing.enabled = config.enabled
            existing.updated_at = config.updated_at
            return existing

        def delete_strategy_config(self, name):
            return self.configs.pop(name, None) is not None

        def get_open_positions(self, strategy=None):
            return []

    FakeRepository.configs = {}
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000)
    messages = []

    async def broadcast(message):
        messages.append(message)

    app = FastAPI()
    app.include_router(strategy_api.create_router(broadcast=broadcast), prefix="/api/strategies")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        types_resp = await client.get("/api/strategies/types")
        validate_resp = await client.post(
            "/api/strategies/configs/validate",
            json={
                "name": "ma_cross_btc",
                "strategy_type": "ma_cross",
                "symbol": " BTC-USDT ",
                "timeframe": "1h",
                "params": {"fast_window": 5, "slow_window": 20},
            },
        )
        yaml_resp = await client.post(
            "/api/strategies/configs/validate-yaml",
            content=(
                "name: ma_cross_btc\n"
                "strategy_type: ma_cross\n"
                "symbol: BTC-USDT\n"
                "timeframe: 1h\n"
                "params:\n"
                "  fast_window: 5\n"
                "  slow_window: 20\n"
            ),
        )
        create_resp = await client.post(
            "/api/strategies/configs",
            json=validate_resp.json()["config"],
        )
        duplicate_resp = await client.post(
            "/api/strategies/configs",
            json=validate_resp.json()["config"],
        )
        get_resp = await client.get("/api/strategies/configs/ma_cross_btc")
        update_resp = await client.put(
            "/api/strategies/configs/ma_cross_btc",
            json={
                "name": "ma_cross_btc",
                "strategy_type": "ma_cross",
                "symbol": "ETH-USDT",
                "timeframe": "15m",
                "enabled": False,
                "params": {"fast_window": 6, "slow_window": 20},
            },
        )
        clone_resp = await client.post(
            "/api/strategies/configs/ma_cross_btc/clone",
            json={"target_name": "ma_cross_clone", "overrides": {"params": {"fast_window": 7}}},
        )
        delete_resp = await client.delete("/api/strategies/configs/ma_cross_btc")

    assert types_resp.status_code == 200
    assert types_resp.json()[0]["strategy_type"] == "ma_cross"
    assert validate_resp.status_code == 200
    assert validate_resp.json()["config"]["symbol"] == "BTC-USDT"
    assert yaml.safe_load(yaml_resp.json()["yaml"]) == validate_resp.json()["config"]
    assert create_resp.status_code == 201
    assert duplicate_resp.status_code == 409
    assert get_resp.status_code == 200
    assert update_resp.status_code == 200
    assert update_resp.json()["params"] == {"fast_window": 6, "slow_window": 20, "amount": 0.1}
    assert clone_resp.status_code == 201
    assert clone_resp.json()["name"] == "ma_cross_clone"
    assert clone_resp.json()["enabled"] is False
    assert delete_resp.status_code == 204
    assert [message["type"] for message in messages] == [
        "strategy_config_created",
        "strategy_config_updated",
        "strategy_config_created",
        "strategy_config_deleted",
    ]


@pytest.mark.asyncio
async def test_strategy_config_yaml_validation_reports_syntax_and_semantic_envelopes(monkeypatch):
    class FakeRepository:
        def get_strategy_configs(self):
            return []

    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    app = FastAPI()
    app.include_router(strategy_api.create_router(), prefix="/api/strategies")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        syntax_resp = await client.post(
            "/api/strategies/configs/validate-yaml",
            content="name: bad\nparams:\n  fast_window: [\n",
        )
        scalar_resp = await client.post("/api/strategies/configs/validate-yaml", content="42\n")
        multi_resp = await client.post(
            "/api/strategies/configs/validate-yaml",
            content="name: a\n---\nname: b\n",
        )
        unknown_resp = await client.post(
            "/api/strategies/configs/validate-yaml",
            content=(
                "name: x\n"
                "strategy_type: ma_cross\n"
                "symbol: BTC-USDT\n"
                "timeframe: 1m\n"
                "unexpected: true\n"
                "params: {}\n"
            ),
        )
        unsupported_resp = await client.post(
            "/api/strategies/configs/validate",
            json={
                "name": "x",
                "strategy_type": "grid",
                "symbol": "BTC-USDT",
                "timeframe": "1m",
                "params": {},
            },
        )
        semantic_resp = await client.post(
            "/api/strategies/configs/validate?expected_name=expected",
            json={
                "name": "other",
                "strategy_type": "ma_cross",
                "symbol": "BTC-USDT",
                "timeframe": "1m",
                "params": {"fast_window": 30, "slow_window": 10},
            },
        )

    assert syntax_resp.status_code == 400
    assert syntax_resp.json()["detail"]["code"] == "strategy_yaml_invalid"
    assert syntax_resp.json()["detail"]["issues"][0]["line"] == 3
    assert syntax_resp.json()["detail"]["issues"][0]["column"] > 0
    assert scalar_resp.status_code == 400
    assert multi_resp.status_code == 400
    assert unknown_resp.status_code == 400
    assert unknown_resp.json()["detail"]["code"] == "strategy_validation_failed"
    assert unknown_resp.json()["detail"]["issues"][0]["code"] == "unknown_field"
    assert unsupported_resp.status_code == 400
    assert unsupported_resp.json()["detail"]["code"] == "strategy_validation_failed"
    assert semantic_resp.status_code == 422
    assert {issue["code"] for issue in semantic_resp.json()["detail"]["issues"]} == {
        "name_mismatch",
        "invalid_window_order",
    }


def strategy_config_payload(**overrides):
    payload = {
        "name": "ma_cross_btc",
        "strategy_type": "ma_cross",
        "symbol": "BTC-USDT",
        "timeframe": "1h",
        "enabled": True,
        "params": {"fast_window": 5, "slow_window": 20},
    }
    payload.update(overrides)
    return payload


PERSISTED_BUILTIN_STRATEGY_CASES = [
    pytest.param(
        {
            "name": "persisted_ma_cross",
            "strategy_type": "ma_cross",
            "strategy_class": MACrossStrategy,
            "symbol": "ETH-USDT",
            "timeframe": "15m",
            "params": {"fast_window": 3.0, "slow_window": 4.0, "amount": 0.25},
            "expected_params": {"fast_window": 3, "slow_window": 4, "amount": 0.25},
        },
        id="ma-cross",
    ),
    pytest.param(
        {
            "name": "persisted_rsi_mean_reversion",
            "strategy_type": "rsi_mean_reversion",
            "strategy_class": RSIMeanReversionStrategy,
            "symbol": "ETH-USDT",
            "timeframe": "15m",
            "params": {
                "period": 14.0,
                "oversold": 28,
                "overbought": 72.5,
                "amount": 0.15,
            },
            "expected_params": {
                "period": 14,
                "oversold": 28.0,
                "overbought": 72.5,
                "amount": 0.15,
            },
        },
        id="rsi-mean-reversion",
    ),
    pytest.param(
        {
            "name": "persisted_bollinger_mean_reversion",
            "strategy_type": "bollinger_mean_reversion",
            "strategy_class": BollingerMeanReversionStrategy,
            "symbol": "ETH-USDT",
            "timeframe": "15m",
            "params": {"window": 21.0, "stddev_multiplier": 2, "amount": 0.2},
            "expected_params": {
                "window": 21,
                "stddev_multiplier": 2.0,
                "amount": 0.2,
            },
        },
        id="bollinger-mean-reversion",
    ),
    pytest.param(
        {
            "name": "persisted_donchian_breakout",
            "strategy_type": "donchian_breakout",
            "strategy_class": DonchianBreakoutStrategy,
            "symbol": "ETH-USDT",
            "timeframe": "15m",
            "params": {
                "entry_window": 20.0,
                "exit_window": 9.0,
                "amount": 0.3,
            },
            "expected_params": {"entry_window": 20, "exit_window": 9, "amount": 0.3},
        },
        id="donchian-breakout",
    ),
]


class IsolatedStrategyConfigRepository:
    configs = {}
    positions = {}
    fail_next_create = False
    fail_next_update = False
    fail_next_delete = False

    def get_strategy_configs(self):
        return list(self.configs.values())

    def get_strategy_config(self, name):
        return self.configs.get(name)

    def create_strategy_config(self, config):
        if self.fail_next_create:
            self.__class__.fail_next_create = False
            raise RuntimeError("create failed")
        if config.name in self.configs:
            raise strategy_api.IntegrityError("duplicate", None, None)
        self.configs[config.name] = config
        return config

    def update_strategy_config(self, name, config):
        if self.fail_next_update:
            self.__class__.fail_next_update = False
            raise RuntimeError("update failed")
        existing = self.configs.get(name)
        if existing is None:
            return None
        existing.strategy_type = config.strategy_type
        existing.symbol = config.symbol
        existing.timeframe = config.timeframe
        existing.params = config.params
        existing.enabled = config.enabled
        existing.updated_at = config.updated_at
        return existing

    def delete_strategy_config(self, name):
        if self.fail_next_delete:
            self.__class__.fail_next_delete = False
            raise RuntimeError("delete failed")
        return self.configs.pop(name, None) is not None

    def get_open_positions(self, strategy=None):
        return self.positions.get(strategy, [])

    def get_kill_switch(self):
        return SimpleNamespace(engaged=False)


class FakeMarketDataService:
    def __init__(self):
        self.subscriptions = []
        self.unsubscriptions = []
        self.start_calls = 0
        self.stop_calls = 0
        self._running = False

    def subscribe(self, symbol, timeframe, callback):
        self.subscriptions.append((symbol, timeframe, callback))

    def unsubscribe(self, symbol, timeframe, callback):
        self.unsubscriptions.append((symbol, timeframe, callback))

    def get_recent_bars(self, symbol, timeframe, count=1):
        return []

    async def start(self):
        self.start_calls += 1
        self._running = True
        for _symbol, _timeframe, callback in list(self.subscriptions):
            await callback(Bar(1, 1, 1, 1, 1, 1))

    async def stop(self):
        self.stop_calls += 1
        self._running = False


@pytest.fixture(autouse=True)
def fake_market_data_service_factory(monkeypatch):
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        lambda: FakeMarketDataService(),
    )


@pytest.fixture
def isolated_strategy_config_api(monkeypatch):
    IsolatedStrategyConfigRepository.configs = {}
    IsolatedStrategyConfigRepository.positions = {}
    IsolatedStrategyConfigRepository.fail_next_create = False
    IsolatedStrategyConfigRepository.fail_next_update = False
    IsolatedStrategyConfigRepository.fail_next_delete = False
    monkeypatch.setattr(strategy_api, "Repository", IsolatedStrategyConfigRepository, raising=False)
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000)
    messages = []

    async def broadcast(message):
        messages.append(message)

    runtime = strategy_api.StrategyRuntimeState()
    app = FastAPI()
    app.include_router(
        strategy_api.create_router(broadcast=broadcast, runtime=runtime),
        prefix="/api/strategies",
    )
    return app, runtime, messages


@pytest.mark.asyncio
async def test_clone_config_to_different_strategy_type_replaces_source_params(
    isolated_strategy_config_api,
):
    app, _runtime, _messages = isolated_strategy_config_api
    source_params = {"window": 20, "stddev_multiplier": 2, "amount": 0.1}
    target_params = {
        "period": 14,
        "oversold": 30,
        "overbought": 70,
        "amount": 0.25,
    }
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_resp = await client.post(
            "/api/strategies/configs",
            json={
                "name": "test",
                "strategy_type": "bollinger_mean_reversion",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "params": source_params,
                "enabled": True,
            },
        )
        clone_resp = await client.post(
            "/api/strategies/configs/test/clone",
            json={
                "target_name": "test_rsi_clone",
                "overrides": {
                    "strategy_type": "rsi_mean_reversion",
                    "symbol": "ETH-USDT",
                    "timeframe": "15m",
                    "enabled": False,
                    "params": target_params,
                },
            },
        )

    assert create_resp.status_code == 201
    assert clone_resp.status_code == 201
    clone = clone_resp.json()
    assert clone["enabled"] is False
    assert clone["strategy_type"] == "rsi_mean_reversion"
    assert clone["params"] == target_params
    assert "window" not in clone["params"]
    assert "stddev_multiplier" not in clone["params"]
    assert IsolatedStrategyConfigRepository.configs["test_rsi_clone"].params == target_params


@pytest.mark.asyncio
async def test_clone_config_to_different_strategy_type_without_params_uses_target_defaults(
    isolated_strategy_config_api,
):
    app, _runtime, _messages = isolated_strategy_config_api
    source_params = {"window": 20, "stddev_multiplier": 2, "amount": 0.1}
    rsi_defaults = {
        "period": 14,
        "oversold": 30,
        "overbought": 70,
        "amount": 0.1,
    }
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_resp = await client.post(
            "/api/strategies/configs",
            json={
                "name": "test",
                "strategy_type": "bollinger_mean_reversion",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "params": source_params,
                "enabled": True,
            },
        )
        clone_resp = await client.post(
            "/api/strategies/configs/test/clone",
            json={
                "target_name": "test_rsi_defaults_clone",
                "overrides": {
                    "strategy_type": "rsi_mean_reversion",
                },
            },
        )

    assert create_resp.status_code == 201
    assert clone_resp.status_code == 201
    clone = clone_resp.json()
    assert clone["enabled"] is False
    assert clone["strategy_type"] == "rsi_mean_reversion"
    assert clone["params"] == rsi_defaults
    assert "window" not in clone["params"]
    assert "stddev_multiplier" not in clone["params"]
    stored_clone = IsolatedStrategyConfigRepository.configs["test_rsi_defaults_clone"]
    assert stored_clone.params == rsi_defaults


@pytest.mark.asyncio
async def test_clone_config_to_canonically_same_strategy_type_merges_partial_params(
    isolated_strategy_config_api,
):
    app, _runtime, _messages = isolated_strategy_config_api
    source_params = {"window": 24, "stddev_multiplier": 2.5, "amount": 0.2}
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_resp = await client.post(
            "/api/strategies/configs",
            json={
                "name": "test",
                "strategy_type": "bollinger_mean_reversion",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "params": source_params,
                "enabled": True,
            },
        )
        clone_resp = await client.post(
            "/api/strategies/configs/test/clone",
            json={
                "target_name": "test_bollinger_clone",
                "overrides": {
                    "strategy_type": " bollinger_mean_reversion ",
                    "params": {"amount": 0.35},
                },
            },
        )

    assert create_resp.status_code == 201
    assert clone_resp.status_code == 201
    clone = clone_resp.json()
    assert clone["enabled"] is False
    assert clone["strategy_type"] == "bollinger_mean_reversion"
    assert clone["params"]["amount"] == 0.35
    assert clone["params"]["window"] == 24
    assert clone["params"]["stddev_multiplier"] == 2.5
    stored_clone = IsolatedStrategyConfigRepository.configs["test_bollinger_clone"]
    assert stored_clone.params == clone["params"]


@pytest.fixture
def hermetic_persisted_builtin_strategy_api(monkeypatch):
    IsolatedStrategyConfigRepository.configs = {}
    IsolatedStrategyConfigRepository.positions = {}
    IsolatedStrategyConfigRepository.fail_next_create = False
    IsolatedStrategyConfigRepository.fail_next_update = False
    IsolatedStrategyConfigRepository.fail_next_delete = False
    safe_settings = AppConfig(
        mode="paper",
        exchange=ExchangeConfig(demo=True),
        risk=RiskConfig(allow_live_open_orders=False, live_max_order_notional=0.0),
    )
    market_data = FakeMarketDataService()
    notifier_factory_calls = []

    def create_noop_risk_event_notifier():
        notifier_factory_calls.append(True)
        return None

    def fail_live_order_handler(settings):
        raise AssertionError("Live order handler must not be created")

    async def fail_live_sync(*args, **kwargs):
        raise AssertionError("Live state synchronization must not run")

    monkeypatch.setattr(strategy_api, "Repository", IsolatedStrategyConfigRepository, raising=False)
    monkeypatch.setattr(strategy_api, "load_runtime_settings", lambda: safe_settings, raising=False)
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        lambda: market_data,
        raising=False,
    )
    monkeypatch.setattr(
        strategy_api,
        "create_risk_event_notifier",
        create_noop_risk_event_notifier,
        raising=False,
    )
    monkeypatch.setattr(
        strategy_api,
        "create_live_order_handler",
        fail_live_order_handler,
        raising=False,
    )
    monkeypatch.setattr(strategy_api, "refresh_okx_live_state", fail_live_sync, raising=False)
    monkeypatch.setattr(
        strategy_api,
        "current_timestamp_ms",
        lambda: 1700000000000,
        raising=False,
    )
    messages = []

    async def broadcast(message):
        messages.append(message)

    runtime = strategy_api.StrategyRuntimeState()
    app = FastAPI()
    app.include_router(
        strategy_api.create_router(broadcast=broadcast, runtime=runtime),
        prefix="/api/strategies",
    )
    return SimpleNamespace(
        app=app,
        runtime=runtime,
        messages=messages,
        market_data=market_data,
        notifier_factory_calls=notifier_factory_calls,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    PERSISTED_BUILTIN_STRATEGY_CASES,
)
async def test_strategy_config_api_accepts_all_registered_builtin_types(
    hermetic_persisted_builtin_strategy_api,
    case,
):
    environment = hermetic_persisted_builtin_strategy_api
    payload = {
        "name": case["name"],
        "strategy_type": case["strategy_type"],
        "symbol": case["symbol"],
        "timeframe": case["timeframe"],
        "params": dict(case["params"]),
        "enabled": True,
    }
    expected_config = {
        "name": case["name"],
        "strategy_type": case["strategy_type"],
        "symbol": case["symbol"],
        "timeframe": case["timeframe"],
        "params": dict(case["expected_params"]),
        "enabled": True,
    }

    transport = ASGITransport(app=environment.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        validate_response = await client.post(
            "/api/strategies/configs/validate",
            json=payload,
        )
        create_response = await client.post(
            "/api/strategies/configs",
            json=payload,
        )
        fetch_response = await client.get(f"/api/strategies/configs/{case['name']}")
        configs_response = await client.get("/api/strategies/configs")
        statuses_response = await client.get("/api/strategies")
        types_response = await client.get("/api/strategies/types")

    assert validate_response.status_code == 200
    assert create_response.status_code == 201
    assert fetch_response.status_code == 200
    assert configs_response.status_code == 200
    assert statuses_response.status_code == 200
    assert types_response.status_code == 200

    validation_result = validate_response.json()
    validated = validation_result["config"]
    validation_yaml = validation_result["yaml"]
    created = create_response.json()
    fetched = fetch_response.json()
    configs = configs_response.json()
    statuses = statuses_response.json()
    strategy_types = types_response.json()

    for field, expected_value in expected_config.items():
        assert validated[field] == expected_value
        assert created[field] == expected_value
        assert fetched[field] == expected_value

    parsed_yaml = yaml.safe_load(validation_yaml)
    assert parsed_yaml == expected_config
    assert any(
        config["name"] == case["name"]
        and all(
            config[field] == expected_value for field, expected_value in expected_config.items()
        )
        for config in configs
    )
    assert {"name": case["name"], "status": "stopped"} in statuses
    assert case["strategy_type"] in {definition["strategy_type"] for definition in strategy_types}


@pytest.mark.asyncio
async def test_strategy_start_preserves_preexisting_startup_owner(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    sentinel_owner = SimpleNamespace(stop_calls=0)
    constructed_engines = []

    async def fail_live_sync(*args, **kwargs):
        raise AssertionError("External OKX sync must not be attempted")

    class RecordingEngine:
        def __init__(self, *args, **kwargs):
            self.start_calls = 0
            self.stop_calls = 0
            constructed_engines.append(self)

        async def start(self):
            self.start_calls += 1

        async def stop(self):
            self.stop_calls += 1

    monkeypatch.setattr(strategy_api, "refresh_okx_live_state", fail_live_sync, raising=False)
    monkeypatch.setattr(strategy_api, "BotEngine", RecordingEngine)
    environment.runtime.starting_engines["ma_cross"] = sentinel_owner
    transport = ASGITransport(app=environment.app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/strategies/ma_cross/start")

    assert response.status_code == 200
    assert environment.runtime.starting_engines.get("ma_cross") is sentinel_owner
    assert constructed_engines == []
    assert sentinel_owner.stop_calls == 0


@pytest.mark.asyncio
async def test_strategy_start_cancellation_during_engine_start_cleans_startup_owner(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []
    start_entered = asyncio.Event()

    class CancellableStartEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            created_engines.append(self)

        async def start(self):
            start_entered.set()
            await asyncio.Future()

        async def stop(self):
            self.stop_calls += 1

    monkeypatch.setattr(strategy_api, "BotEngine", CancellableStartEngine)
    transport = ASGITransport(app=environment.app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_task = asyncio.create_task(client.post("/api/strategies/ma_cross/start"))
        await asyncio.wait_for(start_entered.wait(), timeout=1)
        engine = created_engines[0]
        assert environment.runtime.starting_engines["ma_cross"] is engine

        start_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start_task

    assert environment.runtime.starting_engines.get("ma_cross") is not engine
    assert environment.runtime.engines.get("ma_cross") is not engine
    assert engine.stop_calls == 1
    assert environment.market_data.stop_calls == 1


@pytest.mark.asyncio
async def test_strategy_start_cancellation_after_engine_start_cleans_orphaned_engine(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []

    class CancelBeforeOwnershipTransferLock:
        def __init__(self):
            self._lock = asyncio.Lock()
            self.entries = 0

        async def __aenter__(self):
            await self._lock.acquire()
            self.entries += 1
            if self.entries == 2:
                self._lock.release()
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self._lock.release()

    class StartedEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            created_engines.append(self)

        async def start(self):
            return None

        async def stop(self):
            self.stop_calls += 1

    environment.runtime.market_data_lifecycle_lock = CancelBeforeOwnershipTransferLock()
    monkeypatch.setattr(strategy_api, "BotEngine", StartedEngine)
    transport = ASGITransport(app=environment.app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_task = asyncio.create_task(client.post("/api/strategies/ma_cross/start"))
        with pytest.raises(asyncio.CancelledError):
            await start_task

    engine = created_engines[0]
    assert environment.runtime.starting_engines.get("ma_cross") is not engine
    assert environment.runtime.engines.get("ma_cross") is not engine
    assert engine.stop_calls == 1
    assert environment.market_data.stop_calls == 1


@pytest.mark.asyncio
async def test_strategy_start_cancellation_preserves_request_cancel_when_stop_is_cancelled(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []
    start_entered = asyncio.Event()

    class StopCancelledEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            self.market_data_service = kwargs["market_data_service"]
            created_engines.append(self)

        async def start(self):
            self.running = True
            self.market_data_service._running = True
            start_entered.set()
            await asyncio.Future()

        async def stop(self):
            self.stop_calls += 1
            self.running = False
            raise asyncio.CancelledError("engine stop cleanup cancelled")

    monkeypatch.setattr(strategy_api, "BotEngine", StopCancelledEngine)
    transport = ASGITransport(app=environment.app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_task = asyncio.create_task(client.post("/api/strategies/ma_cross/start"))
        await asyncio.wait_for(start_entered.wait(), timeout=1)
        engine = created_engines[0]
        assert environment.runtime.starting_engines["ma_cross"] is engine

        start_task.cancel("original request cancellation")
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await start_task

    assert exc_info.value.args == ("original request cancellation",)
    assert environment.runtime.starting_engines.get("ma_cross") is not engine
    assert environment.runtime.engines.get("ma_cross") is not engine
    assert engine.running is False
    assert engine.stop_calls == 1
    assert environment.market_data.stop_calls == 1
    assert environment.market_data._running is False


@pytest.mark.asyncio
async def test_strategy_start_cancellation_cleanup_finishes_despite_second_cancel(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []
    start_entered = asyncio.Event()
    stop_entered = asyncio.Event()
    allow_stop_to_finish = asyncio.Event()

    class SlowStopEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            self.market_data_service = kwargs["market_data_service"]
            created_engines.append(self)

        async def start(self):
            self.running = True
            self.market_data_service._running = True
            start_entered.set()
            await asyncio.Future()

        async def stop(self):
            self.stop_calls += 1
            stop_entered.set()
            await allow_stop_to_finish.wait()
            self.running = False

    monkeypatch.setattr(strategy_api, "BotEngine", SlowStopEngine)
    transport = ASGITransport(app=environment.app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_task = asyncio.create_task(client.post("/api/strategies/ma_cross/start"))
        await asyncio.wait_for(start_entered.wait(), timeout=1)
        engine = created_engines[0]

        start_task.cancel("first request cancellation")
        await asyncio.wait_for(stop_entered.wait(), timeout=1)
        start_task.cancel("second request cancellation")
        await asyncio.sleep(0)
        allow_stop_to_finish.set()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await asyncio.wait_for(start_task, timeout=1)

    assert exc_info.value.args == ("first request cancellation",)
    assert environment.runtime.starting_engines.get("ma_cross") is not engine
    assert environment.runtime.engines.get("ma_cross") is not engine
    assert engine.running is False
    assert engine.stop_calls == 1
    assert environment.market_data.stop_calls == 1
    assert environment.market_data._running is False


@pytest.mark.asyncio
async def test_strategy_start_cancellation_after_ownership_transfer_cleans_engine(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []
    running_broadcast_entered = asyncio.Event()

    async def broadcast(message):
        if message["type"] == "strategy_status" and message["status"] == "running":
            running_broadcast_entered.set()
            await asyncio.Future()
        environment.messages.append(message)

    class TransferredEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            self.market_data_service = kwargs["market_data_service"]
            created_engines.append(self)

        async def start(self):
            self.running = True
            self.market_data_service._running = True

        async def stop(self):
            self.stop_calls += 1
            self.running = False

    app = FastAPI()
    app.include_router(
        strategy_api.create_router(broadcast=broadcast, runtime=environment.runtime),
        prefix="/api/strategies",
    )
    monkeypatch.setattr(strategy_api, "BotEngine", TransferredEngine)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_task = asyncio.create_task(client.post("/api/strategies/ma_cross/start"))
        await asyncio.wait_for(running_broadcast_entered.wait(), timeout=1)
        engine = created_engines[0]
        assert environment.runtime.engines["ma_cross"] is engine

        start_task.cancel("post-transfer cancellation")
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await start_task

    assert exc_info.value.args == ("post-transfer cancellation",)
    assert environment.runtime.engines.get("ma_cross") is not engine
    assert environment.runtime.starting_engines.get("ma_cross") is not engine
    assert environment.runtime.strategy_status["ma_cross"] == "stopped"
    assert engine.running is False
    assert engine.stop_calls == 1
    assert environment.market_data.stop_calls == 1
    assert environment.market_data._running is False
    assert not any(message.get("status") == "running" for message in environment.messages)


@pytest.mark.asyncio
async def test_strategy_start_post_transfer_cleanup_finishes_despite_second_cancel(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []
    running_broadcast_entered = asyncio.Event()
    stop_entered = asyncio.Event()
    allow_stop_to_finish = asyncio.Event()

    async def broadcast(message):
        if message["type"] == "strategy_status" and message["status"] == "running":
            running_broadcast_entered.set()
            await asyncio.Future()
        environment.messages.append(message)

    class SlowTransferredEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            self.market_data_service = kwargs["market_data_service"]
            created_engines.append(self)

        async def start(self):
            self.running = True
            self.market_data_service._running = True

        async def stop(self):
            self.stop_calls += 1
            stop_entered.set()
            await allow_stop_to_finish.wait()
            self.running = False

    app = FastAPI()
    app.include_router(
        strategy_api.create_router(broadcast=broadcast, runtime=environment.runtime),
        prefix="/api/strategies",
    )
    monkeypatch.setattr(strategy_api, "BotEngine", SlowTransferredEngine)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_task = asyncio.create_task(client.post("/api/strategies/ma_cross/start"))
        await asyncio.wait_for(running_broadcast_entered.wait(), timeout=1)
        engine = created_engines[0]

        start_task.cancel("first post-transfer cancellation")
        await asyncio.wait_for(stop_entered.wait(), timeout=1)
        start_task.cancel("second post-transfer cancellation")
        await asyncio.sleep(0)
        allow_stop_to_finish.set()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await asyncio.wait_for(start_task, timeout=1)

    assert exc_info.value.args == ("first post-transfer cancellation",)
    assert environment.runtime.engines.get("ma_cross") is not engine
    assert environment.runtime.starting_engines.get("ma_cross") is not engine
    assert engine.running is False
    assert engine.stop_calls == 1
    assert environment.market_data.stop_calls == 1
    assert environment.market_data._running is False


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement_map", ["engines", "starting_engines"])
async def test_strategy_start_post_transfer_cleanup_keeps_replacement_owner(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
    replacement_map,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []
    running_broadcast_entered = asyncio.Event()
    stop_entered = asyncio.Event()
    allow_stop_to_finish = asyncio.Event()
    replacement_engine = SimpleNamespace(stop_calls=0)

    async def broadcast(message):
        if message["type"] == "strategy_status" and message["status"] == "running":
            running_broadcast_entered.set()
            await asyncio.Future()
        environment.messages.append(message)

    class ReplacedTransferredEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            self.market_data_service = kwargs["market_data_service"]
            created_engines.append(self)

        async def start(self):
            self.running = True
            self.market_data_service._running = True

        async def stop(self):
            self.stop_calls += 1
            stop_entered.set()
            await allow_stop_to_finish.wait()
            self.running = False

    app = FastAPI()
    app.include_router(
        strategy_api.create_router(broadcast=broadcast, runtime=environment.runtime),
        prefix="/api/strategies",
    )
    monkeypatch.setattr(strategy_api, "BotEngine", ReplacedTransferredEngine)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_task = asyncio.create_task(client.post("/api/strategies/ma_cross/start"))
        await asyncio.wait_for(running_broadcast_entered.wait(), timeout=1)
        engine = created_engines[0]

        start_task.cancel("replacement post-transfer cancellation")
        await asyncio.wait_for(stop_entered.wait(), timeout=1)
        async with environment.runtime.market_data_lifecycle_lock:
            if replacement_map == "engines":
                environment.runtime.engines["ma_cross"] = replacement_engine
            else:
                environment.runtime.starting_engines["ma_cross"] = replacement_engine
        allow_stop_to_finish.set()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await start_task

    assert exc_info.value.args == ("replacement post-transfer cancellation",)
    owner_map = getattr(environment.runtime, replacement_map)
    assert owner_map["ma_cross"] is replacement_engine
    assert engine.stop_calls == 1
    assert engine.running is False
    assert environment.market_data.stop_calls == 0
    assert environment.market_data._running is True


@pytest.mark.asyncio
async def test_strategy_start_cancellation_keeps_replacement_startup_owner(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []
    start_entered = asyncio.Event()
    replacement_engine = object()

    class ReplacedStartupOwnerEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            created_engines.append(self)

        async def start(self):
            self.running = True
            start_entered.set()
            await asyncio.Future()

        async def stop(self):
            self.stop_calls += 1
            self.running = False

    monkeypatch.setattr(strategy_api, "BotEngine", ReplacedStartupOwnerEngine)
    transport = ASGITransport(app=environment.app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_task = asyncio.create_task(client.post("/api/strategies/ma_cross/start"))
        await asyncio.wait_for(start_entered.wait(), timeout=1)
        engine = created_engines[0]
        assert environment.runtime.starting_engines["ma_cross"] is engine
        environment.runtime.starting_engines["ma_cross"] = replacement_engine

        start_task.cancel("request cancellation")
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await start_task

    assert exc_info.value.args == ("request cancellation",)
    assert environment.runtime.starting_engines["ma_cross"] is replacement_engine
    assert environment.runtime.engines.get("ma_cross") is not engine
    assert engine.running is False
    assert engine.stop_calls == 1


@pytest.mark.asyncio
async def test_strategy_start_success_keeps_replacement_startup_owner(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []
    start_entered = asyncio.Event()
    finish_start = asyncio.Event()
    replacement_engine = object()

    class SuccessfulReplacedStartupOwnerEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            created_engines.append(self)

        async def start(self):
            self.running = True
            start_entered.set()
            await finish_start.wait()

        async def stop(self):
            self.stop_calls += 1
            self.running = False

    monkeypatch.setattr(strategy_api, "BotEngine", SuccessfulReplacedStartupOwnerEngine)
    transport = ASGITransport(app=environment.app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_task = asyncio.create_task(client.post("/api/strategies/ma_cross/start"))
        await asyncio.wait_for(start_entered.wait(), timeout=1)
        engine = created_engines[0]
        assert environment.runtime.starting_engines["ma_cross"] is engine
        async with environment.runtime.market_data_lifecycle_lock:
            environment.runtime.starting_engines["ma_cross"] = replacement_engine
            environment.runtime.strategy_status["ma_cross"] = "replacement-starting"
            environment.runtime.strategy_errors["ma_cross"] = "replacement startup pending"

        finish_start.set()
        response = await start_task

    assert environment.runtime.starting_engines["ma_cross"] is replacement_engine
    assert environment.runtime.engines.get("ma_cross") is not engine
    assert engine.running is False
    assert engine.stop_calls == 1
    assert environment.market_data.stop_calls == 0
    assert environment.runtime.strategy_status["ma_cross"] == "replacement-starting"
    assert environment.runtime.strategy_errors["ma_cross"] == "replacement startup pending"
    assert response.status_code == 409
    assert response.json()["detail"] == "Strategy startup was superseded"


@pytest.mark.asyncio
async def test_strategy_start_failure_keeps_replacement_startup_owner(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []
    start_entered = asyncio.Event()
    fail_start = asyncio.Event()
    replacement_engine = object()

    class FailingReplacedStartupOwnerEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            created_engines.append(self)

        async def start(self):
            self.running = True
            start_entered.set()
            await fail_start.wait()
            raise RuntimeError("startup boom")

        async def stop(self):
            self.stop_calls += 1
            self.running = False

    monkeypatch.setattr(strategy_api, "BotEngine", FailingReplacedStartupOwnerEngine)
    transport = ASGITransport(app=environment.app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_task = asyncio.create_task(client.post("/api/strategies/ma_cross/start"))
        await asyncio.wait_for(start_entered.wait(), timeout=1)
        engine = created_engines[0]
        assert environment.runtime.starting_engines["ma_cross"] is engine
        environment.runtime.starting_engines["ma_cross"] = replacement_engine
        environment.runtime.strategy_status["ma_cross"] = "replacement-starting"
        environment.runtime.strategy_errors["ma_cross"] = "replacement startup pending"

        fail_start.set()
        response = await start_task

    assert response.status_code == 400
    assert response.json()["detail"] == "startup boom"
    assert environment.runtime.starting_engines["ma_cross"] is replacement_engine
    assert environment.runtime.engines.get("ma_cross") is not engine
    assert engine.running is False
    assert engine.stop_calls == 1
    assert environment.market_data.stop_calls == 0
    assert environment.runtime.strategy_status["ma_cross"] == "replacement-starting"
    assert environment.runtime.strategy_errors["ma_cross"] == "replacement startup pending"


@pytest.mark.asyncio
async def test_strategy_start_ordinary_failure_records_current_startup_owner_error(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []

    class FailingStartupOwnerEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            created_engines.append(self)

        async def start(self):
            self.running = True
            raise RuntimeError("startup boom")

        async def stop(self):
            self.stop_calls += 1
            self.running = False

    monkeypatch.setattr(strategy_api, "BotEngine", FailingStartupOwnerEngine)
    transport = ASGITransport(app=environment.app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/strategies/ma_cross/start")

    engine = created_engines[0]
    assert response.status_code == 400
    assert response.json()["detail"] == "startup boom"
    assert environment.runtime.starting_engines == {}
    assert environment.runtime.engines == {}
    assert engine.running is False
    assert engine.stop_calls == 1
    assert environment.market_data.stop_calls == 1
    assert environment.runtime.strategy_status["ma_cross"] == "stopped"
    assert environment.runtime.strategy_errors["ma_cross"] == "startup boom"
    assert {
        "type": "strategy_status",
        "strategy": "ma_cross",
        "status": "stopped",
        "timestamp": 1700000000000,
    } in environment.messages
    assert {
        "type": "strategy_error",
        "strategy": "ma_cross",
        "error": "startup boom",
        "timestamp": 1700000000000,
    } in environment.messages


@pytest.mark.asyncio
async def test_strategy_error_after_ownership_loss_does_not_report_against_replacement(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []
    stop_entered = asyncio.Event()
    release_stop = asyncio.Event()
    replacement_installed = asyncio.Event()
    broadcasts_after_replacement = []
    messages = []

    class BlockingStopMarketDataService(FakeMarketDataService):
        async def stop(self):
            self.stop_calls += 1
            stop_entered.set()
            await release_stop.wait()
            self._running = False

    class CallbackCapturingEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            self.on_strategy_error = kwargs["on_strategy_error"]
            created_engines.append(self)

        async def start(self):
            self.running = True

        async def stop(self):
            self.stop_calls += 1
            self.running = False

    async def broadcast(message):
        messages.append(message)
        if replacement_installed.is_set() and message.get("type") in {
            "strategy_status",
            "strategy_error",
        }:
            broadcasts_after_replacement.append(message)

    blocking_market_data = BlockingStopMarketDataService()
    monkeypatch.setattr(strategy_api, "create_market_data_service", lambda: blocking_market_data)
    monkeypatch.setattr(strategy_api, "BotEngine", CallbackCapturingEngine)
    app = FastAPI()
    app.include_router(
        strategy_api.create_router(broadcast=broadcast, runtime=environment.runtime),
        prefix="/api/strategies",
    )
    transport = ASGITransport(app=app)
    error_task = None
    replacement_engine = object()

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            start_response = await client.post("/api/strategies/ma_cross/start")
            assert start_response.status_code == 200

            engine = created_engines[0]
            assert environment.runtime.engines["ma_cross"] is engine
            environment.runtime.strategy_status["ma_cross"] = "running"
            environment.runtime.strategy_errors["ma_cross"] = "replacement B error"
            messages.clear()

            error_task = asyncio.create_task(
                engine.on_strategy_error("ma_cross", RuntimeError("stale A boom"))
            )
            await asyncio.wait_for(stop_entered.wait(), timeout=1)
            assert environment.runtime.engines.get("ma_cross") is not engine

            environment.runtime.engines["ma_cross"] = replacement_engine
            replacement_installed.set()
            release_stop.set()
            await asyncio.wait_for(error_task, timeout=1)
    finally:
        release_stop.set()
        if error_task is not None and not error_task.done():
            error_task.cancel()
            await asyncio.gather(error_task, return_exceptions=True)

    assert environment.runtime.engines["ma_cross"] is replacement_engine
    assert environment.runtime.strategy_status["ma_cross"] == "running"
    assert environment.runtime.strategy_errors["ma_cross"] == "replacement B error"
    assert broadcasts_after_replacement == []


@pytest.mark.asyncio
async def test_persisted_strategy_error_suppressed_after_transient_replacement_supersession(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    strategy_name = "persisted_ma_cross"
    created_engines = []
    status_broadcast_entered = asyncio.Event()
    allow_status_broadcast = asyncio.Event()
    messages = []

    class CallbackCapturingEngine:
        def __init__(self, *args, **kwargs):
            self.running = False
            self.stop_calls = 0
            self.on_strategy_error = kwargs["on_strategy_error"]
            created_engines.append(self)

        async def start(self):
            self.running = True

        async def stop(self):
            self.stop_calls += 1
            self.running = False

    async def broadcast(message):
        messages.append(message)
        if (
            message.get("type") == "strategy_status"
            and message.get("strategy") == strategy_name
            and message.get("status") == "stopped"
        ):
            assert environment.runtime.engines.get(strategy_name) is None
            assert environment.runtime.starting_engines.get(strategy_name) is None
            status_broadcast_entered.set()
            await allow_status_broadcast.wait()

    monkeypatch.setattr(strategy_api, "BotEngine", CallbackCapturingEngine)
    app = FastAPI()
    app.include_router(
        strategy_api.create_router(broadcast=broadcast, runtime=environment.runtime),
        prefix="/api/strategies",
    )
    transport = ASGITransport(app=app)
    error_task = None
    replacement_engine = object()

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_response = await client.post(
                "/api/strategies/configs",
                json={
                    "name": strategy_name,
                    "strategy_type": "ma_cross",
                    "symbol": "BTC-USDT",
                    "timeframe": "1h",
                    "enabled": True,
                    "params": {"fast_window": 5, "slow_window": 20, "amount": 0.1},
                },
            )
            assert create_response.status_code == 201
            start_response = await client.post(f"/api/strategies/{strategy_name}/start")
            assert start_response.status_code == 200

            engine = created_engines[0]
            assert environment.runtime.engines[strategy_name] is engine
            messages.clear()

            error_task = asyncio.create_task(
                engine.on_strategy_error(strategy_name, RuntimeError("stale A boom"))
            )
            await asyncio.wait_for(status_broadcast_entered.wait(), timeout=1)

            environment.runtime.engines[strategy_name] = replacement_engine
            environment.runtime.strategy_status[strategy_name] = "running"
            environment.runtime.strategy_errors[strategy_name] = "replacement B error"
            if environment.runtime.engines.get(strategy_name) is replacement_engine:
                environment.runtime.engines.pop(strategy_name)
            allow_status_broadcast.set()
            await asyncio.wait_for(error_task, timeout=1)
    finally:
        allow_status_broadcast.set()
        if error_task is not None and not error_task.done():
            error_task.cancel()
            await asyncio.gather(error_task, return_exceptions=True)

    assert environment.runtime.engines.get(strategy_name) is None
    assert environment.runtime.starting_engines.get(strategy_name) is None
    assert environment.runtime.strategy_status[strategy_name] == "running"
    assert environment.runtime.strategy_errors[strategy_name] == "replacement B error"
    assert not any(
        message.get("type") == "strategy_error"
        and message.get("strategy") == strategy_name
        and message.get("error") == "stale A boom"
        for message in messages
    )


@pytest.mark.asyncio
async def test_strategy_start_callback_error_after_transfer_stays_stopped(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []
    callback_tasks = []
    engine_transferred = asyncio.Event()
    stopped_broadcast_entered = asyncio.Event()
    release_stopped_broadcast = asyncio.Event()
    messages = []

    class TransferTrackingEngines(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            if key == "ma_cross" and value is created_engines[0]:
                engine_transferred.set()

    async def broadcast(message):
        messages.append(message)
        if (
            message.get("type") == "strategy_status"
            and message.get("strategy") == "ma_cross"
            and message.get("status") == "stopped"
        ):
            assert environment.runtime.engines.get("ma_cross") is not created_engines[0]
            assert environment.runtime.strategy_status["ma_cross"] == "stopped"
            assert environment.runtime.strategy_errors["ma_cross"] == "callback startup boom"
            stopped_broadcast_entered.set()
            await release_stopped_broadcast.wait()

    environment.runtime.engines = TransferTrackingEngines()
    app = FastAPI()
    app.include_router(
        strategy_api.create_router(broadcast=broadcast, runtime=environment.runtime),
        prefix="/api/strategies",
    )

    class CallbackErrorAfterTransferEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            self.on_strategy_error = kwargs["on_strategy_error"]
            created_engines.append(self)

        async def start(self):
            self.running = True

            async def fail_after_transfer():
                await engine_transferred.wait()
                await self.on_strategy_error(
                    "ma_cross",
                    RuntimeError("callback startup boom"),
                )

            callback_tasks.append(asyncio.create_task(fail_after_transfer()))

        async def stop(self):
            self.stop_calls += 1
            self.running = False

    monkeypatch.setattr(strategy_api, "BotEngine", CallbackErrorAfterTransferEngine)
    transport = ASGITransport(app=app)

    response = None
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            start_task = asyncio.create_task(client.post("/api/strategies/ma_cross/start"))
            await asyncio.wait_for(stopped_broadcast_entered.wait(), timeout=1)
            response = await asyncio.wait_for(start_task, timeout=1)
    finally:
        release_stopped_broadcast.set()
        if callback_tasks:
            if engine_transferred.is_set():
                await asyncio.gather(*callback_tasks)
            else:
                for task in callback_tasks:
                    task.cancel()
                await asyncio.gather(*callback_tasks, return_exceptions=True)

    engine = created_engines[0]
    assert response.status_code == 400
    assert response.json()["detail"] == "callback startup boom"
    assert environment.runtime.engines.get("ma_cross") is not engine
    assert environment.runtime.strategy_status["ma_cross"] == "stopped"
    assert environment.runtime.strategy_errors["ma_cross"] == "callback startup boom"
    assert {
        "type": "strategy_status",
        "strategy": "ma_cross",
        "status": "stopped",
        "timestamp": 1700000000000,
    } in messages
    assert {
        "type": "strategy_error",
        "strategy": "ma_cross",
        "error": "callback startup boom",
        "timestamp": 1700000000000,
    } in messages
    assert not any(
        message.get("type") == "strategy_status"
        and message.get("strategy") == "ma_cross"
        and message.get("status") == "running"
        for message in messages
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    PERSISTED_BUILTIN_STRATEGY_CASES,
)
async def test_persisted_builtin_strategy_configs_share_start_stop_lifecycle(
    hermetic_persisted_builtin_strategy_api,
    case,
):
    environment = hermetic_persisted_builtin_strategy_api
    assert environment.runtime.strategy_status == {
        "ma_cross": "stopped",
    }
    assert environment.runtime.strategy_errors == {}
    assert environment.runtime.engines == {}
    assert environment.runtime.starting_engines == {}
    assert environment.runtime.lifecycle_locks == {}

    payload = {
        "name": case["name"],
        "strategy_type": case["strategy_type"],
        "symbol": case["symbol"],
        "timeframe": case["timeframe"],
        "params": dict(case["params"]),
        "enabled": True,
    }

    transport = ASGITransport(app=environment.app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        create_response = await client.post(
            "/api/strategies/configs",
            json=payload,
        )
        assert create_response.status_code == 201
        assert environment.runtime.strategy_status == {
            "ma_cross": "stopped",
            case["name"]: "stopped",
        }
        assert environment.runtime.strategy_errors == {}
        assert environment.runtime.engines == {}
        assert environment.runtime.starting_engines == {}
        assert set(environment.runtime.lifecycle_locks) == {
            case["name"],
        }
        lifecycle_lock = environment.runtime.lifecycle_locks[case["name"]]
        environment.messages.clear()

        start_response = await client.post(f"/api/strategies/{case['name']}/start")

        assert start_response.status_code == 200
        assert start_response.json() == {
            "status": "started",
            "strategy": case["name"],
        }
        assert environment.runtime.strategy_status == {
            "ma_cross": "stopped",
            case["name"]: "running",
        }
        assert environment.runtime.strategy_errors == {}
        assert set(environment.runtime.engines) == {
            case["name"],
        }
        assert environment.runtime.starting_engines == {}
        assert set(environment.runtime.lifecycle_locks) == {
            case["name"],
        }
        assert environment.runtime.lifecycle_locks[case["name"]] is lifecycle_lock

        engine = environment.runtime.engines[case["name"]]
        assert isinstance(engine, BotEngine)
        assert len(engine.strategies) == 1
        strategy = engine.strategies[0]
        assert isinstance(strategy, case["strategy_class"])
        assert strategy.name == case["name"]
        assert strategy.symbol == case["symbol"]
        assert strategy.timeframe == case["timeframe"]
        for field, expected_value in case["expected_params"].items():
            assert getattr(strategy, field) == expected_value
        assert isinstance(strategy._order_manager, UnifiedOrderManager)

        assert len(environment.market_data.subscriptions) == 1
        subscribed_symbol, subscribed_timeframe, subscribed_callback = (
            environment.market_data.subscriptions[0]
        )
        assert subscribed_symbol == case["symbol"]
        assert subscribed_timeframe == case["timeframe"]
        assert environment.market_data.start_calls == 1
        assert environment.messages == [
            {
                "type": "strategy_status",
                "strategy": case["name"],
                "status": "running",
                "timestamp": 1700000000000,
            }
        ]

        stop_response = await client.post(f"/api/strategies/{case['name']}/stop")

    assert stop_response.status_code == 200
    assert stop_response.json() == {
        "status": "stopped",
        "strategy": case["name"],
    }
    assert environment.runtime.strategy_status == {
        "ma_cross": "stopped",
        case["name"]: "stopped",
    }
    assert environment.runtime.strategy_errors == {}
    assert environment.runtime.engines == {}
    assert environment.runtime.starting_engines == {}
    assert set(environment.runtime.lifecycle_locks) == {
        case["name"],
    }
    assert environment.runtime.lifecycle_locks[case["name"]] is lifecycle_lock
    assert len(environment.market_data.unsubscriptions) == 1
    unsubscribed_symbol, unsubscribed_timeframe, unsubscribed_callback = (
        environment.market_data.unsubscriptions[0]
    )
    assert unsubscribed_symbol == case["symbol"]
    assert unsubscribed_timeframe == case["timeframe"]
    assert unsubscribed_callback is subscribed_callback
    assert environment.market_data.stop_calls == 1
    assert environment.market_data._running is False
    assert environment.messages == [
        {
            "type": "strategy_status",
            "strategy": case["name"],
            "status": "running",
            "timestamp": 1700000000000,
        },
        {
            "type": "strategy_status",
            "strategy": case["name"],
            "status": "stopped",
            "timestamp": 1700000000000,
        },
    ]
    assert not any(message["type"] == "strategy_error" for message in environment.messages)
    assert environment.notifier_factory_calls == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    PERSISTED_BUILTIN_STRATEGY_CASES,
)
async def test_disabled_persisted_builtin_configs_cannot_start(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
    case,
):
    environment = hermetic_persisted_builtin_strategy_api
    assert environment.runtime.strategy_status == {
        "ma_cross": "stopped",
    }
    assert environment.runtime.strategy_errors == {}
    assert environment.runtime.engines == {}
    assert environment.runtime.starting_engines == {}
    assert environment.runtime.lifecycle_locks == {}

    payload = {
        "name": case["name"],
        "strategy_type": case["strategy_type"],
        "symbol": case["symbol"],
        "timeframe": case["timeframe"],
        "params": dict(case["params"]),
        "enabled": False,
    }

    transport = ASGITransport(app=environment.app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        create_response = await client.post(
            "/api/strategies/configs",
            json=payload,
        )
        assert create_response.status_code == 201
        assert environment.runtime.strategy_status == {
            "ma_cross": "stopped",
            case["name"]: "stopped",
        }
        assert environment.runtime.strategy_errors == {}
        assert environment.runtime.engines == {}
        assert environment.runtime.starting_engines == {}
        assert set(environment.runtime.lifecycle_locks) == {
            case["name"],
        }
        lifecycle_lock = environment.runtime.lifecycle_locks[case["name"]]
        environment.messages.clear()

        def fail_runtime_construction(*args, **kwargs):
            raise AssertionError("Disabled strategy must not construct runtime dependencies")

        monkeypatch.setattr(
            environment.runtime.registry,
            "create_instance",
            fail_runtime_construction,
        )
        monkeypatch.setattr(strategy_api, "Repository", fail_runtime_construction)
        monkeypatch.setattr(
            strategy_api,
            "create_order_manager",
            fail_runtime_construction,
        )
        monkeypatch.setattr(strategy_api, "BotEngine", fail_runtime_construction)

        start_response = await client.post(f"/api/strategies/{case['name']}/start")

    assert start_response.status_code == 409
    assert start_response.json()["detail"] == "Strategy config is disabled"
    assert environment.runtime.strategy_status == {
        "ma_cross": "stopped",
        case["name"]: "stopped",
    }
    assert environment.runtime.strategy_errors == {}
    assert environment.runtime.engines == {}
    assert environment.runtime.starting_engines == {}
    assert set(environment.runtime.lifecycle_locks) == {
        case["name"],
    }
    assert environment.runtime.lifecycle_locks[case["name"]] is lifecycle_lock
    assert environment.messages == []


def assert_strategy_validation_envelope(response, *, issue_codes):
    detail = response.json()["detail"]
    assert detail["code"] == "strategy_validation_failed"
    assert detail["message"] == "Strategy configuration is invalid"
    assert {issue["code"] for issue in detail["issues"]} == set(issue_codes)
    assert all(
        set(issue) == {"path", "code", "message", "line", "column"} for issue in detail["issues"]
    )


@pytest.mark.asyncio
async def test_strategy_config_update_rejects_strategy_type_change(
    isolated_strategy_config_api,
):
    app, _runtime, messages = isolated_strategy_config_api
    original_params = {"fast_window": 5, "slow_window": 20, "amount": 0.1}
    IsolatedStrategyConfigRepository.configs["ma_cross_btc"] = StrategyConfigRecord(
        **strategy_config_payload(enabled=False, params=dict(original_params)),
        created_at=1,
        updated_at=1,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/strategies/configs/ma_cross_btc",
            json=strategy_config_payload(
                enabled=False,
                strategy_type="rsi_mean_reversion",
                params={
                    "period": 14,
                    "oversold": 30,
                    "overbought": 70,
                    "amount": 0.1,
                },
            ),
        )

    assert response.status_code == 422
    assert_strategy_validation_envelope(response, issue_codes={"strategy_type_mismatch"})
    issue = response.json()["detail"]["issues"][0]
    assert issue["path"] == "strategy_type"
    assert issue["line"] is None
    assert issue["column"] is None
    stored = IsolatedStrategyConfigRepository.configs["ma_cross_btc"]
    assert stored.strategy_type == "ma_cross"
    assert stored.params == original_params
    assert messages == []


@pytest.mark.asyncio
async def test_strategy_config_json_mutations_pass_raw_mapping_to_registry(
    isolated_strategy_config_api,
):
    app, _runtime, _messages = isolated_strategy_config_api
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        malformed_resp = await client.post(
            "/api/strategies/configs",
            content='{"name":',
            headers={"content-type": "application/json"},
        )
        list_root_resp = await client.post("/api/strategies/configs", json=[])
        unknown_create_resp = await client.post(
            "/api/strategies/configs",
            json=strategy_config_payload(unexpected=True),
        )
        enabled_string_resp = await client.post(
            "/api/strategies/configs",
            json=strategy_config_payload(name="ma_cross_eth", enabled="false"),
        )
        await client.post("/api/strategies/configs", json=strategy_config_payload())
        unknown_update_resp = await client.put(
            "/api/strategies/configs/ma_cross_btc",
            json=strategy_config_payload(extra="nope"),
        )
        trimmed_name_resp = await client.put(
            "/api/strategies/configs/ma_cross_btc",
            json=strategy_config_payload(
                name=" ma_cross_btc ",
                params={"fast_window": 6, "slow_window": 20},
            ),
        )
        rename_resp = await client.put(
            "/api/strategies/configs/ma_cross_btc",
            json=strategy_config_payload(name="ma_cross_eth"),
        )

    assert malformed_resp.status_code == 400
    assert malformed_resp.json()["detail"]["code"] == "malformed_config"
    assert list_root_resp.status_code == 400
    assert list_root_resp.json()["detail"]["issues"][0]["code"] == "invalid_root"
    assert unknown_create_resp.status_code == 422
    assert_strategy_validation_envelope(unknown_create_resp, issue_codes={"unknown_field"})
    assert enabled_string_resp.status_code == 422
    assert_strategy_validation_envelope(enabled_string_resp, issue_codes={"invalid_type"})
    assert unknown_update_resp.status_code == 422
    assert_strategy_validation_envelope(unknown_update_resp, issue_codes={"unknown_field"})
    assert trimmed_name_resp.status_code == 200
    assert rename_resp.status_code == 422
    assert_strategy_validation_envelope(rename_resp, issue_codes={"name_mismatch"})


@pytest.mark.asyncio
async def test_strategy_config_yaml_and_json_validation_envelopes_match(
    isolated_strategy_config_api,
):
    app, _runtime, _messages = isolated_strategy_config_api
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        syntax_resp = await client.post(
            "/api/strategies/configs/validate-yaml",
            content="name: [\n",
        )
        root_resp = await client.post("/api/strategies/configs/validate-yaml", content="[]\n")
        multi_resp = await client.post(
            "/api/strategies/configs/validate-yaml",
            content="name: a\n---\nname: b\n",
        )
        yaml_unknown_resp = await client.post(
            "/api/strategies/configs/validate-yaml",
            content=(
                "name: ma_cross_btc\nstrategy_type: ma_cross\nsymbol: BTC-USDT\n"
                "timeframe: 1h\nenabled: true\nunexpected: true\nparams: {}\n"
            ),
        )
        unsupported_resp = await client.post(
            "/api/strategies/configs/validate",
            json=strategy_config_payload(strategy_type="grid"),
        )
        semantic_json_resp = await client.post(
            "/api/strategies/configs/validate?expected_name=ma_cross_btc",
            json=strategy_config_payload(
                name="other",
                params={"fast_window": 30, "slow_window": 10, "unknown": 1},
            ),
        )
        semantic_yaml_resp = await client.post(
            "/api/strategies/configs/validate-yaml?expected_name=ma_cross_btc",
            content=(
                "name: other\nstrategy_type: ma_cross\nsymbol: BTC-USDT\ntimeframe: 1h\n"
                "params:\n  fast_window: 30\n  slow_window: 10\n  unknown: 1\n"
            ),
        )
        invalid_utf8_resp = await client.post(
            "/api/strategies/configs/validate-yaml",
            content=b"\xff\xfe\xfa",
        )

    for response in (syntax_resp, root_resp, multi_resp, invalid_utf8_resp):
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "strategy_yaml_invalid"
        assert response.json()["detail"]["message"] == "Strategy YAML is invalid"
        issue = response.json()["detail"]["issues"][0]
        assert issue["line"] is None or issue["line"] >= 1
        assert issue["column"] is None or issue["column"] >= 1
    assert yaml_unknown_resp.status_code == 400
    assert_strategy_validation_envelope(yaml_unknown_resp, issue_codes={"unknown_field"})
    assert unsupported_resp.status_code == 400
    assert_strategy_validation_envelope(unsupported_resp, issue_codes={"unsupported_strategy_type"})
    assert semantic_json_resp.status_code == 422
    assert_strategy_validation_envelope(
        semantic_json_resp,
        issue_codes={"unknown_param", "invalid_window_order", "name_mismatch"},
    )
    assert semantic_yaml_resp.status_code == 422
    assert semantic_yaml_resp.json()["detail"] == semantic_json_resp.json()["detail"]


@pytest.mark.asyncio
async def test_strategy_config_validation_accepts_supported_two_hour_timeframe(
    isolated_strategy_config_api,
):
    app, _runtime, _messages = isolated_strategy_config_api
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        json_response = await client.post(
            "/api/strategies/configs/validate",
            json=strategy_config_payload(timeframe="2h"),
        )
        yaml_response = await client.post(
            "/api/strategies/configs/validate-yaml",
            content=(
                "name: ma_cross_btc\nstrategy_type: ma_cross\nsymbol: BTC-USDT\n"
                "timeframe: 2h\nenabled: true\nparams:\n"
                "  fast_window: 5\n  slow_window: 20\n"
            ),
        )

    for response in (json_response, yaml_response):
        assert response.status_code == 200
        assert response.json()["config"]["timeframe"] == "2h"


@pytest.mark.asyncio
@pytest.mark.parametrize("timeframe", ["7m", "invalid", "1M", "3M"])
async def test_strategy_config_validation_rejects_unsupported_runtime_timeframes(
    isolated_strategy_config_api,
    timeframe,
):
    app, _runtime, _messages = isolated_strategy_config_api
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        json_response = await client.post(
            "/api/strategies/configs/validate",
            json=strategy_config_payload(timeframe=timeframe),
        )
        yaml_response = await client.post(
            "/api/strategies/configs/validate-yaml",
            content=(
                "name: ma_cross_btc\nstrategy_type: ma_cross\nsymbol: BTC-USDT\n"
                f"timeframe: {timeframe}\nenabled: true\nparams:\n"
                "  fast_window: 5\n  slow_window: 20\n"
            ),
        )

    expected_issue = {
        "path": "timeframe",
        "code": "unsupported_timeframe",
        "message": "Unsupported strategy timeframe",
        "line": None,
        "column": None,
    }
    for response in (json_response, yaml_response):
        assert response.status_code == 422
        assert_strategy_validation_envelope(
            response,
            issue_codes={"unsupported_timeframe"},
        )
        assert response.json()["detail"]["issues"] == [expected_issue]


@pytest.mark.asyncio
@pytest.mark.parametrize("timeframe", ["7m", "invalid", "1M", "3M"])
async def test_strategy_config_mutations_reject_unsupported_timeframe_without_persistence(
    isolated_strategy_config_api,
    timeframe,
):
    app, runtime, messages = isolated_strategy_config_api
    initial_strategy_status = dict(runtime.strategy_status)
    initial_strategy_errors = dict(runtime.strategy_errors)
    initial_engines = dict(runtime.engines)
    initial_starting_engines = dict(runtime.starting_engines)
    initial_lifecycle_locks = dict(runtime.lifecycle_locks)
    original_params = {"fast_window": 5, "slow_window": 20, "amount": 0.1}
    source = StrategyConfigRecord(
        **strategy_config_payload(enabled=False, params=dict(original_params)),
        created_at=1,
        updated_at=1,
    )
    IsolatedStrategyConfigRepository.configs[source.name] = source
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_response = await client.post(
            "/api/strategies/configs",
            json=strategy_config_payload(
                name="unsupported_create",
                timeframe=timeframe,
            ),
        )
        update_response = await client.put(
            f"/api/strategies/configs/{source.name}",
            json=strategy_config_payload(
                enabled=False,
                timeframe=timeframe,
                params={"fast_window": 6, "slow_window": 20, "amount": 0.2},
            ),
        )
        clone_response = await client.post(
            f"/api/strategies/configs/{source.name}/clone",
            json={
                "target_name": "unsupported_clone",
                "overrides": {"timeframe": timeframe},
            },
        )

    for response in (create_response, update_response, clone_response):
        assert response.status_code == 422
        assert_strategy_validation_envelope(
            response,
            issue_codes={"unsupported_timeframe"},
        )
    assert IsolatedStrategyConfigRepository.configs == {source.name: source}
    assert source.timeframe == "1h"
    assert source.params == original_params
    assert source.updated_at == 1
    assert runtime.strategy_status == initial_strategy_status
    assert runtime.strategy_errors == initial_strategy_errors
    assert runtime.engines == initial_engines
    assert runtime.starting_engines == initial_starting_engines
    assert runtime.lifecycle_locks == initial_lifecycle_locks
    assert messages == []


@pytest.mark.asyncio
async def test_strategy_config_lifecycle_safety_isolated_from_real_runtime(
    isolated_strategy_config_api,
    monkeypatch,
):
    app, runtime, messages = isolated_strategy_config_api
    created_engines = []

    class SentinelEngine:
        def __init__(self, *args, **kwargs):
            created_engines.append(self)

    monkeypatch.setattr(strategy_api, "BotEngine", SentinelEngine)
    IsolatedStrategyConfigRepository.configs["ma_cross_btc"] = StrategyConfigRecord(
        **strategy_config_payload(enabled=False),
        created_at=1,
        updated_at=1,
    )
    IsolatedStrategyConfigRepository.configs["ma_cross_eth"] = StrategyConfigRecord(
        **strategy_config_payload(name="ma_cross_eth", symbol="ETH-USDT"),
        created_at=1,
        updated_at=1,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        disabled_start_resp = await client.post("/api/strategies/ma_cross_btc/start")
        runtime.engines["ma_cross_eth"] = object()
        active_update_resp = await client.put(
            "/api/strategies/configs/ma_cross_eth",
            json=strategy_config_payload(name="ma_cross_eth", enabled=True),
        )
        active_disable_resp = await client.put(
            "/api/strategies/configs/ma_cross_eth",
            json=strategy_config_payload(name="ma_cross_eth", enabled=False),
        )
        active_delete_resp = await client.delete("/api/strategies/configs/ma_cross_eth")
        runtime.engines.pop("ma_cross_eth")
        runtime.starting_engines["ma_cross_eth"] = object()
        starting_update_resp = await client.put(
            "/api/strategies/configs/ma_cross_eth",
            json=strategy_config_payload(name="ma_cross_eth"),
        )
        starting_delete_resp = await client.delete("/api/strategies/configs/ma_cross_eth")
        runtime.starting_engines.pop("ma_cross_eth")
        IsolatedStrategyConfigRepository.positions["ma_cross_eth"] = [
            PositionRecord(
                strategy="ma_cross_eth",
                symbol="ETH-USDT",
                side="long",
                amount=0.1,
                entry_price=1,
                leverage=1,
                timestamp=1,
            )
        ]
        open_update_resp = await client.put(
            "/api/strategies/configs/ma_cross_eth",
            json=strategy_config_payload(name="ma_cross_eth"),
        )
        open_disable_resp = await client.put(
            "/api/strategies/configs/ma_cross_eth",
            json=strategy_config_payload(name="ma_cross_eth", enabled=False),
        )
        open_delete_resp = await client.delete("/api/strategies/configs/ma_cross_eth")
        IsolatedStrategyConfigRepository.positions["ma_cross_eth"] = [
            PositionRecord(
                strategy="ma_cross_eth",
                symbol="ETH-USDT",
                side="long",
                amount=0,
                entry_price=1,
                leverage=1,
                timestamp=1,
            )
        ]
        zero_update_resp = await client.put(
            "/api/strategies/configs/ma_cross_eth",
            json=strategy_config_payload(
                name="ma_cross_eth",
                params={"fast_window": 6, "slow_window": 20},
            ),
        )
        runtime.engines["ma_cross_eth"] = object()
        active_source_clone_resp = await client.post(
            "/api/strategies/configs/ma_cross_eth/clone",
            json={"target_name": "ma_cross_active_source_clone", "overrides": {}},
        )
        runtime.engines.pop("ma_cross_eth")
        clone_resp = await client.post(
            "/api/strategies/configs/ma_cross_eth/clone",
            json={"target_name": "ma_cross_clone", "overrides": {}},
        )
        invalid_override_params_resp = await client.post(
            "/api/strategies/configs/ma_cross_eth/clone",
            json={
                "target_name": "ma_cross_invalid_params",
                "overrides": {"params": ["not", "a", "mapping"]},
            },
        )
        runtime.engines["ma_cross_running_target"] = object()
        create_active_target_resp = await client.post(
            "/api/strategies/configs",
            json=strategy_config_payload(name="ma_cross_running_target"),
        )
        clone_active_target_resp = await client.post(
            "/api/strategies/configs/ma_cross_eth/clone",
            json={"target_name": "ma_cross_running_target", "overrides": {}},
        )
        unknown_clone_field_resp = await client.post(
            "/api/strategies/configs/ma_cross_eth/clone",
            json={"target_name": "ma_cross_unknown", "overrides": {}, "extra": True},
        )
        unknown_override_resp = await client.post(
            "/api/strategies/configs/ma_cross_eth/clone",
            json={"target_name": "ma_cross_override", "overrides": {"unexpected": True}},
        )
        IsolatedStrategyConfigRepository.fail_next_update = True
        failed_update_resp = await client.put(
            "/api/strategies/configs/ma_cross_eth",
            json=strategy_config_payload(
                name="ma_cross_eth",
                params={"fast_window": 7, "slow_window": 20},
            ),
        )
        IsolatedStrategyConfigRepository.fail_next_create = True
        failed_create_resp = await client.post(
            "/api/strategies/configs",
            json=strategy_config_payload(name="ma_cross_create_failure"),
        )
        IsolatedStrategyConfigRepository.fail_next_create = True
        failed_clone_resp = await client.post(
            "/api/strategies/configs/ma_cross_eth/clone",
            json={"target_name": "ma_cross_clone_failure", "overrides": {}},
        )
        IsolatedStrategyConfigRepository.fail_next_delete = True
        failed_delete_resp = await client.delete("/api/strategies/configs/ma_cross_eth")

    assert disabled_start_resp.status_code == 409
    assert disabled_start_resp.json()["detail"] == "Strategy config is disabled"
    assert created_engines == []
    for response in (
        active_update_resp,
        active_disable_resp,
        active_delete_resp,
        starting_update_resp,
        starting_delete_resp,
    ):
        assert response.status_code == 409
        assert response.json()["detail"] == "Strategy is active"
    for response in (open_update_resp, open_disable_resp):
        assert response.status_code == 409
        assert response.json()["detail"] == "Cannot update strategy config with open positions"
    assert open_delete_resp.status_code == 409
    assert open_delete_resp.json()["detail"] == "Cannot delete strategy config with open positions"
    assert zero_update_resp.status_code == 200
    assert active_source_clone_resp.status_code == 201
    assert active_source_clone_resp.json()["enabled"] is False
    assert clone_resp.status_code == 201
    assert clone_resp.json()["enabled"] is False
    assert invalid_override_params_resp.status_code == 422
    assert_strategy_validation_envelope(
        invalid_override_params_resp,
        issue_codes={"invalid_type"},
    )
    invalid_override_params_issue = invalid_override_params_resp.json()["detail"]["issues"][0]
    assert invalid_override_params_issue["path"] == "overrides.params"
    assert invalid_override_params_issue["code"] == "invalid_type"
    assert invalid_override_params_issue["line"] is None
    assert invalid_override_params_issue["column"] is None
    for response in (create_active_target_resp, clone_active_target_resp):
        assert response.status_code == 409
        assert response.json()["detail"] == "Strategy is active"
    assert unknown_clone_field_resp.status_code == 422
    assert_strategy_validation_envelope(unknown_override_resp, issue_codes={"unknown_field"})
    for response, expected_detail, unsafe_detail in (
        (failed_update_resp, "Failed to update strategy config", "update failed"),
        (failed_create_resp, "Failed to create strategy config", "create failed"),
        (failed_clone_resp, "Failed to clone strategy config", "create failed"),
        (failed_delete_resp, "Failed to delete strategy config", "delete failed"),
    ):
        assert response.status_code == 500
        assert response.json()["detail"] == expected_detail
        assert unsafe_detail not in response.text
    assert IsolatedStrategyConfigRepository.configs["ma_cross_eth"].params["fast_window"] == 6
    assert [message["type"] for message in messages] == [
        "strategy_config_updated",
        "strategy_config_created",
        "strategy_config_created",
    ]


@pytest.mark.asyncio
async def test_config_delete_event_timestamp_is_captured_before_delayed_broadcast(monkeypatch):
    class DelayingLifecycleLock:
        def __init__(self, lock):
            self.lock = lock

        async def __aenter__(self):
            await self.lock.acquire()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self.lock.release()
            if DelayDeleteRepository.delay_after_delete:
                DelayDeleteRepository.delay_after_delete = False
                delete_lock_released.set()
                await release_delete_broadcast.wait()
            return False

    class DelayingRuntime(strategy_api.StrategyRuntimeState):
        def lifecycle_lock(self, name):
            lock = self.lifecycle_locks.get(name)
            if lock is None:
                lock = DelayingLifecycleLock(asyncio.Lock())
                self.lifecycle_locks[name] = lock
            return lock

    class DelayDeleteRepository(IsolatedStrategyConfigRepository):
        delay_after_delete = False

        def delete_strategy_config(self, name):
            deleted = super().delete_strategy_config(name)
            if deleted:
                self.__class__.delay_after_delete = True
            return deleted

    timestamps = iter([1000, 2000, 3000, 4000, 5000])
    monkeypatch.setattr(strategy_api, "Repository", DelayDeleteRepository, raising=False)
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: next(timestamps))
    DelayDeleteRepository.configs = {}
    DelayDeleteRepository.positions = {}
    delete_lock_released = asyncio.Event()
    release_delete_broadcast = asyncio.Event()
    messages = []

    async def broadcast(message):
        messages.append(message)

    app = FastAPI()
    app.include_router(
        strategy_api.create_router(broadcast=broadcast, runtime=DelayingRuntime()),
        prefix="/api/strategies",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        initial_resp = await client.post("/api/strategies/configs", json=strategy_config_payload())
        delete_task = asyncio.create_task(client.delete("/api/strategies/configs/ma_cross_btc"))
        await asyncio.wait_for(delete_lock_released.wait(), timeout=1)
        recreate_resp = await client.post("/api/strategies/configs", json=strategy_config_payload())
        release_delete_broadcast.set()
        delete_resp = await asyncio.wait_for(delete_task, timeout=1)

    assert initial_resp.status_code == 201
    assert recreate_resp.status_code == 201
    assert delete_resp.status_code == 204
    delete_message = next(
        message for message in messages if message["type"] == "strategy_config_deleted"
    )
    recreate_message = messages[-2]
    assert recreate_message["type"] == "strategy_config_created"
    assert delete_message["timestamp"] == 2000
    assert recreate_message["timestamp"] == 3000
    assert delete_message["timestamp"] < recreate_message["timestamp"]


@pytest.mark.asyncio
async def test_strategy_lifecycle_locks_are_independent_by_name():
    runtime = strategy_api.StrategyRuntimeState()
    first_lock = runtime.lifecycle_lock("ma_cross_btc")
    second_lock = runtime.lifecycle_lock("ma_cross_eth")

    async with first_lock:
        await asyncio.wait_for(second_lock.acquire(), timeout=0.01)
        second_lock.release()
        same_name_acquired = asyncio.Event()

        async def acquire_same_name_lock():
            async with runtime.lifecycle_lock("ma_cross_btc"):
                same_name_acquired.set()

        same_name_task = asyncio.create_task(acquire_same_name_lock())
        await asyncio.sleep(0)
        assert not same_name_acquired.is_set()

    await asyncio.wait_for(same_name_task, timeout=0.01)
    assert same_name_acquired.is_set()


@pytest.mark.asyncio
async def test_strategy_start_rechecks_config_after_lifecycle_lock(monkeypatch):
    class VanishingRepository(IsolatedStrategyConfigRepository):
        lookups = 0

        def get_strategy_config(self, name):
            self.__class__.lookups += 1
            if self.lookups == 1:
                return StrategyConfigRecord(
                    **strategy_config_payload(name=name), created_at=1, updated_at=1
                )
            return None

    VanishingRepository.configs = {}
    VanishingRepository.positions = {}
    VanishingRepository.lookups = 0
    monkeypatch.setattr(strategy_api, "Repository", VanishingRepository, raising=False)
    app = FastAPI()
    app.include_router(strategy_api.create_router(), prefix="/api/strategies")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/strategies/vanishing/start")

    assert response.status_code == 404
    assert response.json()["detail"] == "Strategy not found"


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


def test_create_order_manager_maps_paper_runtime_to_demo_router(monkeypatch):
    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: AppConfig(mode="paper"),
    )

    manager = strategy_api.create_order_manager()

    assert manager.router.mode == "demo"
    assert manager.router.demo is not None
    assert manager.router.backtest is None
    assert manager.post_live_order_sync is None


def test_create_order_manager_maps_demo_runtime_to_safe_demo_router(monkeypatch):
    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: AppConfig(mode="demo"),
    )

    manager = strategy_api.create_order_manager()

    assert manager.router.mode == "demo"
    assert isinstance(manager.router.demo, strategy_api.LocalPaperOrderHandler)
    assert manager.router.live is None
    assert manager.router.backtest is None
    assert manager.post_live_order_sync is None


def test_create_order_manager_maps_explicit_paper_to_demo_router(monkeypatch):
    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: AppConfig(mode="live"),
    )

    manager = strategy_api.create_order_manager(order_router_mode="paper")

    assert manager.router.mode == "demo"
    assert manager.router.demo is not None
    assert manager.router.live is None
    assert manager.router.backtest is None
    assert manager.post_live_order_sync is None


def test_create_order_manager_rejects_live_missing_credentials_before_dependencies(
    monkeypatch,
):
    constructed: list[str] = []

    class SentinelRepository:
        def __init__(self):
            constructed.append("repository")

    class SentinelHandler:
        def __init__(self, *args, **kwargs):
            constructed.append("handler")

    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: AppConfig(
            mode="live",
            exchange=ExchangeConfig(api_key="key", secret="", passphrase="pass"),
        ),
    )
    monkeypatch.setattr(strategy_api, "Repository", SentinelRepository)
    monkeypatch.setattr(strategy_api, "LocalPaperOrderHandler", SentinelHandler)

    with pytest.raises(
        ValueError,
        match="Live trading requires OKX api_key, secret, and passphrase",
    ):
        strategy_api.create_order_manager(order_router_mode="live")

    assert constructed == []


@pytest.mark.parametrize(
    ("market_type", "adapter_name", "default_type"),
    [
        ("spot", "OKXSpotAdapter", "spot"),
        ("swap", "OKXSwapAdapter", "swap"),
        ("future", "OKXFuturesAdapter", "future"),
        ("futures", "OKXFuturesAdapter", "future"),
        ("option", "OKXOptionsAdapter", "option"),
        ("options", "OKXOptionsAdapter", "option"),
    ],
)
def test_create_order_manager_live_selects_okx_adapter(
    monkeypatch,
    market_type,
    adapter_name,
    default_type,
):
    constructed: list[tuple[str, str, str, str, bool]] = []

    class SentinelAdapter(strategy_api.LocalPaperOrderHandler):
        def __init__(self, api_key, secret, passphrase, demo=True):
            constructed.append((adapter_name, api_key, secret, passphrase, demo))

    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: AppConfig(
            mode="live",
            exchange=ExchangeConfig(
                api_key="key",
                secret="secret",
                passphrase="pass",
                market_type=market_type,
                demo=True,
            ),
            risk=RiskConfig(
                allow_live_open_orders=True,
                live_max_order_notional=1234.0,
            ),
        ),
    )
    monkeypatch.setattr(strategy_api, adapter_name, SentinelAdapter)

    manager = strategy_api.create_order_manager(order_router_mode="live")

    assert manager.router.mode == "live"
    assert manager.router.live is not None
    assert manager.router.demo is None
    assert manager.router.backtest is None
    assert manager.post_live_order_sync is not None
    assert manager.allow_live_open_orders is True
    assert manager.live_max_order_notional == 1234.0
    assert constructed == [(adapter_name, "key", "secret", "pass", True)]


@pytest.mark.asyncio
async def test_create_order_manager_live_syncs_with_router_adapter(monkeypatch):
    class SentinelAdapter(strategy_api.LocalPaperOrderHandler):
        def __init__(self, api_key, secret, passphrase, demo=True):
            pass

    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: AppConfig(
            mode="live",
            exchange=ExchangeConfig(
                api_key="key",
                secret="secret",
                passphrase="pass",
                market_type="swap",
                demo=True,
            ),
        ),
    )
    monkeypatch.setattr(strategy_api, "OKXSwapAdapter", SentinelAdapter)
    calls = []

    async def fake_sync_private_state(repository, adapter, **kwargs):
        calls.append((repository, adapter, kwargs))

    monkeypatch.setattr(strategy_api, "sync_private_state", fake_sync_private_state)
    repository = object()

    async def on_risk_event(payload):
        pass

    manager = strategy_api.create_order_manager(
        repository=repository,
        on_risk_event=on_risk_event,
        order_router_mode="live",
    )

    await manager.post_live_order_sync("ma_cross", "BTC-USDT-SWAP")

    assert calls == [
        (
            repository,
            manager.router.live,
            {
                "symbols": ["BTC-USDT-SWAP"],
                "timestamp_ms": strategy_api.current_timestamp_ms,
                "risk_event_notifier": on_risk_event,
            },
        )
    ]


@pytest.mark.parametrize(
    ("mode", "detail"),
    [
        ("shadow", "Unsupported strategy runtime mode: shadow"),
    ],
)
def test_create_order_manager_rejects_explicit_unsafe_mode_before_dependencies(
    monkeypatch,
    mode,
    detail,
):
    constructed: list[str] = []

    class SentinelRepository:
        def __init__(self):
            constructed.append("repository")

    class SentinelHandler:
        def __init__(self, *args, **kwargs):
            constructed.append("handler")

    def sentinel_backtest_config():
        constructed.append("backtest_config")
        return strategy_api.BacktestConfig()

    def sentinel_risk_manager():
        constructed.append("risk_manager")
        raise AssertionError("Risk manager should not be constructed")

    monkeypatch.setattr(strategy_api, "Repository", SentinelRepository)
    monkeypatch.setattr(strategy_api, "LocalPaperOrderHandler", SentinelHandler)
    monkeypatch.setattr(strategy_api, "paper_backtest_config", sentinel_backtest_config)
    monkeypatch.setattr(strategy_api, "create_risk_manager", sentinel_risk_manager)

    with pytest.raises(ValueError, match=detail):
        strategy_api.create_order_manager(order_router_mode=mode)

    assert constructed == []


@pytest.mark.parametrize(
    ("mode", "detail"),
    [
        (None, "Unsupported strategy runtime mode: None"),
        (42, "Unsupported strategy runtime mode: 42"),
        (["paper"], "Unsupported strategy runtime mode: ['paper']"),
    ],
)
def test_create_order_manager_rejects_explicit_malformed_mode_before_dependencies(
    monkeypatch,
    mode,
    detail,
):
    constructed: list[str] = []

    class SentinelRepository:
        def __init__(self):
            constructed.append("repository")

    class SentinelHandler:
        def __init__(self, *args, **kwargs):
            constructed.append("handler")

    def sentinel_backtest_config():
        constructed.append("backtest_config")
        return strategy_api.BacktestConfig()

    def sentinel_risk_manager():
        constructed.append("risk_manager")
        raise AssertionError("Risk manager should not be constructed")

    monkeypatch.setattr(strategy_api, "Repository", SentinelRepository)
    monkeypatch.setattr(strategy_api, "LocalPaperOrderHandler", SentinelHandler)
    monkeypatch.setattr(strategy_api, "paper_backtest_config", sentinel_backtest_config)
    monkeypatch.setattr(strategy_api, "create_risk_manager", sentinel_risk_manager)

    with pytest.raises(ValueError) as exc_info:
        strategy_api.create_order_manager(order_router_mode=mode)

    assert str(exc_info.value) == detail
    assert constructed == []


@pytest.mark.asyncio
async def test_refresh_live_state_endpoint_requires_live_mode(monkeypatch, app):
    monkeypatch.setattr(
        "src.web.api.trading.load_runtime_settings",
        lambda: AppConfig(mode="paper"),
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/trading/live-state/refresh", params={"strategy": "ma_cross"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Live state refresh requires live mode"


@pytest.mark.asyncio
async def test_refresh_live_state_endpoint_persists_and_returns_state(monkeypatch, app):
    account = AccountRecord(
        strategy="ma_cross",
        initial_equity=1000.0,
        cash_balance=950.0,
        equity=1000.0,
        realized_pnl=0.0,
        unrealized_pnl=50.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    position = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        mark_price=51000.0,
        unrealized_pnl=1000.0,
    )
    calls = []

    async def fake_refresh(exchange, repository, strategy, symbols, timestamp_ms):
        calls.append((exchange.market_type, strategy, symbols))
        return LiveStateSyncResult(account=account, positions=[position])

    monkeypatch.setattr(
        "src.web.api.trading.load_runtime_settings",
        lambda: AppConfig(
            mode="live",
            exchange=ExchangeConfig(
                api_key="key",
                secret="secret",
                passphrase="pass",
                market_type="swap",
                demo=True,
            ),
        ),
    )
    monkeypatch.setattr("src.web.api.trading.refresh_okx_live_state", fake_refresh)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/trading/live-state/refresh",
            params={"strategy": "ma_cross", "symbol": "BTC-USDT-SWAP"},
        )

    assert resp.status_code == 200
    assert calls == [("swap", "ma_cross", ["BTC-USDT-SWAP"])]
    payload = resp.json()
    assert payload["account"]["equity"] == 1000.0
    assert payload["positions"][0]["symbol"] == "BTC-USDT-SWAP"


@pytest.mark.asyncio
async def test_start_strategy_refreshes_live_state_before_order_manager(monkeypatch):
    class FakeRepository:
        def get_strategy_configs(self):
            return []

        def get_strategy_config(self, name):
            return None

    class LiveStrategy(BaseStrategy):
        name = "live_test"
        symbol = "BTC-USDT-SWAP"

        async def on_bar(self, bar):
            pass

    class FakeBotEngine:
        def __init__(self, *args, **kwargs):
            events.append("engine_init")

        async def start(self):
            events.append("engine_start")

        async def stop(self):
            pass

    account = AccountRecord(
        strategy="live_test",
        initial_equity=1000.0,
        cash_balance=950.0,
        equity=1000.0,
        realized_pnl=0.0,
        unrealized_pnl=50.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    events = []
    refresh_calls = []
    order_manager_repositories = []

    async def fake_refresh(exchange, repository, strategy, symbols, timestamp_ms):
        events.append("refresh")
        refresh_calls.append((exchange.market_type, repository, strategy, symbols, timestamp_ms()))
        return LiveStateSyncResult(account=account, positions=[])

    def fake_create_order_manager(**kwargs):
        events.append("order_manager")
        order_manager_repositories.append(kwargs["repository"])
        return object()

    registry = StrategyRegistry()
    registry.register("live_test", LiveStrategy)
    runtime = strategy_api.StrategyRuntimeState(registry=registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000)
    monkeypatch.setattr(strategy_api, "refresh_okx_live_state", fake_refresh)
    monkeypatch.setattr(strategy_api, "create_order_manager", fake_create_order_manager)
    monkeypatch.setattr(strategy_api, "create_market_data_service", lambda: object())
    monkeypatch.setattr(strategy_api, "BotEngine", FakeBotEngine)
    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: AppConfig(
            mode="live",
            exchange=ExchangeConfig(
                api_key="key",
                secret="secret",
                passphrase="pass",
                market_type="swap",
                demo=True,
            ),
        ),
    )
    app = FastAPI()
    app.include_router(strategy_api.create_router(runtime=runtime), prefix="/api/strategies")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/live_test/start")

    assert resp.status_code == 200
    assert events == ["refresh", "order_manager", "engine_init", "engine_start"]
    assert refresh_calls == [
        (
            "swap",
            order_manager_repositories[0],
            "live_test",
            ["BTC-USDT-SWAP"],
            1700000000000,
        )
    ]


@pytest.mark.asyncio
async def test_start_strategy_rejects_live_missing_credentials_before_creating_runtime_services(
    monkeypatch,
):
    created_market_data_services = 0
    created_bot_engines = 0

    def create_market_data_service():
        nonlocal created_market_data_services
        created_market_data_services += 1
        raise AssertionError("MarketDataService should not be created for live mode")

    class SentinelBotEngine:
        def __init__(self, *args, **kwargs):
            nonlocal created_bot_engines
            created_bot_engines += 1
            raise AssertionError("BotEngine should not be created for live mode")

    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: AppConfig(mode="live"),
    )
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        create_market_data_service,
    )
    monkeypatch.setattr(strategy_api, "BotEngine", SentinelBotEngine)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/ma_cross/start")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Live trading requires OKX api_key, secret, and passphrase"
    assert created_market_data_services == 0
    assert created_bot_engines == 0


@pytest.mark.asyncio
async def test_start_strategy_rejects_unknown_runtime_mode(monkeypatch):
    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: AppConfig(mode="shadow"),
    )

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/ma_cross/start")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unsupported strategy runtime mode: shadow"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "detail"),
    [
        ("shadow", "Unsupported strategy runtime mode: shadow"),
        (["paper"], "Unsupported strategy runtime mode: ['paper']"),
    ],
)
async def test_start_strategy_rejects_unsafe_runtime_mode_before_strategy_lookup(
    monkeypatch,
    mode,
    detail,
):
    class SentinelRepository:
        def get_strategy_config(self, name):
            raise AssertionError("Strategy lookup should not run for unsafe runtime modes")

    class RuntimeSettings:
        pass

    settings = RuntimeSettings()
    settings.mode = mode
    runtime = strategy_api.StrategyRuntimeState()
    monkeypatch.setattr(strategy_api, "Repository", lambda: SentinelRepository())
    monkeypatch.setattr(strategy_api, "load_runtime_settings", lambda: settings)
    app = FastAPI()
    app.include_router(strategy_api.create_router(runtime=runtime), prefix="/api/strategies")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/unknown_strategy/start")

    assert resp.status_code == 400
    assert resp.json()["detail"] == detail


@pytest.mark.asyncio
async def test_start_strategy_rejects_malformed_runtime_mode_before_creating_runtime_services(
    monkeypatch,
):
    created_market_data_services = 0
    created_bot_engines = 0

    class MalformedRuntimeSettings:
        mode = ["paper"]

    def create_market_data_service():
        nonlocal created_market_data_services
        created_market_data_services += 1
        raise AssertionError("MarketDataService should not be created for malformed mode")

    class SentinelBotEngine:
        def __init__(self, *args, **kwargs):
            nonlocal created_bot_engines
            created_bot_engines += 1
            raise AssertionError("BotEngine should not be created for malformed mode")

    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: MalformedRuntimeSettings(),
    )
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        create_market_data_service,
    )
    monkeypatch.setattr(strategy_api, "BotEngine", SentinelBotEngine)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/ma_cross/start")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unsupported strategy runtime mode: ['paper']"
    assert created_market_data_services == 0
    assert created_bot_engines == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "detail"),
    [
        ("shadow", "Unsupported strategy runtime mode: shadow"),
    ],
)
async def test_start_strategy_rejects_unsafe_mode_when_engine_already_running(
    monkeypatch,
    mode,
    detail,
):
    runtime = strategy_api.StrategyRuntimeState()
    runtime.engines["ma_cross"] = object()
    runtime.strategy_status["ma_cross"] = "running"
    app = FastAPI()
    app.include_router(strategy_api.create_router(runtime=runtime), prefix="/api/strategies")
    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: AppConfig(mode=mode),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/ma_cross/start")

    assert resp.status_code == 400
    assert resp.json()["detail"] == detail
    assert runtime.strategy_status["ma_cross"] == "running"


def test_create_risk_manager_without_settings_uses_max_position_only(monkeypatch):
    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: AppConfig(),
    )

    manager = strategy_api.create_risk_manager()

    assert manager.enforce_daily_loss is False
    assert manager.enforce_drawdown is False


@pytest.mark.asyncio
async def test_strategy_config_api_rejects_unsupported_strategy_type(monkeypatch):
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
    detail = resp.json()["detail"]
    assert detail["code"] == "strategy_validation_failed"
    assert detail["message"] == "Strategy configuration is invalid"
    assert detail["issues"][0]["code"] == "unsupported_strategy_type"


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
    assert ConfigurableStrategy.created[0].timeframe == "15m"
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
    assert ConfigurableStrategy.created[0].timeframe == "1h"
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
            self.latest_bar = Bar(1, 50000.0, 50000.0, 50000.0, 50000.0, 1.0)
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            self.callbacks.append(callback)

        def get_recent_bars(self, symbol, timeframe, count=1):
            if self.latest_bar is None:
                return []
            return [self.latest_bar]

        async def start(self):
            self._running = True
            for callback in list(self.callbacks):
                await callback(self.latest_bar)

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

        def upsert_order(self, order):
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
        assert start_resp.status_code == 200
        assert FakeRepository.orders == []

        live_bar = Bar(1700003600000, 50000.0, 50000.0, 50000.0, 50000.0, 1.0)
        market_data.latest_bar = live_bar
        await market_data.callbacks[0](live_bar)

    assert len(FakeRepository.orders) == 1
    assert len(FakeRepository.trades) == 1
    assert FakeRepository.orders[0].status == "filled"
    assert FakeRepository.orders[0].fill_price == 50000.0
    assert FakeRepository.trades[0].price == 50000.0


@pytest.mark.asyncio
async def test_runtime_risk_rejection_broadcasts_risk_event_before_trading_updates(monkeypatch):
    class FakeMarketDataService:
        def __init__(self):
            self.callbacks = []
            self.ready_bars = [Bar(1, 50000.0, 50000.0, 50000.0, 50000.0, 1.0)]
            self.latest_bar = None
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            self.callbacks.append(callback)

        def get_recent_bars(self, symbol, timeframe, count=1):
            bars = self.ready_bars + ([] if self.latest_bar is None else [self.latest_bar])
            return bars[-count:]

        async def wait_until_ready(self, symbol, timeframe, *, min_bars=1):
            if len(self.get_recent_bars(symbol, timeframe, count=min_bars)) < min_bars:
                raise TimeoutError

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
        risk_events = []

        def get_strategy_config(self, name):
            if name == config.name:
                return config
            return None

        def get_strategy_configs(self):
            return [config]

        def upsert_order(self, order):
            self.orders.append(order)
            return order

        def save_risk_event(self, event):
            self.risk_events.append(event)
            return event

        def get_orders(self):
            return self.orders

        def get_account(self, strategy=None):
            return None

        def get_position(self, strategy, symbol):
            return None

        def get_open_positions(self, strategy=None):
            return []

    FakeRepository.orders = []
    FakeRepository.risk_events = []
    market_data = FakeMarketDataService()
    messages = []

    class FakeRiskEventNotifier:
        def __init__(self):
            self.payloads = []

        async def send_risk_event(self, payload):
            self.payloads.append(payload)

    risk_event_notifier = FakeRiskEventNotifier()

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
    monkeypatch.setattr(
        strategy_api,
        "create_risk_event_notifier",
        lambda: risk_event_notifier,
        raising=False,
    )
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
    assert FakeRepository.risk_events == [messages[0]]
    assert risk_event_notifier.payloads == [messages[0]]
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
            self.ready_bars = [Bar(1, 50000.0, 50000.0, 50000.0, 50000.0, 1.0)]
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            self.callbacks.append(callback)

        def get_recent_bars(self, symbol, timeframe, count=1):
            return self.ready_bars[-count:]

        async def wait_until_ready(self, symbol, timeframe, *, min_bars=1):
            if len(self.get_recent_bars(symbol, timeframe, count=min_bars)) < min_bars:
                raise TimeoutError

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
                "available_balance": 0.0,
                "equity": 100100.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 100.0,
                "daily_pnl": 0.0,
                "fees_paid": 0.0,
                "assets": [],
            },
        },
    ]


@pytest.mark.asyncio
async def test_runtime_bar_without_open_position_does_not_create_account_or_broadcast(monkeypatch):
    class FakeMarketDataService:
        def __init__(self):
            self.callbacks = []
            self.ready_bars = [Bar(1, 50000.0, 50000.0, 50000.0, 50000.0, 1.0)]
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            self.callbacks.append(callback)

        def get_recent_bars(self, symbol, timeframe, count=1):
            return self.ready_bars[-count:]

        async def wait_until_ready(self, symbol, timeframe, *, min_bars=1):
            if len(self.get_recent_bars(symbol, timeframe, count=min_bars)) < min_bars:
                raise TimeoutError

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
            self.ready_bars = [Bar(1, 50000.0, 50000.0, 50000.0, 50000.0, 1.0)]
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            self.subscription = (symbol, timeframe)
            self.callbacks.append(callback)

        def unsubscribe(self, symbol, timeframe, callback):
            if self.subscription == (symbol, timeframe):
                self.callbacks = [
                    existing for existing in self.callbacks if existing is not callback
                ]
                if not self.callbacks:
                    self.subscription = None

        def get_recent_bars(self, symbol, timeframe, count=1):
            return self.ready_bars[-count:]

        async def wait_until_ready(self, symbol, timeframe, *, min_bars=1):
            if len(self.get_recent_bars(symbol, timeframe, count=min_bars)) < min_bars:
                raise TimeoutError

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
    assert market_data.subscription is None
    assert market_data.callbacks == []
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
async def test_start_and_stop_strategy(app, deny_network_requests):
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
    assert deny_network_requests == []


@pytest.mark.asyncio
async def test_stop_strategy_failure_preserves_running_owner_and_status(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []

    class StopFailureEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            self.market_data_service = kwargs["market_data_service"]
            created_engines.append(self)

        async def start(self):
            self.running = True
            self.market_data_service._running = True

        async def stop(self):
            self.stop_calls += 1
            if self.stop_calls == 1:
                raise RuntimeError("stop failed")
            self.running = False

    monkeypatch.setattr(strategy_api, "BotEngine", StopFailureEngine)
    transport = ASGITransport(app=environment.app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_response = await client.post("/api/strategies/ma_cross/start")
        failed_stop_response = await client.post("/api/strategies/ma_cross/stop")

    engine = created_engines[0]
    assert start_response.status_code == 200
    assert failed_stop_response.status_code == 500
    assert environment.runtime.engines["ma_cross"] is engine
    assert environment.runtime.strategy_status["ma_cross"] == "running"
    assert engine.running is True
    assert engine.stop_calls == 1
    assert environment.market_data.stop_calls == 0
    assert environment.market_data._running is True
    assert not any(message.get("status") == "stopped" for message in environment.messages)


@pytest.mark.asyncio
async def test_stop_strategy_retries_same_owner_after_failure(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
):
    environment = hermetic_persisted_builtin_strategy_api
    created_engines = []

    class RetryStopEngine:
        def __init__(self, *args, **kwargs):
            self.stop_calls = 0
            self.running = False
            self.market_data_service = kwargs["market_data_service"]
            created_engines.append(self)

        async def start(self):
            self.running = True
            self.market_data_service._running = True

        async def stop(self):
            self.stop_calls += 1
            if self.stop_calls == 1:
                raise RuntimeError("stop failed")
            self.running = False

    monkeypatch.setattr(strategy_api, "BotEngine", RetryStopEngine)
    transport = ASGITransport(app=environment.app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_response = await client.post("/api/strategies/ma_cross/start")
        failed_stop_response = await client.post("/api/strategies/ma_cross/stop")
        successful_stop_response = await client.post("/api/strategies/ma_cross/stop")

    engine = created_engines[0]
    assert start_response.status_code == 200
    assert failed_stop_response.status_code == 500
    assert successful_stop_response.status_code == 200
    assert successful_stop_response.json() == {
        "status": "stopped",
        "strategy": "ma_cross",
    }
    assert environment.runtime.engines.get("ma_cross") is None
    assert environment.runtime.strategy_status["ma_cross"] == "stopped"
    assert engine.running is False
    assert engine.stop_calls == 2
    assert environment.market_data.stop_calls == 1
    assert environment.market_data._running is False
    assert sum(message.get("status") == "stopped" for message in environment.messages) == 1


@pytest.mark.asyncio
async def test_start_strategy_clears_stale_error_before_successful_restart(monkeypatch):
    class FakeRepository:
        def get_strategy_configs(self):
            return []

        def get_strategy_config(self, name):
            return None

    class PassiveStrategy(BaseStrategy):
        name = "passive"

        async def on_bar(self, bar):
            pass

    engine_kwargs = []

    class FakeBotEngine:
        async def start(self):
            pass

        async def stop(self):
            pass

        def __init__(self, *args, **kwargs):
            engine_kwargs.append(kwargs)

    registry = StrategyRegistry()
    registry.register("passive", PassiveStrategy)
    runtime = strategy_api.StrategyRuntimeState(registry=registry)
    runtime.strategy_errors["passive"] = "old startup failure"
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "create_market_data_service", lambda: object())
    monkeypatch.setattr(strategy_api, "create_order_manager", lambda **kwargs: object())
    monkeypatch.setattr(strategy_api, "BotEngine", FakeBotEngine)
    app = FastAPI()
    app.include_router(strategy_api.create_router(runtime=runtime), prefix="/api/strategies")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/passive/start")

    assert resp.status_code == 200
    assert resp.json() == {"status": "started", "strategy": "passive"}
    assert runtime.strategy_errors == {}
    assert list(runtime.engines) == ["passive"]
    assert runtime.strategy_status["passive"] == "running"
    assert "before_live_strategy_bar" in engine_kwargs[0]
    assert "before_strategy_bar" not in engine_kwargs[0]


@pytest.mark.asyncio
async def test_concurrent_start_strategy_requests_create_one_engine(monkeypatch):
    class FakeRepository:
        def get_strategy_configs(self):
            return []

        def get_strategy_config(self, name):
            return None

    class PassiveStrategy(BaseStrategy):
        name = "passive"

        async def on_bar(self, bar):
            pass

    created_engines = []
    start_calls = 0

    class FakeBotEngine:
        def __init__(self, *args, **kwargs):
            created_engines.append(self)

        async def start(self):
            nonlocal start_calls
            start_calls += 1
            if start_calls == 1:
                await asyncio.sleep(0.05)

        async def stop(self):
            pass

    registry = StrategyRegistry()
    registry.register("passive", PassiveStrategy)
    runtime = strategy_api.StrategyRuntimeState(registry=registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "create_market_data_service", lambda: object())
    monkeypatch.setattr(strategy_api, "create_order_manager", lambda **kwargs: object())
    monkeypatch.setattr(strategy_api, "BotEngine", FakeBotEngine)
    app = FastAPI()
    app.include_router(strategy_api.create_router(runtime=runtime), prefix="/api/strategies")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(
            client.post("/api/strategies/passive/start"),
            client.post("/api/strategies/passive/start"),
        )

    assert [response.status_code for response in responses] == [200, 200]
    assert len(created_engines) == 1
    assert start_calls == 1
    assert runtime.engines == {"passive": created_engines[0]}
    assert runtime.strategy_status["passive"] == "running"


@pytest.mark.asyncio
async def test_stale_engine_error_does_not_stop_restarted_strategy(monkeypatch):
    class FakeRepository:
        def get_strategy_configs(self):
            return []

        def get_strategy_config(self, name):
            return None

    class PassiveStrategy(BaseStrategy):
        name = "passive"

        async def on_bar(self, bar):
            pass

    created_engines = []

    class FakeBotEngine:
        def __init__(self, *args, **kwargs):
            self.on_strategy_error = kwargs["on_strategy_error"]
            created_engines.append(self)

        async def start(self):
            pass

        async def stop(self):
            pass

    registry = StrategyRegistry()
    registry.register("passive", PassiveStrategy)
    runtime = strategy_api.StrategyRuntimeState(registry=registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "create_market_data_service", lambda: object())
    monkeypatch.setattr(strategy_api, "create_order_manager", lambda **kwargs: object())
    monkeypatch.setattr(strategy_api, "BotEngine", FakeBotEngine)
    app = FastAPI()
    app.include_router(strategy_api.create_router(runtime=runtime), prefix="/api/strategies")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first_start = await client.post("/api/strategies/passive/start")
        first_stop = await client.post("/api/strategies/passive/stop")
        second_start = await client.post("/api/strategies/passive/start")
        await created_engines[0].on_strategy_error("passive", RuntimeError("old boom"))
        strategies_resp = await client.get("/api/strategies")

    assert first_start.status_code == 200
    assert first_stop.status_code == 200
    assert second_start.status_code == 200
    assert runtime.engines == {"passive": created_engines[1]}
    assert runtime.strategy_errors == {}
    assert {"name": "passive", "status": "running"} in strategies_resp.json()


@pytest.mark.asyncio
async def test_stopping_strategy_does_not_release_market_data_while_another_start_is_in_flight(
    monkeypatch,
):
    class FakeRepository:
        def get_strategy_configs(self):
            return []

        def get_strategy_config(self, name):
            return None

    class FirstStrategy(BaseStrategy):
        name = "first"

        async def on_bar(self, bar):
            pass

    class SecondStrategy(BaseStrategy):
        name = "second"

        async def on_bar(self, bar):
            pass

    class FakeMarketDataService:
        def __init__(self):
            self.stop_count = 0

        async def stop(self):
            self.stop_count += 1

    start_entered = asyncio.Event()
    allow_start_to_finish = asyncio.Event()
    market_data = FakeMarketDataService()

    class FakeBotEngine:
        def __init__(self, strategies, *args, **kwargs):
            self.name = strategies[0].name

        async def start(self):
            if self.name == "second":
                start_entered.set()
                await allow_start_to_finish.wait()

        async def stop(self):
            pass

    registry = StrategyRegistry()
    registry.register("first", FirstStrategy)
    registry.register("second", SecondStrategy)
    runtime = strategy_api.StrategyRuntimeState(registry=registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "create_market_data_service", lambda: market_data)
    monkeypatch.setattr(strategy_api, "create_order_manager", lambda **kwargs: object())
    monkeypatch.setattr(strategy_api, "BotEngine", FakeBotEngine)
    app = FastAPI()
    app.include_router(strategy_api.create_router(runtime=runtime), prefix="/api/strategies")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first_start = await client.post("/api/strategies/first/start")
        second_start_task = asyncio.create_task(client.post("/api/strategies/second/start"))
        await start_entered.wait()
        first_stop = await client.post("/api/strategies/first/stop")
        stop_count_while_second_starting = market_data.stop_count
        allow_start_to_finish.set()
        second_start = await second_start_task
        second_stop = await client.post("/api/strategies/second/stop")

    assert first_start.status_code == 200
    assert first_stop.status_code == 200
    assert second_start.status_code == 200
    assert second_stop.status_code == 200
    assert stop_count_while_second_starting == 0
    assert market_data.stop_count == 1


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
async def test_start_strategy_creates_market_data_service_after_router_creation(monkeypatch):
    class FakeRepository:
        def get_strategy_configs(self):
            return []

        def get_strategy_config(self, name):
            return None

        def get_orders(self):
            return []

        def get_account(self, strategy=None):
            return None

        def get_open_positions(self, strategy=None):
            return []

    class PassiveStrategy(BaseStrategy):
        name = "passive"

        def __init__(self):
            super().__init__()
            self.symbol = "BTC-USDT"
            self.timeframe = "1m"

        async def on_bar(self, bar):
            pass

    class FakeMarketDataService:
        def __init__(self, api_key):
            self.api_key = api_key
            self._running = False

        def subscribe(self, symbol, timeframe, callback):
            pass

        async def wait_until_ready(self, symbol, timeframe, *, timeout=None, min_bars=1):
            pass

        async def start(self):
            self._running = True
            started_api_keys.append(self.api_key)

        async def stop(self):
            self._running = False

    current_api_key = {"value": ""}
    created_api_keys = []
    started_api_keys = []

    def fake_create_market_data_service():
        created_api_keys.append(current_api_key["value"])
        return FakeMarketDataService(current_api_key["value"])

    registry = StrategyRegistry()
    registry.register("passive", PassiveStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        fake_create_market_data_service,
        raising=False,
    )

    app = create_app()
    current_api_key["value"] = "saved-key"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/passive/start")

    assert resp.status_code == 200
    assert created_api_keys == ["saved-key"]
    assert started_api_keys == ["saved-key"]


@pytest.mark.asyncio
async def test_start_strategy_refreshes_market_data_service_after_all_strategies_stop(monkeypatch):
    class FakeRepository:
        def get_strategy_configs(self):
            return []

        def get_strategy_config(self, name):
            return None

        def get_orders(self):
            return []

        def get_account(self, strategy=None):
            return None

        def get_open_positions(self, strategy=None):
            return []

    class PassiveStrategy(BaseStrategy):
        name = "passive"

        def __init__(self):
            super().__init__()
            self.symbol = "BTC-USDT"
            self.timeframe = "1m"

        async def on_bar(self, bar):
            pass

    class FakeMarketDataService:
        def __init__(self, api_key):
            self.api_key = api_key

        def subscribe(self, symbol, timeframe, callback):
            pass

        async def wait_until_ready(self, symbol, timeframe, *, timeout=None, min_bars=1):
            pass

        async def start(self):
            started_api_keys.append(self.api_key)

        async def stop(self):
            stopped_api_keys.append(self.api_key)

    current_api_key = {"value": "first-key"}
    created_api_keys = []
    started_api_keys = []
    stopped_api_keys = []

    def fake_create_market_data_service():
        created_api_keys.append(current_api_key["value"])
        return FakeMarketDataService(current_api_key["value"])

    registry = StrategyRegistry()
    registry.register("passive", PassiveStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        fake_create_market_data_service,
        raising=False,
    )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first_start = await client.post("/api/strategies/passive/start")
        first_stop = await client.post("/api/strategies/passive/stop")
        current_api_key["value"] = "second-key"
        second_start = await client.post("/api/strategies/passive/start")

    assert first_start.status_code == 200
    assert first_stop.status_code == 200
    assert second_start.status_code == 200
    assert created_api_keys == ["first-key", "second-key"]
    assert started_api_keys == ["first-key", "second-key"]
    assert stopped_api_keys == ["first-key"]


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

        def upsert_order(self, order):
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
            "exchange_order_id": "",
            "client_order_id": "",
            "strategy": "buyer",
            "symbol": "BTC-USDT",
            "side": "buy",
            "type": "limit",
            "amount": 0.1,
            "price": 50000.0,
            "status": "filled",
            "fill_price": 50000.0,
            "timestamp": 1700000000000,
            "updated_at": 0,
        }
    ]
    assert [trade.model_dump() for trade in FakeRepository.trades] == [
        {
            "id": None,
            "exchange_trade_id": "",
            "order_id": "",
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
                "available_balance": 0.0,
                "equity": 99997.5,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "daily_pnl": 0.0,
                "fees_paid": 2.5,
                "assets": [],
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
async def test_run_backtest_accepts_persisted_strategy_config(monkeypatch):
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

        def get_strategy_config(self, name):
            if name != "saved_config":
                return None
            return StrategyConfigRecord(
                name="saved_config",
                strategy_type="configured_type",
                symbol="ETH-USDT",
                timeframe="1h",
                params={"amount": 2.5},
                enabled=True,
                created_at=1700000000000,
                updated_at=1700000000000,
            )

        def get_klines(self, symbol, timeframe, start, end):
            return [
                kline
                for kline in self.klines
                if kline.symbol == symbol
                and kline.timeframe == timeframe
                and start <= kline.timestamp <= end
            ]

        def save_backtest_result_with_trades(self, result, trades):
            self.results.append(result)
            return result

    class ConfiguredStrategy(BaseStrategy):
        amount_seen = None

        def __init__(self, symbol, amount):
            super().__init__()
            self.symbol = symbol
            self.__class__.amount_seen = amount

        async def on_bar(self, bar):
            return None

    FakeRepository.results = []
    ConfiguredStrategy.amount_seen = None
    registry = StrategyRegistry()
    registry.register("configured_type", ConfiguredStrategy)
    monkeypatch.setattr(backtest_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(backtest_api, "create_strategy_registry", lambda: registry, raising=False)
    monkeypatch.setattr(backtest_api, "current_timestamp_ms", lambda: 1700007200000, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/backtest/run",
            json={
                "strategy": "saved_config",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "start_time": 1700002800000,
                "end_time": 1700006400000,
                "initial_capital": 100000,
            },
        )

    assert resp.status_code == 200
    assert ConfiguredStrategy.amount_seen == 2.5
    assert FakeRepository.results[0].strategy == "saved_config"


@pytest.mark.asyncio
async def test_run_backtest_uses_cached_bars_and_persists_summary(monkeypatch):
    class FakeRepository:
        results = []
        trades = []
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

        def save_backtest_result_with_trades(self, result, trades):
            self.results.append(result)
            self.trades.extend(trades)
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
    FakeRepository.trades = []
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
    assert len(FakeRepository.trades) == 1
    trade = FakeRepository.trades[0]
    assert trade.result_id == saved.id
    assert trade.symbol == "BTC-USDT"
    assert trade.side == "buy"
    assert trade.amount == 1.0
    assert trade.price == 100.1
    assert trade.fee == 0.05005
    assert trade.pnl == -100.15005
    assert trade.timestamp == 1700002800000


@pytest.mark.asyncio
async def test_run_backtest_fetches_missing_historical_bars_before_running(monkeypatch):
    class FakeRepository:
        results = []
        trades = []
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

        def save_backtest_result_with_trades(self, result, trades):
            self.results.append(result)
            self.trades.extend(trades)
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
    FakeRepository.trades = []
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
    saved = FakeRepository.results[0]
    assert saved.strategy == "fetch_buy_once"
    assert len(FakeRepository.trades) == 1
    trade = FakeRepository.trades[0]
    assert trade.result_id == saved.id
    assert trade.symbol == "BTC-USDT"
    assert trade.side == "buy"
    assert trade.amount == 1.0
    assert trade.price == 100.1
    assert trade.fee == 0.05005
    assert trade.pnl == -100.15005
    assert trade.timestamp == 1700002800000


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
        trades = []

        def get_klines(self, symbol, timeframe, start, end):
            return []

        def save_backtest_result_with_trades(self, result, trades):
            self.results.append(result)
            self.trades.extend(trades)
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

    FakeRepository.results = []
    FakeRepository.trades = []
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
    assert FakeRepository.trades == []


@pytest.mark.asyncio
async def test_run_backtest_rejects_partial_historical_fetch_without_persisting(monkeypatch):
    class FakeRepository:
        results = []
        trades = []
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

        def save_backtest_result_with_trades(self, result, trades):
            self.results.append(result)
            self.trades.extend(trades)
            return result

    class EmptyStrategy(BaseStrategy):
        name = "partial_fetch"

        async def on_bar(self, bar):
            return None

    class PartialAdapter:
        async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
            return [
                Bar(
                    timestamp=1700002800000,
                    open=100,
                    high=101,
                    low=99,
                    close=100,
                    volume=1,
                )
            ]

        async def close(self):
            pass

    FakeRepository.results = []
    FakeRepository.trades = []
    FakeRepository.klines = []
    registry = StrategyRegistry()
    registry.register("partial_fetch", EmptyStrategy)
    monkeypatch.setattr(backtest_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        backtest_api, "OKXSpotAdapter", lambda **kwargs: PartialAdapter(), raising=False
    )
    monkeypatch.setattr(backtest_api, "create_strategy_registry", lambda: registry, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/backtest/run",
            json={
                "strategy": "partial_fetch",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "start_time": 1700002800000,
                "end_time": 1700006400000,
                "initial_capital": 100000,
            },
        )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "insufficient historical data for requested backtest range"
    assert FakeRepository.klines == []
    assert FakeRepository.results == []
    assert FakeRepository.trades == []


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


@pytest.mark.asyncio
async def test_get_backtest_result_detail_returns_result_klines_and_markers(monkeypatch):
    async def fail_ensure_historical_bars(*args, **kwargs):
        raise AssertionError("detail endpoint must not fetch historical bars")

    class FailAdapter:
        def __init__(self, *args, **kwargs):
            raise AssertionError("detail endpoint must not create OKX adapters")

    class FakeRepository:
        def get_backtest_result(self, result_id):
            assert result_id == "bt-detail"
            return BacktestResultRecord(
                id="bt-detail",
                strategy="ma_cross",
                symbol="BTC-USDT",
                timeframe="1h",
                start_time=1700000000000,
                end_time=1700003600000,
                initial_capital=100000.0,
                total_return=0.01,
                sharpe_ratio=1.2,
                max_drawdown=0.03,
                win_rate=0.5,
                total_trades=2,
                created_at=1700007200000,
            )

        def get_klines(self, symbol, timeframe, start, end):
            assert (symbol, timeframe, start, end) == (
                "BTC-USDT",
                "1h",
                1700000000000,
                1700003600000,
            )
            return [
                KlineCache(
                    symbol="BTC-USDT",
                    timeframe="1h",
                    timestamp=1700000000000,
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.5,
                    volume=10.0,
                )
            ]

        def get_backtest_trades(self, result_id):
            assert result_id == "bt-detail"
            return [
                BacktestTradeRecord(
                    result_id="bt-detail",
                    symbol="BTC-USDT",
                    side="buy",
                    timestamp=1700000000000,
                    price=100.0,
                    amount=0.1,
                    fee=0.01,
                    pnl=-10.01,
                )
            ]

    monkeypatch.setattr(backtest_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        backtest_api,
        "ensure_historical_bars",
        fail_ensure_historical_bars,
        raising=False,
    )
    monkeypatch.setattr(backtest_api, "OKXSpotAdapter", FailAdapter, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/backtest/results/bt-detail")

    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"result", "klines", "markers"}
    assert data["result"]["id"] == "bt-detail"
    assert data["result"]["symbol"] == "BTC-USDT"
    assert data["klines"] == [
        {
            "id": None,
            "symbol": "BTC-USDT",
            "timeframe": "1h",
            "timestamp": 1700000000000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10.0,
        }
    ]
    assert data["markers"] == [
        {
            "id": None,
            "result_id": "bt-detail",
            "symbol": "BTC-USDT",
            "side": "buy",
            "timestamp": 1700000000000,
            "price": 100.0,
            "amount": 0.1,
            "fee": 0.01,
            "pnl": -10.01,
        }
    ]


@pytest.mark.asyncio
async def test_get_backtest_result_detail_returns_404_for_missing_result(monkeypatch):
    async def fail_ensure_historical_bars(*args, **kwargs):
        raise AssertionError("detail endpoint must not fetch historical bars")

    class FailAdapter:
        def __init__(self, *args, **kwargs):
            raise AssertionError("detail endpoint must not create OKX adapters")

    class FakeRepository:
        def get_backtest_result(self, result_id):
            assert result_id == "missing"
            return None

        def get_klines(self, *args, **kwargs):
            raise AssertionError("missing detail must not read klines")

        def get_backtest_trades(self, *args, **kwargs):
            raise AssertionError("missing detail must not read markers")

    monkeypatch.setattr(backtest_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        backtest_api,
        "ensure_historical_bars",
        fail_ensure_historical_bars,
        raising=False,
    )
    monkeypatch.setattr(backtest_api, "OKXSpotAdapter", FailAdapter, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/backtest/results/missing")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Backtest result not found"


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
                "available_balance": 0.0,
                "equity": 100100.0,
                "realized_pnl": 100.0,
                "unrealized_pnl": 0.0,
                "daily_pnl": 100.0,
                "fees_paid": 1.5,
                "assets": [],
            },
            "positions": [],
            "orders": [],
            "strategies": [{"name": "ma_cross", "status": "stopped"}],
            "strategy_configs": [],
            "strategy_errors": {},
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
                    "allow_live_open_orders": True,
                    "live_max_order_notional": 2500.0,
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
        "market_type": "spot",
        "demo": True,
    }
    assert saved["backtest"]["initial_capital"] == 250000
    assert saved["risk"]["max_daily_loss_pct"] == 0.03
    assert saved["risk"]["allow_live_open_orders"] is True
    assert saved["risk"]["live_max_order_notional"] == 2500.0
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
        "market_type": "spot",
        "demo": True,
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
                    "allow_live_open_orders": True,
                    "live_max_order_notional": 2500.0,
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
        "market_type": "spot",
        "demo": True,
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
                    "allow_live_open_orders": True,
                    "live_max_order_notional": 2500.0,
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
        "market_type": "spot",
        "demo": True,
    }
    assert saved["backtest"]["initial_capital"] == 300000
    assert saved["risk"]["max_total_position_pct"] == 0.5
    assert saved["risk"]["allow_live_open_orders"] is True
    assert saved["risk"]["live_max_order_notional"] == 2500.0
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
        available_balance = 94000.0
        assets = [{"ccy": "USDT", "avail_bal": 94000.0}]

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
        "available_balance": 94000.0,
        "assets": [{"ccy": "USDT", "avail_bal": 94000.0}],
    }


@pytest.mark.asyncio
async def test_get_account_returns_zero_state_with_assets_when_account_absent(monkeypatch, app):
    class FakeRepository:
        def get_account(self, strategy=None):
            assert strategy is None
            return None

    monkeypatch.setattr(trading_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trading/account")

    assert resp.status_code == 200
    assert resp.json() == {
        "cash_balance": 0.0,
        "equity": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "daily_pnl": 0.0,
        "fees_paid": 0.0,
        "available_balance": 0.0,
        "assets": [],
    }


def test_repository_get_accounts_and_get_account_prefers_exchange_account(tmp_path):
    repo = SqliteRepository(db_path=str(tmp_path / "bot.db"))
    repo.upsert_account(
        AccountRecord(
            strategy="alpha",
            initial_equity=100.0,
            cash_balance=90.0,
            available_balance=80.0,
            equity=110.0,
            realized_pnl=10.0,
            unrealized_pnl=1.0,
            daily_pnl=5.0,
            fees_paid=0.5,
            updated_at=1,
        )
    )
    repo.upsert_account(
        AccountRecord(
            strategy="__exchange__",
            initial_equity=1000.0,
            cash_balance=900.0,
            available_balance=850.0,
            equity=1010.0,
            realized_pnl=10.0,
            unrealized_pnl=20.0,
            daily_pnl=5.0,
            fees_paid=1.0,
            updated_at=3,
            assets=[{"ccy": "USDT", "avail_bal": 850.0}],
        )
    )
    repo.upsert_account(
        AccountRecord(
            strategy="beta",
            initial_equity=200.0,
            cash_balance=180.0,
            available_balance=170.0,
            equity=210.0,
            realized_pnl=20.0,
            unrealized_pnl=2.0,
            daily_pnl=6.0,
            fees_paid=0.7,
            updated_at=2,
        )
    )

    accounts = repo.get_accounts()

    assert len(accounts) == 3
    assert {account.strategy for account in accounts} == {"alpha", "beta", "__exchange__"}
    assert repo.get_account("alpha").cash_balance == 90.0
    account = repo.get_account()
    assert account is not None
    assert account.strategy == "__exchange__"
    assert account.cash_balance == 900.0
    assert account.assets == [{"ccy": "USDT", "avail_bal": 850.0}]


@pytest.mark.asyncio
async def test_get_strategy_performance_returns_aggregated_rows(monkeypatch, app):
    accounts = [
        AccountRecord(
            strategy="alpha",
            initial_equity=100.0,
            cash_balance=90.0,
            available_balance=80.0,
            equity=110.0,
            realized_pnl=10.0,
            unrealized_pnl=1.0,
            daily_pnl=5.0,
            fees_paid=0.5,
            updated_at=1,
        ),
        AccountRecord(
            strategy="__exchange__",
            initial_equity=1000.0,
            cash_balance=900.0,
            available_balance=850.0,
            equity=1010.0,
            realized_pnl=10.0,
            unrealized_pnl=20.0,
            daily_pnl=5.0,
            fees_paid=1.0,
            updated_at=3,
        ),
    ]
    positions = [
        PositionRecord(
            strategy="alpha",
            symbol="BTC-USDT-SWAP",
            side="long",
            amount=2.0,
            entry_price=100.0,
            leverage=1,
            timestamp=10,
            mark_price=120.0,
        ),
        PositionRecord(
            strategy="__exchange__",
            symbol="BTC-USDT-SWAP",
            side="long",
            amount=1.0,
            entry_price=100.0,
            leverage=1,
            timestamp=11,
            mark_price=101.0,
        ),
    ]
    orders = [
        OrderRecord(
            order_id="o-1",
            exchange_order_id="",
            client_order_id="",
            strategy="alpha",
            symbol="BTC-USDT-SWAP",
            side="buy",
            type="market",
            amount=1.0,
            price=100.0,
            status="filled",
            fill_price=101.0,
            timestamp=150,
            updated_at=160,
        ),
        OrderRecord(
            order_id="o-2",
            exchange_order_id="",
            client_order_id="",
            strategy="order_only",
            symbol="ETH-USDT-SWAP",
            side="sell",
            type="limit",
            amount=1.0,
            price=200.0,
            status="open",
            fill_price=0.0,
            timestamp=250,
            updated_at=260,
        ),
        OrderRecord(
            order_id="o-3",
            exchange_order_id="",
            client_order_id="",
            strategy="__exchange__",
            symbol="BTC-USDT-SWAP",
            side="buy",
            type="market",
            amount=1.0,
            price=100.0,
            status="open",
            fill_price=0.0,
            timestamp=50,
            updated_at=50,
        ),
    ]
    trades = [
        TradeRecord(
            exchange_trade_id="t-1",
            order_id="o-1",
            strategy="alpha",
            symbol="BTC-USDT-SWAP",
            side="buy",
            amount=2.0,
            price=100.0,
            fee=1.0,
            timestamp=101,
        ),
        TradeRecord(
            exchange_trade_id="t-2",
            order_id="o-3",
            strategy="__exchange__",
            symbol="BTC-USDT-SWAP",
            side="buy",
            amount=1.0,
            price=100.0,
            fee=1.0,
            timestamp=102,
        ),
    ]

    class FakeRepository:
        def get_accounts(self):
            return accounts

        def get_positions(self, strategy=None):
            assert strategy is None
            return positions

        def get_orders(self):
            return orders

        def get_trades(self, strategy=None):
            assert strategy is None
            return trades

    monkeypatch.setattr(trading_api, "Repository", FakeRepository, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trading/strategy-performance")

    expected = [
        asdict(performance)
        for performance in build_strategy_performance(accounts, positions, orders, trades)
    ]
    assert resp.status_code == 200
    assert resp.json() == expected
    assert [row["strategy"] for row in resp.json()] == ["alpha", "order_only"]
    order_only = resp.json()[1]
    assert order_only["initial_equity"] == 0.0
    assert order_only["return_pct"] is None
    assert order_only["position_notional"] == 0.0
    assert order_only["order_count"] == 1
    assert order_only["filled_order_count"] == 0
    assert order_only["trade_count"] == 0
    assert order_only["win_rate"] is None


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
async def test_market_klines_uses_requested_swap_adapter(monkeypatch, app):
    constructed = []

    class SentinelSwapAdapter:
        def __init__(self, api_key, secret, passphrase, demo=True):
            constructed.append((api_key, secret, passphrase, demo))

        async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
            return [Bar(timestamp=1700000000000, open=1, high=2, low=0.5, close=1.5, volume=10)]

        async def fetch_tickers(self, symbols):
            return []

        async def submit(self, order):
            return order

        async def cancel(self, order_id, symbol=None):
            return True

        async def close(self):
            pass

    monkeypatch.setattr(market_api, "OKXSwapAdapter", SentinelSwapAdapter)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/market/klines",
            params={"market_type": "swap", "symbol": "BTC-USDT-SWAP", "timeframe": "1h"},
        )

    assert resp.status_code == 200
    assert constructed == [("", "", "", True)]
    assert resp.json()[0]["symbol"] == "BTC-USDT-SWAP"


@pytest.mark.asyncio
async def test_market_tickers_accepts_symbols_for_options(monkeypatch, app):
    calls = []

    class SentinelOptionsAdapter:
        def __init__(self, api_key, secret, passphrase, demo=True):
            pass

        async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
            return []

        async def fetch_tickers(self, symbols):
            calls.append(symbols)
            return [{"symbol": symbols[0], "last": 1.0, "bidPx": 0.9, "askPx": 1.1, "vol24h": 2.0}]

        async def submit(self, order):
            return order

        async def cancel(self, order_id, symbol=None):
            return True

        async def close(self):
            pass

    monkeypatch.setattr(market_api, "OKXOptionsAdapter", SentinelOptionsAdapter)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/market/tickers",
            params=[("market_type", "option"), ("symbols", "BTC-USDT-260626-100000-C")],
        )

    assert resp.status_code == 200
    assert calls == [["BTC-USDT-260626-100000-C"]]
    assert resp.json()[0]["symbol"] == "BTC-USDT-260626-100000-C"


@pytest.mark.asyncio
async def test_market_tickers_returns_empty_for_options_without_symbols(monkeypatch, app):
    class UnexpectedOptionsAdapter:
        def __init__(self, api_key, secret, passphrase, demo=True):
            raise AssertionError("empty option defaults must not create an adapter")

    monkeypatch.setattr(market_api, "OKXOptionsAdapter", UnexpectedOptionsAdapter)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/market/tickers", params={"market_type": "option"})

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_market_klines_rejects_empty_symbol(app):
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/market/klines",
            params={"market_type": "swap", "symbol": "", "timeframe": "1h"},
        )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "symbol must not be empty"


@pytest.mark.asyncio
async def test_market_klines_with_time_range_returns_cached_rows(monkeypatch, app):
    events = []

    class FakeRepository:
        def __init__(self) -> None:
            events.append(("repo_init",))

        def get_klines(self, symbol: str, timeframe: str, start: int, end: int):
            events.append(("get_klines", symbol, timeframe, start, end))
            return [
                KlineCache(
                    symbol="ETH-USDT",
                    timeframe="1h",
                    timestamp=1700000000000,
                    open=1.1,
                    high=1.3,
                    low=1.0,
                    close=1.2,
                    volume=10.5,
                ),
                KlineCache(
                    symbol="ETH-USDT",
                    timeframe="1h",
                    timestamp=1700003600000,
                    open=1.2,
                    high=1.4,
                    low=1.1,
                    close=1.3,
                    volume=11.5,
                ),
            ]

    class UnexpectedOKXSpotAdapter:
        def __init__(self, api_key: str, secret: str, passphrase: str) -> None:
            raise AssertionError("explicit kline ranges must use cache")

    monkeypatch.setattr(market_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(market_api, "OKXSpotAdapter", UnexpectedOKXSpotAdapter, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/market/klines"
            "?symbol=ETH-USDT&timeframe=1h&start_time=1700000000000&end_time=1700003600000"
        )

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "symbol": "ETH-USDT",
            "timeframe": "1h",
            "timestamp": 1700000000000,
            "open": 1.1,
            "high": 1.3,
            "low": 1.0,
            "close": 1.2,
            "volume": 10.5,
        },
        {
            "symbol": "ETH-USDT",
            "timeframe": "1h",
            "timestamp": 1700003600000,
            "open": 1.2,
            "high": 1.4,
            "low": 1.1,
            "close": 1.3,
            "volume": 11.5,
        },
    ]
    assert events == [
        ("repo_init",),
        ("get_klines", "ETH-USDT", "1h", 1700000000000, 1700003600000),
    ]


@pytest.mark.asyncio
async def test_market_klines_rejects_incomplete_time_range(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/market/klines?symbol=ETH-USDT&timeframe=1h&start_time=1700000000000"
        )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "start_time and end_time must be provided together"


@pytest.mark.asyncio
async def test_market_klines_rejects_invalid_time_range(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/market/klines"
            "?symbol=ETH-USDT&timeframe=1h&start_time=1700003600000&end_time=1700000000000"
        )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "end_time must be after start_time"


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
async def test_market_klines_returns_502_when_adapter_fetch_fails(monkeypatch, app):
    events = []

    class FailingOKXSpotAdapter:
        def __init__(self, api_key: str, secret: str, passphrase: str) -> None:
            events.append(("init", api_key, secret, passphrase))

        async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100):
            events.append(("fetch_ohlcv", symbol, timeframe, limit))
            raise RuntimeError("provider unavailable")

        async def close(self) -> None:
            events.append(("close",))

    monkeypatch.setattr(market_api, "OKXSpotAdapter", FailingOKXSpotAdapter, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/market/klines?symbol=ETH-USDT&timeframe=1m&limit=2")

    assert resp.status_code == 502
    assert resp.json()["detail"] == "failed to fetch market data"
    assert events == [
        ("init", "", "", ""),
        ("fetch_ohlcv", "ETH-USDT", "1m", 2),
        ("close",),
    ]


@pytest.mark.asyncio
async def test_market_tickers_returns_502_when_adapter_fetch_fails(monkeypatch, app):
    events = []

    class FailingOKXSpotAdapter:
        def __init__(self, api_key: str, secret: str, passphrase: str) -> None:
            events.append(("init", api_key, secret, passphrase))

        async def fetch_tickers(self, symbols: list[str]):
            events.append(("fetch_tickers", symbols))
            raise RuntimeError("provider unavailable")

        async def close(self) -> None:
            events.append(("close",))

    monkeypatch.setattr(market_api, "OKXSpotAdapter", FailingOKXSpotAdapter, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/market/tickers")

    assert resp.status_code == 502
    assert resp.json()["detail"] == "failed to fetch market data"
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
