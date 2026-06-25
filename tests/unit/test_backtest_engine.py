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


class SellStrategy:
    name = "sell"

    async def on_init(self) -> None:
        pass

    async def on_bar(self, bar: Bar) -> Order:
        return Order(
            id=f"order-{bar.timestamp}",
            symbol="BTC-USDT",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            amount=2,
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
    assert report.trades[0] == {
        "symbol": "BTC-USDT",
        "side": "buy",
        "amount": 1,
        "price": 100,
        "pnl": -100.1,
        "fee": 0.1,
        "timestamp": 0,
    }


async def test_backtest_engine_records_sell_trade_marker_fields() -> None:
    bars = [Bar(timestamp=1700000000000, open=100, high=101, low=99, close=100, volume=10)]
    engine = BacktestEngine(
        initial_capital=100000,
        matcher=OrderMatcher(slippage=0, fee_rate=0.001),
    )

    report = await engine.run(SellStrategy(), bars)

    assert report.trades == [
        {
            "symbol": "BTC-USDT",
            "side": "sell",
            "amount": 2,
            "price": 100,
            "pnl": 199.8,
            "fee": 0.2,
            "timestamp": 1700000000000,
        }
    ]
