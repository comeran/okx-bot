import subprocess
import sys

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.core.types import Bar
from src.strategy.base import BaseStrategy
from src.strategy.registry import StrategyRegistry
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
        positions = []

        def save_order(self, order):
            self.orders.append(order)
            return order

        def save_trade(self, trade):
            self.trades.append(trade)
            return trade

        def save_position(self, position):
            self.positions.append(position)
            return position

    class BuyingStrategy(BaseStrategy):
        name = "buyer"

        async def on_init(self):
            await self.buy("BTC-USDT", 0.1, price=50000.0)

        async def on_bar(self, bar):
            pass

    registry = StrategyRegistry()
    registry.register("buyer", BuyingStrategy)
    monkeypatch.setattr(strategy_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(strategy_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(strategy_api, "current_timestamp_ms", lambda: 1700000000000, raising=False)

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
            "fee": 0.0,
            "timestamp": 1700000000000,
        }
    ]
    assert [position.model_dump() for position in FakeRepository.positions] == [
        {
            "id": None,
            "strategy": "buyer",
            "symbol": "BTC-USDT",
            "side": "long",
            "amount": 0.1,
            "entry_price": 50000.0,
            "leverage": 1,
            "timestamp": 1700000000000,
        }
    ]


@pytest.mark.asyncio
async def test_start_unknown_strategy_returns_404(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/strategies/unknown/start")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_run_backtest(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/backtest/run",
            json={
                "strategy": "ma_cross",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "start_time": 1700000000000,
                "end_time": 1700100000000,
                "initial_capital": 100000,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_return"] != 0
    assert isinstance(data["sharpe_ratio"], float)
    assert "max_drawdown" in data
    assert "win_rate" in data
    assert data["total_trades"] > 0


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
async def test_get_backtest_results(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/backtest/run",
            json={
                "strategy": "ma_cross",
                "symbol": "ETH-USDT",
                "timeframe": "4h",
                "start_time": 1700000000000,
                "end_time": 1700100000000,
                "initial_capital": 50000,
            },
        )
        resp = await client.get("/api/backtest/results")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    expected = {"strategy": "ma_cross", "symbol": "ETH-USDT", "timeframe": "4h"}
    assert expected.items() <= data[-1].items()


def test_websocket_accepts_connection_and_sends_snapshot(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json() == {"type": "connected"}


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
async def test_get_account_derives_summary_from_persisted_trading_state(monkeypatch, app):
    class Position:
        amount = 0.2
        entry_price = 65000.0

    class Trade:
        side = "sell"
        amount = 0.1
        price = 68000.0
        fee = 1.5
        timestamp = 1700000000000

    class FakeRepository:
        def get_positions(self, strategy=None):
            return [Position()]

        def get_trades(self, strategy=None):
            return [Trade()]

    monkeypatch.setattr(trading_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(trading_api, "current_timestamp_ms", lambda: 1700001000000, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trading/account")

    assert resp.status_code == 200
    assert resp.json() == {
        "equity": 13000.0,
        "daily_pnl": 6798.5,
    }


@pytest.mark.asyncio
async def test_get_positions_forwards_strategy_filter(monkeypatch, app):
    calls = []

    class FakeRepository:
        def get_positions(self, strategy=None):
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
