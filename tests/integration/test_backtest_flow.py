import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.matcher import OrderMatcher
from src.core.engine import BotEngine
from src.core.types import Bar, Order, OrderSide, OrderType
from src.strategy.builtin.ma_cross import MACrossStrategy


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
    assert report.total_trades == 2
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
