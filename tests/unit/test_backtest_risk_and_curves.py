import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.matcher import OrderMatcher
from src.backtest.report import drawdown_series
from src.core.types import Bar, OrderStatus
from src.risk.manager import RiskManager
from src.strategy.base import BaseStrategy


def candle(timestamp: int, opening: float = 10, close: float | None = None) -> Bar:
    close_price = opening if close is None else close
    return Bar(
        timestamp,
        opening,
        max(opening, close_price) + 1,
        max(0, min(opening, close_price) - 1),
        close_price,
        100,
    )


class BuyOnce(BaseStrategy):
    name = "risk_buy"
    symbol = "BTC-USDT"

    def __init__(self) -> None:
        super().__init__()
        self.updates: list[tuple[str, OrderStatus]] = []

    async def on_bar(self, bar: Bar):
        if bar.timestamp != 0:
            return None
        return await self.buy(self.symbol, 2)

    async def on_order(self, order):
        self.updates.append((order.id, order.status))


def test_drawdown_series_tracks_peak_to_trough() -> None:
    assert drawdown_series([100, 80, 90]) == pytest.approx([0, 0.2, 0.1])


async def test_backtest_records_risk_rejection_before_the_order_can_fill() -> None:
    strategy = BuyOnce()
    risk = RiskManager(
        max_position_pct=0.8,
        enforce_daily_loss=True,
        enforce_drawdown=True,
    )

    report = await BacktestEngine(
        20,
        OrderMatcher(slippage=0, fee_rate=0),
        risk_manager=risk,
    ).run(strategy, [candle(0), candle(1)])

    assert report.trades == []
    assert strategy.updates[0][1] == OrderStatus.REJECTED
    assert report.rejected_orders == [
        {
            "symbol": "BTC-USDT",
            "side": "buy",
            "timestamp": 0,
            "amount": 2,
            "price": None,
            "reason": "Order exceeds maximum position size",
        }
    ]
    assert len(report.equity_timestamps) == len(report.equity_curve)
    assert report.equity_timestamps[0] < report.equity_timestamps[1]


async def test_backtest_still_fills_an_affordable_order_inside_the_position_limit() -> None:
    strategy = BuyOnce()
    risk = RiskManager(max_position_pct=0.8, enforce_daily_loss=True, enforce_drawdown=True)

    report = await BacktestEngine(
        100,
        OrderMatcher(slippage=0, fee_rate=0),
        risk_manager=risk,
    ).run(strategy, [candle(0), candle(1, opening=10)])

    assert len(report.trades) == 1
    assert report.rejected_orders == []


async def test_backtest_buy_cannot_exceed_the_allocation_budget() -> None:
    strategy = BuyOnce()

    report = await BacktestEngine(
        100,
        OrderMatcher(slippage=0, fee_rate=0),
        allocation_budget=15,
    ).run(strategy, [candle(0), candle(1)])

    assert report.trades == []
    assert report.rejected_orders[0]["reason"] == "Allocation budget exceeded"
