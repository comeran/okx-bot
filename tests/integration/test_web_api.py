import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.strategy.base import BaseStrategy
from src.strategy.registry import StrategyRegistry
from src.web.api import strategies as strategy_api
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
async def test_get_market_data(app):
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
