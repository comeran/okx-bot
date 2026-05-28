from src.backtest.engine import BacktestEngine
from src.backtest.matcher import OrderMatcher
from src.backtest.report import BacktestReport
from src.core.types import Bar, Order, OrderSide, OrderType


class SimpleStrategy:
    name = "simple"

    def __init__(self) -> None:
        self.bars = []

    async def on_init(self) -> None:
        pass

    async def on_bar(self, bar: Bar) -> Order:
        self.bars.append(bar)
        return Order(
            id=f"order-{bar.timestamp}",
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=1,
        )

    async def on_order(self, order: Order) -> None:
        pass


async def test_backtest_engine_runs() -> None:
    bars = [
        Bar(timestamp=index, open=100, high=101, low=99, close=100, volume=10)
        for index in range(10)
    ]
    engine = BacktestEngine(
        initial_capital=100000,
        matcher=OrderMatcher(slippage=0, fee_rate=0.001),
    )

    report = await engine.run(SimpleStrategy(), bars)

    assert isinstance(report, BacktestReport)
    assert report.total_trades == 10
    assert report.final_equity < 100000
