import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.backtest.engine import BacktestEngine
from src.backtest.matcher import OrderMatcher
from src.core.engine import BotEngine
from src.core.types import Bar, Order, OrderSide, OrderType
from src.strategy.base import BaseStrategy
from src.strategy.builtin.ma_cross import MACrossStrategy
from src.strategy.registry import StrategyRegistry
from src.web.api import backtest as backtest_api


class BacktestOrderManager:
    def __init__(self) -> None:
        self.count = 0

    async def submit(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: float,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        strategy_name: str = "",
    ) -> Order:
        self.count += 1
        return Order(
            id=f"{strategy_name}-{self.count}",
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )


class LifecycleStrategy:
    name = "lifecycle"

    def __init__(self) -> None:
        self.initialized = False
        self.stopped = False

    async def on_init(self) -> None:
        self.initialized = True

    async def on_shutdown(self) -> None:
        self.stopped = True


def make_bar(close: float, index: int) -> Bar:
    return Bar(
        timestamp=1000 + index * 60_000,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=100,
    )


@pytest.mark.asyncio
async def test_full_backtest_flow_matches_ma_cross_orders() -> None:
    strategy = MACrossStrategy(symbol="BTC-USDT", fast_window=2, slow_window=3, amount=0.5)
    engine = BacktestEngine(
        initial_capital=100000,
        matcher=OrderMatcher(slippage=0.001, fee_rate=0.0005),
    )
    bars = [make_bar(close, index) for index, close in enumerate([10, 10, 10, 13, 14, 5, 4])]

    report = await engine.run(strategy, bars)

    assert report.initial_capital == 100000
    assert report.total_trades == 1
    assert report.final_equity > 0
    assert len(report.equity_curve) == len(bars) + 1


@pytest.mark.asyncio
async def test_bot_engine_starts_and_stops_strategies() -> None:
    strategy = LifecycleStrategy()
    engine = BotEngine(strategies=[strategy])

    await engine.start()
    await engine.stop()

    assert strategy.initialized is True
    assert strategy.stopped is True
    assert engine.running is False


@pytest.mark.parametrize(
    ("symbol", "market_type"),
    [
        ("BTC-USDT-SWAP", "spot"),
        ("BTC/USDT:USDT", "spot"),
        ("BTC-USDT-261225", "spot"),
        ("BTC-USD-261225-50000-C", "spot"),
        ("BTC-USDT", "swap"),
    ],
)
async def test_backtest_api_rejects_derivatives_before_fetching_data(
    monkeypatch, symbol, market_type,
):
    fetched = []

    async def fetch(**kwargs):
        fetched.append(kwargs)
        return [make_bar(10, 0), make_bar(10, 1)]

    monkeypatch.setattr(backtest_api, "ensure_historical_bars", fetch)
    app = FastAPI()
    app.include_router(backtest_api.router, prefix="/api/backtest")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/backtest/run", json={
            "strategy": "ma_cross", "symbol": symbol, "market_type": market_type,
            "timeframe": "1h", "start_time": 0, "end_time": 3600000,
            "initial_capital": 100,
        })

    assert response.status_code == 422
    assert fetched == []


async def test_backtest_api_request_context_drives_queries_and_rejects_other_symbol(monkeypatch):
    class ContextStrategy(BaseStrategy):
        def __init__(self):
            super().__init__()
            self.symbol = "ETH-USDT"
            self.timeframe = "1m"
            self.context_seen = None

        async def on_bar(self, bar):
            self.context_seen = (self.symbol, self.timeframe)
            if bar.timestamp == 1000:
                await self.buy(self.symbol, 1)
                await self.buy("ETH-USDT", 1)

    strategy = ContextStrategy()
    registry = StrategyRegistry()
    registry.register("context", lambda: strategy)

    async def fetch(**kwargs):
        assert strategy.symbol == "BTC-USDT"
        assert strategy.timeframe == "1h"
        return [make_bar(10, 0), make_bar(10, 1)]

    class ResultRepository:
        def save_backtest_result_with_trades(self, result, trades):
            self.result = result
            self.trades = trades

    repository = ResultRepository()
    monkeypatch.setattr(backtest_api, "Repository", lambda: repository)
    monkeypatch.setattr(backtest_api, "create_strategy_registry", lambda: registry)
    monkeypatch.setattr(backtest_api, "ensure_historical_bars", fetch)
    app = FastAPI()
    app.include_router(backtest_api.router, prefix="/api/backtest")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/backtest/run", json={
            "strategy": "context", "symbol": "BTC-USDT", "timeframe": "1h",
            "start_time": 1000, "end_time": 61000, "initial_capital": 100,
        })

    assert response.status_code == 200, response.text
    assert strategy.context_seen == ("BTC-USDT", "1h")
    assert strategy.get_position("BTC-USDT").amount == 1
    assert strategy.get_position("ETH-USDT") is None
    assert len(repository.trades) == 1
    assert repository.trades[0].symbol == "BTC-USDT"


async def test_backtest_api_rejects_nonfinite_capital_before_data_fetch(monkeypatch):
    fetched = []

    async def fetch(**kwargs):
        fetched.append(kwargs)
        return [make_bar(10, 0), make_bar(10, 1)]

    monkeypatch.setattr(backtest_api, "ensure_historical_bars", fetch)
    app = FastAPI()
    app.include_router(backtest_api.router, prefix="/api/backtest")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/backtest/run", json={
            "strategy": "ma_cross", "symbol": "BTC-USDT", "timeframe": "1h",
            "start_time": 0, "end_time": 3600000, "initial_capital": "NaN",
        })

    assert response.status_code == 422
    assert fetched == []
