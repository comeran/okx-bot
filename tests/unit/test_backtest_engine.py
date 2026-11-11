from typing import Any

import pytest

from src.backtest.engine import BacktestEngine, BacktestOrderManager
from src.backtest.matcher import OrderMatcher
from src.backtest.report import BacktestReport
from src.core.types import Bar, Order, OrderSide, OrderStatus, OrderType
from src.strategy.base import BaseStrategy


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
    class FundedSellStrategy(BaseStrategy):
        name = "funded_sell"

        async def on_bar(self, bar):
            if bar.timestamp == 1700000000000:
                return await self.buy("BTC-USDT", 2)
            if bar.timestamp == 1700000060000:
                return await self.sell("BTC-USDT", 2)

    bars = [
        Bar(timestamp=1700000000000, open=100, high=101, low=99, close=100, volume=10),
        Bar(timestamp=1700000060000, open=100, high=101, low=99, close=100, volume=10),
        Bar(timestamp=1700000120000, open=100, high=101, low=99, close=100, volume=10),
    ]
    engine = BacktestEngine(
        initial_capital=100000,
        matcher=OrderMatcher(slippage=0, fee_rate=0.001),
    )

    report = await engine.run(FundedSellStrategy(), bars)

    assert report.trades[1:] == [
        {
            "symbol": "BTC-USDT",
            "side": "sell",
            "amount": 2,
            "price": 100,
            "pnl": -0.4,
            "fee": 0.2,
            "timestamp": 1700000120000,
        }
    ]


async def test_backtest_cancel_unknown_order_returns_false() -> None:
    manager = BacktestOrderManager()

    assert await manager.cancel("missing", "BTC-USDT") is False


async def test_cancel_submitted_order_prevents_fill_even_when_returned_by_strategy() -> None:
    class CancelImmediately(BaseStrategy):
        name = "cancel_immediately"

        async def on_bar(self, bar):
            if bar.timestamp == 0:
                self.order = await self.buy("BTC-USDT", 1)
                self.cancelled = await self.cancel(self.order.id)
                self.cancelled_again = await self.cancel(self.order.id)
                return self.order

    strategy = CancelImmediately()
    report = await BacktestEngine(1000, OrderMatcher()).run(
        strategy, [flat_bar(0, 100), flat_bar(1, 100)]
    )

    assert strategy.cancelled is True
    assert strategy.cancelled_again is False
    assert strategy.order.status == OrderStatus.CANCELLED
    assert report.trades == []


async def test_cancel_resting_order_checks_symbol_and_prevents_later_fill() -> None:
    class CancelResting(BaseStrategy):
        name = "cancel_resting"

        async def on_bar(self, bar):
            if bar.timestamp == 0:
                self.order = await self.buy("BTC-USDT", 1, price=90)
            elif bar.timestamp == 1:
                self.wrong_symbol = await self.cancel(self.order.id, "ETH-USDT")
                self.status_after_wrong_symbol = self.order.status
                self.cancelled = await self.cancel(self.order.id, "BTC-USDT")

    strategy = CancelResting()
    report = await BacktestEngine(1000, OrderMatcher()).run(
        strategy, [flat_bar(0, 100), flat_bar(1, 100), flat_bar(2, 90)]
    )

    assert strategy.wrong_symbol is False
    assert strategy.status_after_wrong_symbol == OrderStatus.PENDING
    assert strategy.cancelled is True
    assert strategy.order.status == OrderStatus.CANCELLED
    assert report.trades == []


async def test_backtest_cannot_cancel_filled_order_from_fill_callback() -> None:
    class CancelFilled(BaseStrategy):
        name = "cancel_filled"

        async def on_bar(self, bar):
            if bar.timestamp == 0:
                return await self.buy("BTC-USDT", 1)

        async def on_order(self, order):
            self.order = order
            self.cancelled = await self.cancel(order.id)

    strategy = CancelFilled()
    report = await BacktestEngine(1000, OrderMatcher()).run(
        strategy, [flat_bar(0, 100), flat_bar(1, 100)]
    )

    assert strategy.cancelled is False
    assert strategy.order.status == OrderStatus.FILLED
    assert len(report.trades) == 1


@pytest.mark.parametrize("cancel_earlier_resting_order", [False, True])
async def test_fill_callback_cancellation_does_not_fill_or_requeue_other_order(
    cancel_earlier_resting_order,
) -> None:
    class CancelOther(BaseStrategy):
        name = "cancel_other"

        async def on_bar(self, bar):
            if bar.timestamp == 0:
                if cancel_earlier_resting_order:
                    self.other_order = await self.buy("BTC-USDT", 1, price=90)
                    self.filling_order = await self.buy("BTC-USDT", 1)
                else:
                    self.filling_order = await self.buy("BTC-USDT", 1)
                    self.other_order = await self.buy("BTC-USDT", 1)

        async def on_order(self, order):
            if order is self.filling_order:
                self.cancelled = await self.cancel(self.other_order.id)

    strategy = CancelOther()
    report = await BacktestEngine(1000, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [flat_bar(0, 100), flat_bar(1, 100), flat_bar(2, 90)]
    )

    assert strategy.cancelled is True
    assert strategy.other_order.status == OrderStatus.CANCELLED
    assert len(report.trades) == 1
    assert report.trades[0]["price"] == 100


async def test_returned_legacy_order_can_be_cancelled_while_resting() -> None:
    class CancelReturned(BaseStrategy):
        name = "cancel_returned"

        async def on_bar(self, bar):
            if bar.timestamp == 0:
                self.order = Order(
                    id="legacy", symbol="BTC-USDT", side=OrderSide.BUY,
                    type=OrderType.LIMIT, amount=1, price=90,
                )
                return self.order
            if bar.timestamp == 1:
                self.cancelled = await self.cancel(self.order.id)

    strategy = CancelReturned()
    report = await BacktestEngine(1000, OrderMatcher()).run(
        strategy, [flat_bar(0, 100), flat_bar(1, 100), flat_bar(2, 90)]
    )

    assert strategy.cancelled is True
    assert strategy.order.status == OrderStatus.CANCELLED
    assert report.trades == []
