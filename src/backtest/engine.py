import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.backtest.matcher import OrderMatcher
from src.backtest.report import BacktestReport, generate_report
from src.core.types import Bar, Order, OrderSide, OrderStatus, OrderType


class BacktestOrderManager:
    def __init__(self) -> None:
        self._next_id = 0
        self._submitted: list[Order] = []

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
        self._next_id += 1
        order = Order(
            id=f"{strategy_name}-{self._next_id}",
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        self._submitted.append(order)
        return order

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        return True

    def take_submitted(self) -> list[Order]:
        submitted = self._submitted
        self._submitted = []
        return submitted


@dataclass
class _Portfolio:
    cash: float
    quantity: float = 0.0
    average_price: float = 0.0
    open_fees: float = 0.0
    round_trip_pnls: list[float] = field(default_factory=list)

    def apply_fill(self, side: OrderSide, amount: float, price: float, fee: float) -> float:
        signed_amount = amount if side == OrderSide.BUY else -amount
        realized = 0.0
        closed_quantity = 0.0

        if self.quantity == 0:
            self.average_price = price
            self.open_fees = fee
        elif (self.quantity > 0) == (signed_amount > 0):
            total = abs(self.quantity) + abs(signed_amount)
            self.average_price = (
                abs(self.quantity) * self.average_price + abs(signed_amount) * price
            ) / total
            self.open_fees += fee
        else:
            closed_quantity = min(abs(self.quantity), abs(signed_amount))
            direction = 1 if self.quantity > 0 else -1
            gross = (price - self.average_price) * closed_quantity * direction
            entry_fee_share = self.open_fees * (closed_quantity / abs(self.quantity))
            exit_fee_share = fee * (closed_quantity / abs(signed_amount))
            realized = gross - entry_fee_share - exit_fee_share
            self.open_fees -= entry_fee_share
            if abs(signed_amount) > closed_quantity:
                remaining = abs(signed_amount) - closed_quantity
                self.average_price = price
                self.open_fees += fee * (remaining / abs(signed_amount))

        self.quantity += signed_amount
        self.cash -= signed_amount * price
        self.cash -= fee
        if closed_quantity:
            self.round_trip_pnls.append(realized)
        return realized

    def equity(self, mark_price: float) -> float:
        return self.cash + self.quantity * mark_price


def _bars_per_year(bars: list[Bar]) -> float | None:
    intervals = sorted(
        current.timestamp - previous.timestamp
        for previous, current in zip(bars, bars[1:], strict=False)
        if current.timestamp > previous.timestamp
    )
    if not intervals:
        return None
    interval = intervals[len(intervals) // 2]
    if interval <= 0:
        return None
    return 365 * 24 * 60 * 60 * 1000 / interval


class BacktestEngine:
    def __init__(self, initial_capital: float, matcher: OrderMatcher) -> None:
        self.initial_capital = initial_capital
        self.matcher = matcher

    async def run(self, strategy: Any, bars: list[Bar]) -> BacktestReport:
        portfolio = _Portfolio(cash=float(self.initial_capital))
        trades: list[dict[str, Any]] = []
        equity_curve = [portfolio.cash]
        pending: list[Order] = []

        order_manager = BacktestOrderManager()
        if hasattr(strategy, "set_order_manager"):
            strategy.set_order_manager(order_manager)

        await strategy.on_init()

        for index, bar in enumerate(bars):
            if index % 1000 == 0:
                await asyncio.sleep(0)
            resting: list[Order] = []
            for order in pending:
                match = self.matcher.match(order, bar)
                order.status = match.status
                if match.status == OrderStatus.FILLED and match.fill_price is not None:
                    order.fill_price = match.fill_price
                    order.fill_time = bar.timestamp
                    realized = portfolio.apply_fill(
                        order.side, order.amount, match.fill_price, match.fee
                    )
                    trades.append(
                        {
                            "symbol": order.symbol,
                            "side": order.side.value,
                            "amount": order.amount,
                            "price": match.fill_price,
                            "pnl": realized,
                            "fee": match.fee,
                            "timestamp": bar.timestamp,
                        }
                    )
                    await strategy.on_order(order)
                else:
                    resting.append(order)
            pending = resting

            equity_curve.append(portfolio.equity(bar.close))

            returned_order = await strategy.on_bar(bar)
            submitted = order_manager.take_submitted()
            if isinstance(returned_order, Order) and all(
                returned_order is not item for item in submitted
            ):
                submitted.append(returned_order)
            pending.extend(submitted)

        return generate_report(
            initial_capital=self.initial_capital,
            trades=trades,
            equity_curve=equity_curve,
            round_trip_pnls=portfolio.round_trip_pnls,
            bars_per_year=_bars_per_year(bars),
        )
