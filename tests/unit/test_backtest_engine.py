from typing import Any

import pytest

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


class ScheduledStrategy:
    name = "scheduled"

    def __init__(self, schedule: dict[int, OrderSide]) -> None:
        self.schedule = schedule
        self.fills: list[Order] = []

    async def on_init(self) -> None:
        pass

    async def on_bar(self, bar: Bar) -> Order | None:
        side = self.schedule.get(bar.timestamp)
        if side is None:
            return None
        return Order(
            id=f"order-{bar.timestamp}",
            symbol="BTC-USDT",
            side=side,
            type=OrderType.MARKET,
            amount=1,
        )

    async def on_order(self, order: Order) -> Any:
        self.fills.append(order)


def flat_bar(timestamp: int, price: float) -> Bar:
    return Bar(
        timestamp=timestamp,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=10,
    )


async def test_orders_fill_on_the_next_bar_open() -> None:
    bars = [flat_bar(index, 100) for index in range(10)]
    engine = BacktestEngine(
        initial_capital=100000,
        matcher=OrderMatcher(slippage=0, fee_rate=0.001),
    )

    report = await engine.run(SimpleStrategy(), bars)

    assert isinstance(report, BacktestReport)
    assert len(report.trades) == 9
    assert report.total_trades == 0
    assert report.trades[0] == {
        "symbol": "BTC-USDT",
        "side": "buy",
        "amount": 1,
        "price": 100,
        "pnl": 0.0,
        "fee": 0.1,
        "timestamp": 1,
    }
    assert report.final_equity == pytest.approx(100000 - 9 * 0.1)


async def test_single_bar_has_no_fill() -> None:
    engine = BacktestEngine(
        initial_capital=100000,
        matcher=OrderMatcher(slippage=0, fee_rate=0.001),
    )

    report = await engine.run(SellStrategy(), [flat_bar(0, 100)])

    assert report.trades == []
    assert report.total_trades == 0
    assert report.final_equity == 100000


async def test_open_positions_are_marked_to_market() -> None:
    bars = [
        Bar(timestamp=0, open=100, high=101, low=99, close=100, volume=10),
        Bar(timestamp=1, open=105, high=111, low=104, close=110, volume=10),
    ]
    strategy = ScheduledStrategy({0: OrderSide.BUY})
    engine = BacktestEngine(
        initial_capital=100000,
        matcher=OrderMatcher(slippage=0, fee_rate=0),
    )

    report = await engine.run(strategy, bars)

    assert len(report.trades) == 1
    assert report.trades[0]["timestamp"] == 1
    assert report.equity_curve == [100000, 100000, 100005]
    assert report.final_equity == 100005
    assert report.total_return == 0.00005


async def test_round_trip_metrics_use_position_pnl() -> None:
    bars = [
        Bar(timestamp=0, open=100, high=101, low=99, close=100, volume=10),
        Bar(timestamp=1, open=100, high=111, low=99, close=110, volume=10),
        Bar(timestamp=2, open=110, high=111, low=109, close=110, volume=10),
        Bar(timestamp=3, open=110, high=111, low=109, close=110, volume=10),
    ]
    strategy = ScheduledStrategy({0: OrderSide.BUY, 2: OrderSide.SELL})
    engine = BacktestEngine(
        initial_capital=100000,
        matcher=OrderMatcher(slippage=0, fee_rate=0),
    )

    report = await engine.run(strategy, bars)

    assert report.total_trades == 1
    assert report.win_rate == 1
    assert report.profit_factor == 0
    assert report.final_equity == 100010
    assert report.trades[0]["pnl"] == 0
    assert report.trades[1]["pnl"] == 10


async def test_sell_trade_marker_fields() -> None:
    bars = [
        Bar(timestamp=1700000000000, open=100, high=101, low=99, close=100, volume=10),
        Bar(timestamp=1700000060000, open=100, high=101, low=99, close=100, volume=10),
    ]
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
            "pnl": 0.0,
            "fee": 0.2,
            "timestamp": 1700000060000,
        }
    ]
